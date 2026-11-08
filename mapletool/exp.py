"""EXP tracker: find the status bar, read "EXP. 134,202 [50.32%]", and derive EXP/hour and time to level.

Everything geometric is in base pixels (like the shop layout). The bar's "LV." box is the anchor
(assets/statusbar_anchor.png); the text box and placement offsets are relative to its top-left
(layout.json "statusbar").
"""
from __future__ import annotations

import json
import logging
import re
import statistics
from collections import deque
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

from mapletool.rate import IDLE_S, MIN_INCREASES, MIN_RATE_S, WINDOW_S, GainRate  # noqa: F401  (re-exported)

from mapletool import draw
from mapletool.detect import ShopLocator

log = logging.getLogger("mapletool")

READ_INTERVAL = 1.0  # seconds between EXP reads while On
SEARCH_BAND = 120  # the bar is anchored to the bottom edge: search only the bottom rows of the frame
MIN_SCORE = 0.9  # anchor NCC (measured 0.9998-1.0 on every fixture, next best 0.61)
INK_MIN = 130  # min(B, G, R) above this is text ink: drops the green brackets (measured stable 120-140)

MAX_T_DEV = 0.02  # an implied level total must agree with the running median within 2%
MIN_T_PCT = 1.0  # below 1% the 2-decimal percentage is too coarse for a total estimate
PCT_TOL = 0.01  # the shown percent is rounded or truncated to 2 decimals
LEVEL_DROP = 50.0  # the percentage falling by more than this is a level-up
T_KEEP = 15  # recent total estimates kept per level
RESEED_AFTER = 3  # this many consecutive mutually consistent rejected samples replace the baseline

# --- parsing -----------------------------------------------------------------------------

# OCR confusions inside a token that already holds digits (the pixel font's 5/0 next to the dropped
# bracket read as "so", a 1 as "l", ...). Letters outside numeric tokens are never mapped.
_CONFUSABLE = str.maketrans({"s": "5", "S": "5", "o": "0", "O": "0", "D": "0", "l": "1", "I": "1",
                             "i": "1", "|": "1", "z": "2", "Z": "2", "B": "8"})
_EXP_RE = re.compile(r"^(\d{1,3}(?:[,.]\d{3})*|\d{1,7}) (\d{1,2}(?:\.\d{1,2})?)%$")  # ungrouped: <= 7 digits


def parse_exp(text: str):
    """(exp, percent) from the status bar's EXP text ("134,202 [50.32%]"), or None.

    Tolerates OCR spacing, dropped or misread brackets, a thousands comma read as a dot, and
    s/o/l-style confusions inside numeric tokens; anything else (extra tokens, a percent >= 100,
    3 decimals, bad digit grouping) is rejected."""
    if not text:
        return None
    t = re.sub(r"[\[\]\(\)\{\}]", " ", text)
    t = re.sub(r"\s*%", "%", t)
    tokens = t.split()
    tokens = [tok.translate(_CONFUSABLE) if any(c.isdigit() for c in tok) else tok for tok in tokens]
    m = _EXP_RE.match(" ".join(tokens))
    if m is None:
        return None
    return int(re.sub(r"[,.]", "", m.group(1))), float(m.group(2))


# --- formatting ----------------------------------------------------------------------------

def format_amount(n: float) -> str:
    """999, 1.0K, 12.3K, 312K, 1.2M, 2.1B (one decimal for M and B)."""
    n = int(round(n))
    if n < 1000:
        return str(n)
    if n < 99_950:
        return f"{n / 1e3:.1f}K"
    if n < 999_500:
        return f"{n / 1e3:.0f}K"
    if n < 999_950_000:
        return f"{n / 1e6:.1f}M"
    return f"{n / 1e9:.1f}B"


def format_duration(seconds) -> str:
    """'12m 05s' under an hour, '1h 23m' above, '99h+' from 100 h, '—' when unknown."""
    if seconds is None:
        return "—"
    s = int(seconds)
    if s >= 100 * 3600:
        return "99h+"
    if s >= 3600:
        return f"{s // 3600}h {s % 3600 // 60:02d}m"
    return f"{s // 60}m {s % 60:02d}s"


# --- geometry ----------------------------------------------------------------------------

@dataclass(frozen=True)
class StatusBarLayout:
    exp_text_box: tuple  # (x, y, w, h) of "134,202 [50.32%]" relative to the anchor
    level_box: tuple  # the level number tiles
    panel: tuple  # (x, y, w, h) of the LV box and the job / character-name panel: the EXP box covers both
    bar_top: int  # y of the bar's top edge (light line)


def load_statusbar(path: Path) -> StatusBarLayout:
    d = json.loads(Path(path).read_text(encoding="utf-8"))["statusbar"]
    return StatusBarLayout(tuple(int(v) for v in d["exp_text_box"]), tuple(int(v) for v in d["level_box"]),
                           tuple(int(v) for v in d["panel"]), int(d["bar_top"]))


class StatusBarLocator:
    """Top-left of the status bar's "LV." box in a base-resolution gray frame, or None.

    Like ShopLocator (cached position re-verified cheaply, full search on a miss), but searched
    only in the bottom SEARCH_BAND rows."""

    def __init__(self, anchor: np.ndarray, min_score: float = MIN_SCORE, band: int = SEARCH_BAND):
        self.anchor = anchor
        self.band = band
        self._loc = ShopLocator(anchor, min_score)

    def reset(self) -> None:
        self._loc.reset()

    def locate(self, gray: np.ndarray):
        y0 = max(0, gray.shape[0] - self.band)
        pos = self._loc.locate(gray[y0:])
        return None if pos is None else (pos[0], pos[1] + y0)


@dataclass(frozen=True)
class ExpReading:
    anchor: tuple
    exp: int | None = None
    pct: float | None = None
    source: str = ""  # "glyphs" | "ocr" | "" (unreadable)


def exp_ink(bgr: np.ndarray) -> np.ndarray:
    """Dark-on-white binary of the EXP text: white/grey digits kept, the green brackets dropped."""
    mn = bgr.min(axis=2) if bgr.ndim == 3 else bgr
    return np.where(mn > INK_MIN, 0, 255).astype(np.uint8)


# The status bar's EXP digits are a fixed 7-px pixel font (identical at every base; only the client's
# upscale blurs it). Cut from the fixtures at 3840x2160 (5 and "," at 2560x1600, where the strokes are
# a pixel thicker); no fixture shows an 8, its pattern follows the font's 0/3/9. Windows OCR alone
# confuses this font's 5 and 9 with "s"/"S" and drops leading digits of long numbers (measured 57/118
# synthetic strings), so it is only the fallback for crops this reader is not sure about.
EXP_GLYPHS = {
    "%": (".#....#", "###..#.", ".#..#..", "...#...", "..#..#.", ".#..###", "#....#."),
    ",": ("..", "..", "..", "..", "..", ".#", "#."),
    ".": (".", ".", ".", ".", ".", ".", "#"),
    "0": (".###.", "#...#", "#...#", "#...#", "#...#", "#...#", ".###."),
    "1": (".#", "##", ".#", ".#", ".#", ".#", ".#"),
    "2": (".###.", "#...#", "....#", "...#.", "..#..", ".#...", "#####"),
    "3": (".###.", "#...#", "....#", "..##.", "....#", "#...#", ".###."),
    "4": ("...#.", "..##.", ".#.#.", "#..#.", "#####", "...#.", "...#."),
    "5": ("#####", "#....", "#....", ".###.", "....#", "#...#", ".###."),
    "6": (".###.", "#...#", "#....", "####.", "#...#", "#...#", ".###."),
    "7": ("#####", "....#", "....#", "....#", "...#.", "..#..", "..#.."),
    "8": (".###.", "#...#", "#...#", ".###.", "#...#", "#...#", ".###."),
    "9": (".###.", "#...#", "#...#", ".####", "....#", "#...#", ".###."),
}
GLYPH_TOP = 3  # first glyph row inside exp_text_box
EIGHT_SAMPLE = "exp_glyph_8.png"  # the first real crop with an "8" (its template is hand-drawn), for checking
GLYPH_MIN_SCORE = 0.75  # correlation a glyph needs (real fixture glyphs >= 0.879; an 8 with its middle
# bar a row off scores 0.685-0.701 as "0") ...
GLYPH_MIN_MARGIN = 0.03  # ... and its lead over the next best (measured >= 0.046 on every fixture)
GLYPH_WIDTH_PENALTY = 0.15  # per column the template is wider/narrower than the ink run
SPACE_GAP = 5  # blank columns between the EXP number and the percent (the dropped bracket); digits <= 3


def _norm(a: np.ndarray) -> np.ndarray | None:
    a = a - a.mean()
    n = float(np.sqrt((a * a).sum()))
    return a / n if n else None


def _template_groups() -> dict:
    """{width: (chars, matrix of zero-mean unit-norm flattened templates)}; every glyph is 7 rows."""
    groups: dict = {}
    for c, rows in EXP_GLYPHS.items():
        t = _norm(np.array([[1.0 if ch == "#" else 0.0 for ch in row] for row in rows], np.float32))
        groups.setdefault(len(rows[0]), []).append((c, t.ravel()))
    return {w: ([c for c, _ in v], np.stack([t for _, t in v])) for w, v in groups.items()}


_GROUPS = _template_groups()
GLYPH_H = 7


def _ink_runs(mask: np.ndarray) -> list:
    cols = mask.any(axis=0)
    runs, x = [], 0
    while x < len(cols):
        if cols[x]:
            s = x
            while x < len(cols) and cols[x]:
                x += 1
            runs.append((s, x))
        else:
            x += 1
    return runs


def _classify(padded: np.ndarray, s: int, e: int):
    """(char, score, runner-up score) for the ink run [s, e) of `padded` (min-channel, one background
    column added on each side): best correlation over templates within one column of the run's width,
    shifted up to one pixel each way."""
    rw = e - s
    scores = []
    for w in (rw - 1, rw, rw + 1):
        if w not in _GROUPS:
            continue
        chars, mat = _GROUPS[w]
        wins = []
        for dy in (GLYPH_TOP - 1, GLYPH_TOP, GLYPH_TOP + 1):
            for x0 in range(s, s + 3):  # template left edge at run start - 1 .. + 1 (padded coordinates)
                win = padded[dy:dy + GLYPH_H, x0:x0 + w]
                if win.shape == (GLYPH_H, w):
                    n = _norm(win)
                    if n is not None:
                        wins.append(n.ravel())
        if wins:
            best = (np.stack(wins) @ mat.T).max(axis=0) - GLYPH_WIDTH_PENALTY * abs(w - rw)
            scores += zip(best.tolist(), chars)
    scores.sort(reverse=True)
    scores += [(-1.0, "?"), (-1.0, "?")]
    return scores[0][1], scores[0][0], scores[1][0]


def read_glyphs(bgr: np.ndarray):
    """The EXP text ("134,202 50.32%") read with the status bar's pixel font, "" when there is no ink,
    or None when any glyph is unknown or ambiguous (then OCR decides)."""
    mn = (bgr.min(axis=2) if bgr.ndim == 3 else bgr).astype(np.float32)
    runs = _ink_runs(mn > INK_MIN)
    padded = np.pad(mn, ((0, 0), (1, 1)), mode="edge")
    out = []
    for i, (s, e) in enumerate(runs):
        if i and s - runs[i - 1][1] >= SPACE_GAP:
            out.append(" ")
        c, score, second = _classify(padded, s, e)
        if score < GLYPH_MIN_SCORE or score - second < GLYPH_MIN_MARGIN:
            return None
        out.append(c)
    return "".join(out)


class ExpReader:
    """read(gray, crop) -> ExpReading (anchor, exp/pct or None when unreadable), None without a bar.

    crop(box) returns the BGR pixels of a base-pixel box of the same frame (Grabber.crops); the
    colour is needed to drop the green brackets. The pixel-font reader goes first; Windows OCR (`ocr`,
    may be None) only reads crops it is not sure about."""

    def __init__(self, locator: StatusBarLocator, layout: StatusBarLayout, ocr, sample_dir=None):
        self.locator = locator
        self.layout = layout
        self.ocr = ocr
        self.sample_dir = sample_dir  # where EIGHT_SAMPLE goes (default: the app data dir)
        self._eight_saved = False

    def _save_eight(self, bgr) -> None:
        """Once per run, keep the first crop the glyph reader read an 8 in (never overwrites)."""
        self._eight_saved = True
        try:
            if self.sample_dir is None:
                from mapletool.config import data_dir
                self.sample_dir = data_dir()
            path = Path(self.sample_dir) / EIGHT_SAMPLE
            if not path.exists():
                cv2.imwrite(str(path), bgr)
                log.info("EXP crop with a glyph-read 8 saved to %s (check against the hand-drawn template)", path)
        except Exception:  # noqa: BLE001
            log.warning("could not save %s", EIGHT_SAMPLE, exc_info=True)

    def text_box(self, anchor) -> tuple:
        x, y, w, h = self.layout.exp_text_box
        return (anchor[0] + x, anchor[1] + y, w, h)

    def read(self, gray: np.ndarray, crop):
        anchor = self.locator.locate(gray)
        if anchor is None:
            return None
        bgr = crop(self.text_box(anchor))
        if bgr is None:
            return ExpReading(anchor)
        text = read_glyphs(bgr)
        parsed = parse_exp(text) if text else None
        if parsed is not None:
            if "8" in text and not self._eight_saved:
                self._save_eight(bgr)
            return ExpReading(anchor, parsed[0], parsed[1], "glyphs")
        if self.ocr is None:
            return ExpReading(anchor)
        parsed = parse_exp(self.ocr.read(exp_ink(bgr)))
        if parsed is None:
            return ExpReading(anchor)
        return ExpReading(anchor, parsed[0], parsed[1], "ocr")


# --- tracker -----------------------------------------------------------------------------

def _t_range(exp: int, pct: float) -> tuple:
    """Level totals a sample allows given the 2-decimal percent (lo, hi); hi may be inf."""
    lo = exp * 100.0 / (pct + PCT_TOL)
    hi = exp * 100.0 / (pct - PCT_TOL) if pct > PCT_TOL else float("inf")
    return lo, hi


class _Level:
    """Samples of one level: recent total estimates and the intersection of the allowed totals."""

    def __init__(self):
        self.totals: deque = deque(maxlen=T_KEEP)
        self.lo, self.hi = 0.0, float("inf")

    def median(self):
        return statistics.median(self.totals) if self.totals else None

    def fits(self, exp: int, pct: float) -> bool:
        med = self.median()
        if med is not None and pct >= MIN_T_PCT and abs(exp * 100.0 / pct - med) > MAX_T_DEV * med:
            return False
        lo, hi = _t_range(exp, pct)
        return lo <= self.hi and hi >= self.lo

    def add(self, exp: int, pct: float) -> None:
        if pct >= MIN_T_PCT:
            self.totals.append(exp * 100.0 / pct)
        lo, hi = _t_range(exp, pct)
        self.lo, self.hi = max(self.lo, lo), min(self.hi, hi)

    def total(self, exp: int, pct: float):
        """Best estimate of the level total: median of estimates, else the allowed range, else the sample."""
        med = self.median()
        if med is not None:
            return med
        if self.hi != float("inf"):
            return (self.lo + self.hi) / 2
        return exp * 100.0 / pct if pct > 0 else None


class ExpTracker(GainRate):
    """Validated EXP samples -> session gain, EXP/hour, time to the next level (pure; caller's clock).

    add(exp, pct, now) returns False for a discarded sample (OCR misread): percent outside [0, 100),
    an implied level total that disagrees with the level's running median by more than 2% (or a
    total range that does not overlap the level's), or EXP going down within a level. A percentage
    drop of more than 50 points is a level-up: it is held (add returns False) until the next sample
    that differs from it confirms it (still a level-up from the old level, not lower, total range
    overlapping), then the gain wraps using the old level's total; a sample that does not confirm it
    drops it. Three consecutive rejected samples that agree with each other replace the baseline (a
    misread first sample, or EXP lost on death); their difference is not counted as gain.

    Idle gaps (no EXP change for IDLE_S) do not count as time: on the first gain after one, the
    history is shifted forward by the gap, so the rate window covers active time only.
    """

    def reset(self) -> None:
        super().reset()
        self.exp = None
        self.pct = None
        self.level_ups = 0
        self._level = _Level()
        self._pending: list = []  # consecutive rejected samples (exp, pct, t)
        self._held = None  # a level-up waiting for confirmation (exp, pct, t)

    # -- input ----------------------------------------------------------------------------
    def add(self, exp: int, pct: float, now: float) -> bool:
        if not (0.0 <= pct < 100.0) or exp < 0:
            return False
        if self.exp is None:
            self._start(exp, pct, now)
            return True
        if self._held is not None:
            h_exp, h_pct, h_t = self._held
            if (exp, pct) == (h_exp, h_pct):
                return False  # the same reading again: neither confirms nor drops it
            self._held = None
            if self._is_level_up(exp, pct) and exp >= h_exp and pct >= h_pct and self._agrees(h_exp, h_pct, exp, pct):
                self._commit_level_up(h_exp, h_pct, h_t)
                return self.add(exp, pct, now)
            if self._is_level_up(exp, pct):
                return False  # disagrees with the held sample: both are suspect
        if exp == self.exp and pct == self.pct:
            self._pending.clear()
            return True
        if self._is_level_up(exp, pct):
            self._held = (exp, pct, now)
            return False
        if exp < self.exp or not self._level.fits(exp, pct):
            self._reject(exp, pct, now)
            return False
        self._level.add(exp, pct)
        if exp > self.exp:
            self.gain(exp - self.exp, now)
        self.exp, self.pct = exp, pct
        self._pending.clear()
        return True

    def _is_level_up(self, exp, pct) -> bool:
        return self.pct - pct > LEVEL_DROP and exp < self.exp

    @staticmethod
    def _agrees(e1, p1, e2, p2) -> bool:
        level = _Level()
        level.add(e1, p1)
        return level.fits(e2, p2)

    def _commit_level_up(self, exp, pct, now) -> None:
        old_total = self._level.total(self.exp, self.pct)
        self.gain(max(0.0, (old_total or self.exp) - self.exp) + exp, now)
        self.level_ups += 1
        self._level = _Level()
        self._level.add(exp, pct)
        self.exp, self.pct = exp, pct
        self._pending.clear()

    def _start(self, exp, pct, now) -> None:
        self.exp, self.pct = exp, pct
        self._level.add(exp, pct)
        self.start(now)

    def _reject(self, exp, pct, now) -> None:
        self._pending.append((exp, pct, now))
        del self._pending[:-RESEED_AFTER]
        if len(self._pending) < RESEED_AFTER:
            return
        level = _Level()
        prev = None
        for e, p, _ in self._pending:
            if (prev is not None and e < prev) or not level.fits(e, p):
                return
            level.add(e, p)
            prev = e
        e, p, _ = self._pending[-1]
        self._level = level
        self.exp, self.pct = e, p
        self._pending.clear()

    # -- output ---------------------------------------------------------------------------
    def level_total(self):
        return None if self.exp is None else self._level.total(self.exp, self.pct)

    def time_to_level(self, now: float):
        """Seconds until the next level at the current rate, None when unknown."""
        rate = self.rate(now)
        total = self.level_total()
        if not rate or total is None:
            return None
        return max(0.0, total - self.exp) / rate * 3600.0


# --- display box -------------------------------------------------------------------------

# Sampled from the fixtures' status bar at base resolution (the job / name panel).
BAR_TOP = "#727679"  # panel gradient top (first row under the bar's bevel)
BAR_BOTTOM = "#313a3f"  # panel colour from row 14 down (dark slate)
EXP_LABEL = "#b2b5b7"  # the job / name text grey
EXP_VALUE = "#ffffff"  # values in white
EXP_SUB = "#8cb83a"  # the bar's yellow-green accent (EXP "[ %]" brackets): session gains, idle
EXP_MUTED = "#80868a"  # "open inv"

EXP_GRADIENT_H = 14  # the panel fades from BAR_TOP to BAR_BOTTOM over its top 14 rows, then stays flat
EXP_PAD = 4
EXP_FONT = 9  # three lines (bold)
EXP_LINE_Y = (0.21, 0.52, 0.82)  # line centres as a fraction of the box height
LEVEL_SCALE = 0.75  # the game's own level tiles, copied small into the top-left corner
LEVEL_LABEL = "LV"
LEVEL_GAP = 5  # between the level column and the text column


@dataclass(frozen=True)
class MesoLine:
    """What the third line shows: the inventory's meso box seen (rate, session gain) or not."""
    visible: bool
    rate: float | None = None
    gained: int = 0


@dataclass(frozen=True)
class ExpBox:
    """The EXP box over the LV box and the job / name panel; base pixels from build_exp_box, screen
    pixels after exp_box_to_screen.

    lines: one tuple of (text, colour) segments per line, drawn left to right; tails: per line, segments
    drawn right-aligned (dropped when they would touch the line); level: base64 PNG of the game's level
    tiles at screen size (None: not available, the "LV" label stands alone)."""
    x: int
    y: int
    w: int
    h: int
    lines: tuple
    scale: float = 1.0
    tails: tuple = ((), (), ())
    level: str | None = None
    level_w: int = 0  # the level tiles' width in base pixels (before LEVEL_SCALE)


def exp_lines(tracker: ExpTracker, now: float, meso: MesoLine | None = None) -> tuple:
    """((line1, line2, line3), (tail1, tail2, tail3)): EXP/h with the session gain; time to level with
    the idle mark; mesos/h with the session meso gain, or "open inv" while the inventory is closed."""
    rate = tracker.rate(now)
    line1 = (("EXP/h ", EXP_LABEL), ("—" if rate is None else format_amount(rate), EXP_VALUE))
    line2 = (("Lv up ", EXP_LABEL), (format_duration(tracker.time_to_level(now)), EXP_VALUE))
    tail1 = (("+" + format_amount(tracker.gained), EXP_SUB),) if tracker.gained else ()
    tail2 = (("idle", EXP_SUB),) if tracker.idle(now) else ()
    if meso is None or not meso.visible:
        line3, tail3 = (("Meso/h ", EXP_LABEL), ("open inv", EXP_MUTED)), ()
    else:
        line3 = (("Meso/h ", EXP_LABEL), ("—" if meso.rate is None else format_amount(meso.rate), EXP_VALUE))
        tail3 = (("+" + format_amount(meso.gained), EXP_SUB),) if meso.gained else ()
    return (line1, line2, line3), (tail1, tail2, tail3)


def build_exp_box(anchor, layout: StatusBarLayout, tracker: ExpTracker, now: float,
                  frame_w: int | None = None, meso: MesoLine | None = None) -> ExpBox:
    """Box exactly over the LV box and the job / name panel (the player knows job and name; the level is
    redrawn small in the corner)."""
    lines, tails = exp_lines(tracker, now, meso)
    px, py, pw, ph = layout.panel
    x, y = anchor[0] + px, anchor[1] + py
    if frame_w is not None:
        pw = min(pw, max(0, frame_w - x))
    return ExpBox(x, y, pw, ph, lines, tails=tails, level_w=layout.level_box[2])


def exp_box_to_screen(b: ExpBox, client_xy, scale: float, level_crop=None) -> ExpBox:
    """Screen pixels; `level_crop` (BGR at base resolution, the level tiles) is scaled into the box."""
    from mapletool.sidelist import icon_png_b64
    cx, cy = client_xy
    level = icon_png_b64(level_crop, scale * LEVEL_SCALE) if level_crop is not None else None
    return ExpBox(round(b.x * scale + cx), round(b.y * scale + cy), round(b.w * scale), round(b.h * scale),
                  b.lines, b.scale * scale, b.tails, level, b.level_w)


def exp_prims(b: ExpBox, measure) -> list:
    """The EXP box as drawing primitives in the box's pixels: the panel's own slate gradient (hiding the
    LV box, job and name), a narrow level column ("LV" over the game's level tiles, small), and the three
    label/value lines in one column right of it, with right-aligned tails where they fit."""
    s = b.scale
    px = EXP_FONT * s
    x0, y0, x1, y1 = b.x, b.y, b.x + b.w, b.y + b.h
    gy = min(y1, y0 + round(EXP_GRADIENT_H * s))
    out = [draw.Gradient(x0, y0, x1, gy, BAR_TOP, BAR_BOTTOM)]
    if y1 > gy:
        out.append(draw.Rect(x0, gy, x1, y1, BAR_BOTTOM))
    pad = EXP_PAD * s
    cys = [y0 + b.h * f for f in EXP_LINE_Y]
    # level column on the left: "LV" over the game's level tiles; the three lines share the next column
    lw = b.level_w * LEVEL_SCALE * s if b.level is not None else 0
    col_w = max(measure(LEVEL_LABEL, px, True), lw)
    out.append(draw.Text(x0 + pad + (col_w - measure(LEVEL_LABEL, px, True)) / 2, cys[0], LEVEL_LABEL,
                         EXP_LABEL, px, True))
    if b.level is not None:
        lh = round(13 * LEVEL_SCALE * s)
        out.append(draw.Image(round(x0 + pad + (col_w - lw) / 2), round((cys[1] + cys[2]) / 2 - lh / 2), b.level))
    text_x = x0 + pad + col_w + LEVEL_GAP * s
    starts = [text_x] * 3
    for line, tail, cy, x in zip(b.lines, b.tails, cys, starts):
        for text, color in line:
            out.append(draw.Text(x, cy, text, color, px, True))
            x += measure(text, px, True)
        tail_w = sum(measure(t, px, True) for t, _ in tail)
        if tail and x + measure("  ", px, True) + tail_w <= x1 - pad:
            xr = x1 - pad
            for text, color in reversed(tail):
                out.append(draw.Text(xr, cy, text, color, px, True, "e"))
                xr -= measure(text, px, True)
    return out
