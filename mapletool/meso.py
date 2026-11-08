"""Mesos from the item inventory's meso box (screen pixels only), and mesos per hour.

The box ("599,144" left of the window's "MESOS" label) is found by the label and the box's right edge
(assets/meso_label.png). Its digits are a small 5-px pixel font, read with MESO_GLYPHS: '#' must be ink,
'.' must not be, '+' is either (antialiasing differs by a pixel between draws). A digit counts only when
exactly one pattern fits, so an unknown or damaged glyph gives no reading rather than a wrong one.
Every pattern comes from live captures (611,814 · 599,144 · 602,357).
"""
from __future__ import annotations

import re
from dataclasses import dataclass

import cv2
import numpy as np

from mapletool.glyphs import column_runs
from mapletool.rate import GainRate

MIN_SCORE = 0.9  # label match (inventory open: 1.0; anything else in the fixtures: <= 0.62)
CACHE_MAX_MEAN_DIFF = 4
DIGIT_BOX = (-142, 2, 140, 11)  # (x, y, w, h) of the digits relative to the label template's top-left
INK_MAX_GRAY = 120  # dark digits on the white box
# The box may hang off the left edge of the game window (inventory dragged there); the right-aligned number
# is read from the visible part when at least this many blank columns precede it, so no digit can be hidden
# beyond the edge (digits and commas are at most 3 px apart).
MIN_EDGE_MARGIN = 4

MESO_GLYPHS = {
    ",": ("..", "..", "..", "..", "..", "..", "..", ".#", ".#", ".#", "#."),
    "1": ("...", "+#.", "##.", "+#.", ".#.", ".#.", ".#.", "+#+", "###", "...", "..."),
    "4": (".....", "..+#.", "..##.", ".#+#.", "#++#.", "#####", "..+#+", "...#.", "...#.", ".....", "....."),
    "5": (".....", "#####", "#+...", "#+...", "####.", "...+#", "....#", "#..+#", ".###.", ".....", "....."),
    "6": (".....", "..##.", ".#+..", "#+...", "####.", "#+.+#", "#...#", "#+.+#", ".###.", ".....", "....."),
    "8": (".....", ".###.", "#+.+#", "#+.+#", "+###+", "#+.+#", "#...#", "#+.+#", ".###.", ".....", "....."),
    "9": (".....", ".###.", "#+.+#", "#...#", "#+.+#", ".####", "...+#", "..+#.", ".##..", ".....", "....."),
    # one capture each so far (602,357): '+' also where the pixel sits within 15 of a threshold
    "0": (".....", "+###+", "#+.+#", "#...#", "#...#", "#...#", "#...#", "#+.+#", "+###+", ".....", "....."),
    "2": (".....", "+###+", "++.+#", "...+#", "..+#+", ".+++.", "+++..", "#++..", "#####", ".....", "....."),
    "3": (".....", "+###+", "++.+#", "...+#", "..##+", "...+#", "....#", "++.+#", "+###+", ".....", "....."),
    "7": (".....", "#####", "..++#", "...#+", "..+#.", "..#+.", ".+#..", ".#+..", ".#...", ".....", "....."),
}
_MESO_RE = re.compile(r"^\d{1,3}(?:,\d{3})*$")


def _masks(rows: tuple):
    a = np.array([list(r) for r in rows])
    return a == "#", a == "."


_TEMPLATES = {ch: _masks(rows) for ch, rows in MESO_GLYPHS.items()}


def classify(run: np.ndarray):
    """The character whose pattern fits this run (bool ink, 11 rows) exactly, or None (none or several)."""
    hits = []
    for ch, (ink, bg) in _TEMPLATES.items():
        if ink.shape == run.shape and run[ink].all() and not run[bg].any():
            hits.append(ch)
    return hits[0] if len(hits) == 1 else None


def read_digits(gray_box: np.ndarray, clipped: bool = False):
    """Mesos from the digit box, or None (no digits, an unknown glyph, bad digit grouping, or - when the
    box is `clipped` at the frame's left edge - a number that may continue beyond the edge)."""
    ink = gray_box < INK_MAX_GRAY
    runs = column_runs(ink)
    if clipped and (not runs or runs[0][0] < MIN_EDGE_MARGIN):
        return None
    text = []
    for x0, x1 in runs:
        ch = classify(ink[:, x0:x1])
        if ch is None:
            return None
        text.append(ch)
    s = "".join(text)
    return int(s.replace(",", "")) if _MESO_RE.match(s) else None


@dataclass(frozen=True)
class MesoReading:
    visible: bool  # the inventory's meso box is on screen
    mesos: int | None  # None: visible but not readable (cursor over it, unknown glyph)


class MesoReader:
    def __init__(self, label: np.ndarray, min_score: float = MIN_SCORE):
        self.label = label
        self.min_score = min_score
        self._pos = None
        self._ref = None

    def reset(self) -> None:
        self._pos = None
        self._ref = None

    def locate(self, gray: np.ndarray):
        h, w = self.label.shape
        if self._pos is not None:
            x, y = self._pos
            region = gray[y:y + h, x:x + w]
            if region.shape == self.label.shape and \
                    np.abs(region.astype(np.int16) - self._ref).mean() <= CACHE_MAX_MEAN_DIFF:
                return self._pos
        self._pos = self._ref = None
        if gray.shape[0] < h or gray.shape[1] < w:
            return None
        _, score, _, loc = cv2.minMaxLoc(cv2.matchTemplate(gray, self.label, cv2.TM_CCOEFF_NORMED))
        if score < self.min_score:
            return None
        self._pos = (int(loc[0]), int(loc[1]))
        self._ref = gray[self._pos[1]:self._pos[1] + h, self._pos[0]:self._pos[0] + w].astype(np.int16)
        return self._pos

    def read(self, gray: np.ndarray) -> MesoReading:
        pos = self.locate(gray)
        if pos is None:
            return MesoReading(False, None)
        x, y, w, h = DIGIT_BOX
        x0, y0 = pos[0] + x, pos[1] + y
        if y0 < 0 or y0 + h > gray.shape[0]:
            return MesoReading(True, None)
        return MesoReading(True, read_digits(gray[y0:y0 + h, max(0, x0):x0 + w], clipped=x0 < 0))


class MesoTracker(GainRate):
    """Meso readings -> session gain and mesos per hour (pure; caller's clock).

    A value counts once two consecutive readings agree (a single misread or a cursor-damaged frame never
    moves it). Increases are gains (drops, quest rewards, NPC sales); decreases (buying potions, shop
    purchases) are spending and are never subtracted. Rate and idle handling as GainRate."""

    def reset(self) -> None:
        super().reset()
        self.mesos = None
        self._candidate = None

    def add(self, mesos: int, now: float) -> bool:
        if mesos != self._candidate:
            self._candidate = mesos
            return False
        if self.mesos is None:
            self.mesos = mesos
            self.start(now)
            return True
        if mesos > self.mesos:
            self.gain(mesos - self.mesos, now)
        self.mesos = mesos
        return True
