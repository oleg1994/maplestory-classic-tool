"""Build glyphs.json from labelled shop screenshots; draw layout debug images.

python -m mapletool.calibrate build FIXTURE_DIR --out mapletool/assets/glyphs.json [--holdout NAME]
python -m mapletool.calibrate draw SHOT.png --out dbg.png
"""
import argparse
import difflib
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np

from mapletool.catalog import fold_il
from mapletool.glyphs import GlyphSet, binarize, column_runs, glyph_key
from mapletool.layout import Layout, load_layout, to_base
from mapletool.shop import collapse_digit_spaces

ASSETS = Path(__file__).resolve().parent / "assets"
DEFAULT_PRICE_FORMAT = "{qty} for {price:,} mesos"
DEFAULT_SPACE_GAP = 3  # only used when the training lines contain no gaps at all
DEFAULT_TOLERANCE = 0.0  # base-resolution rendering is pixel-exact: exact matches only
_DIGITISH = frozenset("0123456789,")


def price_text(qty: int, price: int, fmt: str = DEFAULT_PRICE_FORMAT) -> str:
    return fmt.format(qty=qty, price=price)


@dataclass
class Fixture:
    name: str
    shop: bool
    rows: list  # dict | None | "occluded" per visible row
    gray: np.ndarray  # base resolution


@dataclass
class Line:
    source: str
    text: str
    ink: np.ndarray
    touching: tuple = ()  # label hint: substrings drawn as one run (e.g. "xt" in "Dexterity")


@dataclass
class BuildReport:
    glyphs: GlyphSet
    trained: int
    skipped: list = field(default_factory=list)
    conflicts: list = field(default_factory=list)  # sorted (char, char) drawn identically
    multi: list = field(default_factory=list)  # learned multi-char templates (touching runs)
    holdout_lines: int = 0
    wrong: int = 0
    unknown: int = 0
    unknown_chars: list = field(default_factory=list)  # holdout label chars read as "?"


def load_fixtures(fixture_dir: Path, layout: Layout) -> list:
    out = []
    for js in sorted(Path(fixture_dir).glob("*.json")):
        data = json.loads(js.read_text(encoding="utf-8"))
        gray = cv2.imread(str(js.with_suffix(".png")), cv2.IMREAD_GRAYSCALE)
        if gray is None:
            raise FileNotFoundError(js.with_suffix(".png"))
        base = tuple(data.get("base_size", layout.base_size))  # captures at other window sizes carry theirs
        out.append(Fixture(js.stem, bool(data["shop"]), list(data["rows"]), to_base(gray, base)))
    return out


def match_anchor(gray: np.ndarray, anchor: np.ndarray) -> tuple:
    """Best anchor position (x, y) and its TM_CCOEFF_NORMED score."""
    res = cv2.matchTemplate(gray, anchor, cv2.TM_CCOEFF_NORMED)
    _, score, _, loc = cv2.minMaxLoc(res)
    return (int(loc[0]), int(loc[1])), float(score)


def box_ink(gray: np.ndarray, origin: tuple, box: tuple, ink_max_gray: int) -> np.ndarray:
    ox, oy = origin
    x, y, w, h = box
    return binarize(gray[oy + y: oy + y + h, ox + x: ox + x + w], ink_max_gray)


def fixture_lines(fixtures: list, layout: Layout, anchor: np.ndarray) -> dict:
    """{fixture name: [Line]} for every labelled (dict) row: name line, then price line."""
    out = {}
    for f in fixtures:
        if not f.shop:
            continue
        axy, _ = match_anchor(f.gray, anchor)
        lines = []
        for r, row in enumerate(f.rows):
            if not isinstance(row, dict):
                continue
            origin = layout.row_origin(axy, r)
            lines.append(Line(f"{f.name} row {r} name", row["name"],
                              box_ink(f.gray, origin, layout.name_box, layout.ink_max_gray),
                              tuple(row.get("touching", ()))))
            lines.append(Line(f"{f.name} row {r} price",
                              price_text(row["qty"], row["price"], layout.price_format),
                              box_ink(f.gray, origin, layout.price_box, layout.ink_max_gray)))
        out[f.name] = lines
    return out


def word_gaps(ink: np.ndarray, text: str) -> tuple:
    """(intra-word gaps, inter-word gaps) in px, or (None, None) if runs != chars.

    Gaps between two digits/commas are left out of intra: digits are tabular, so the gap
    after "1" is as wide as a word gap; collapse_digit_spaces repairs those reads.
    """
    runs = column_runs(ink)
    if len(runs) != len(text.replace(" ", "")):
        return None, None
    intra, inter = [], []
    i, prev, space_before = 0, None, False
    for ch in text:
        if ch == " ":
            space_before = True
            continue
        if i > 0:
            gap = runs[i][0] - runs[i - 1][1]
            if space_before:
                inter.append(gap)
            elif not (prev in _DIGITISH and ch in _DIGITISH):
                intra.append(gap)
        prev, space_before, i = ch, False, i + 1
    return intra, inter


def _diff(expected: str, got: str) -> tuple:
    """(wrong, label substrings read as '?'). wrong = chars read as a different non-'?'
    char, plus chars dropped or invented."""
    wrong, unknown_chars = 0, []
    sm = difflib.SequenceMatcher(None, expected, got, autojunk=False)
    for op, i1, i2, j1, j2 in sm.get_opcodes():
        if op == "equal":
            continue
        read = got[j1:j2]
        wrong += sum(1 for c in read if c != "?")
        wrong += max(0, (i2 - i1) - len(read))
        if "?" in read:
            unknown_chars.append(expected[i1:i2])
    return wrong, unknown_chars


def _word_groups(runs: list, space_gap: int) -> list:
    groups = [[runs[0]]] if runs else []
    for prev, run in zip(runs, runs[1:]):
        if run[0] - prev[1] >= space_gap:
            groups.append([run])
        else:
            groups[-1].append(run)
    return groups


def _split_known(seg: np.ndarray, known: dict, widths: list):
    """Text of a run as a concatenation of known templates (exact matches, widest first,
    as GlyphSet.read splits touching glyphs), or None."""
    t = known.get(glyph_key(seg))
    if t:
        return t
    out, pos, w = [], 0, seg.shape[1]
    while pos < w:
        for wd in widths:
            if wd <= w - pos:
                t = known.get(glyph_key(seg[:, pos:pos + wd]))
                if t:
                    out.append(t)
                    pos += wd
                    break
        else:
            return None
        while pos < w and not seg[:, pos].any():
            pos += 1
    return "".join(out)


def _units(word: str, touching: tuple) -> list:
    """The word as runs: each `touching` substring (labelled as drawn joined) is one unit, other chars one each."""
    out, i = [], 0
    while i < len(word):
        for t in sorted(touching, key=len, reverse=True):
            if t and word.startswith(t, i):
                out.append(t)
                i += len(t)
                break
        else:
            out.append(word[i])
            i += 1
    return out


def build(lines: dict, holdout: str = None, tolerance: float = DEFAULT_TOLERANCE) -> BuildReport:
    """Train a GlyphSet from labelled lines (label "I" is folded into "l").

    Pass 1: lines whose run count equals the char count give single-char templates and
    the space gap. Pass 2: other lines are split into words at the space gap; a word whose
    run count equals its char count adds singles; otherwise its runs are matched against
    known templates (whole run, or split into known templates) from both ends and, if
    exactly one run is left, it becomes a template
    for the leftover substring (touching glyphs, e.g. "tt"). Repeated until nothing new is
    learned; words still left with 2+ unmatched runs are skipped and reported.
    """
    templates = {}  # (glyph_key, text) -> ink
    known = {}  # glyph_key -> text (last wins, as in GlyphSet exact lookup)
    seen = {}  # glyph_key -> set of texts
    skipped = []

    def learn(text, seg):
        key = glyph_key(seg)
        seen.setdefault(key, set()).add(text)
        templates.setdefault((key, text), seg)
        known[key] = text

    intra, inter, pending = [], [], []
    for name, ls in lines.items():
        if name == holdout:
            continue
        for ln in ls:
            text = fold_il(ln.text)
            runs = column_runs(ln.ink)
            chars = text.replace(" ", "")
            if len(runs) == len(chars):
                gi, ge = word_gaps(ln.ink, text)
                intra += gi
                inter += ge
                for ch, (x0, x1) in zip(chars, runs):
                    learn(ch, ln.ink[:, x0:x1])
            else:
                pending.append((ln, text, runs))
    if intra:
        space_gap = max(intra) + 1
        if inter:
            space_gap = min(space_gap, min(inter))
    else:
        space_gap = min(inter) if inter else DEFAULT_SPACE_GAP

    words_todo = []  # (line, word, [ink per run])
    multi = []
    for ln, text, runs in pending:
        words = text.split()
        groups = _word_groups(runs, space_gap)
        if len(groups) != len(words):
            skipped.append(f"{ln.source}: {ln.text!r} words={len(words)} "
                           f"run groups={len(groups)}")
            continue
        for word, group in zip(words, groups):
            segs = [ln.ink[:, x0:x1] for x0, x1 in group]
            units = _units(word, ln.touching)
            if len(segs) == len(units):
                for unit, seg in zip(units, segs):
                    learn(unit, seg)
                    if len(unit) > 1:
                        multi.append(unit)
            else:
                words_todo.append((ln, word, segs))

    progress = True
    while progress and words_todo:
        progress, rest = False, []
        widths = sorted({seg.shape[1] for seg in templates.values()}, reverse=True)
        for ln, word, segs in words_todo:
            i, j, ci, cj = 0, len(segs), 0, len(word)
            while i < j:
                t = _split_known(segs[i], known, widths)
                if not t or not word.startswith(t, ci, cj):
                    break
                i, ci = i + 1, ci + len(t)
            while j > i:
                t = _split_known(segs[j - 1], known, widths)
                if not t or not word.endswith(t, ci, cj):
                    break
                j, cj = j - 1, cj - len(t)
            if j - i == 1 and cj > ci:
                sub = word[ci:cj]
                learn(sub, segs[i])
                if len(sub) > 1:
                    multi.append(sub)
                progress = True
            elif not (j == i and cj == ci):  # fully explained words are simply dropped
                rest.append((ln, word, segs))
        words_todo = rest
    for ln, word, segs in words_todo:
        skipped.append(f"{ln.source}: {ln.text!r} word {word!r} "
                       f"runs={len(segs)} chars={len(word)}")

    glyph_dict = {}
    for (_, text), seg in templates.items():
        glyph_dict.setdefault(text, []).append(seg)
    gs = GlyphSet(glyph_dict, space_gap, tolerance)
    conflicts = sorted({tuple(sorted(t)) for t in seen.values() if len(t) > 1})
    rep = BuildReport(gs, len(templates), skipped, conflicts, sorted(set(multi)))
    for ln in lines.get(holdout, []) if holdout else []:
        r = gs.read(ln.ink)
        wrong, unknown_chars = _diff(collapse_digit_spaces(fold_il(ln.text)),
                                     collapse_digit_spaces(r.text))
        rep.holdout_lines += 1
        rep.wrong += wrong
        rep.unknown += r.unknown
        rep.unknown_chars += unknown_chars
    return rep


def draw(shot: Path, out: Path, layout: Layout, anchor: np.ndarray) -> float:
    bgr = cv2.imread(str(shot))
    if bgr is None:
        raise FileNotFoundError(shot)
    w, h = layout.base_size
    base = bgr if bgr.shape[:2] == (h, w) else cv2.resize(bgr, (w, h), interpolation=cv2.INTER_AREA)
    gray = cv2.cvtColor(base, cv2.COLOR_BGR2GRAY)
    (ax, ay), score = match_anchor(gray, anchor)
    img = cv2.resize(base, None, fx=2, fy=2, interpolation=cv2.INTER_NEAREST)

    def rect(x, y, bw, bh, color):
        cv2.rectangle(img, (2 * x, 2 * y), (2 * (x + bw) - 1, 2 * (y + bh) - 1), color, 1)

    rect(ax, ay, anchor.shape[1], anchor.shape[0], (0, 0, 255))
    cv2.putText(img, f"{score:.3f}", (2 * ax, 2 * ay - 4), cv2.FONT_HERSHEY_SIMPLEX, 0.5,
                (0, 0, 255), 1)
    for r in range(layout.rows):
        ox, oy = layout.row_origin((ax, ay), r)
        rect(ox, oy, layout.name_box[0] + layout.name_box[2], layout.row_pitch, (255, 0, 0))
        rect(ox + layout.name_box[0], oy + layout.name_box[1], *layout.name_box[2:], (0, 160, 0))
        rect(ox + layout.price_box[0], oy + layout.price_box[1], *layout.price_box[2:],
             (0, 160, 255))
        cv2.drawMarker(img, (2 * (ox + layout.label_dx), 2 * (oy + layout.row_pitch // 2)),
                       (255, 0, 255), cv2.MARKER_CROSS, 10)
    cv2.imwrite(str(out), img)
    return score


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="python -m mapletool.calibrate")
    p.add_argument("--layout", type=Path, default=ASSETS / "layout.json")
    p.add_argument("--anchor", type=Path, default=ASSETS / "anchor.png")
    sub = p.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build")
    b.add_argument("fixture_dir", type=Path)
    b.add_argument("--out", type=Path, required=True)
    b.add_argument("--holdout")
    b.add_argument("--tolerance", type=float, default=DEFAULT_TOLERANCE)
    d = sub.add_parser("draw")
    d.add_argument("shot", type=Path)
    d.add_argument("--out", type=Path, required=True)
    a = p.parse_args(argv)

    layout = load_layout(a.layout)
    anchor = cv2.imread(str(a.anchor), cv2.IMREAD_GRAYSCALE)
    if anchor is None:
        raise FileNotFoundError(a.anchor)
    if a.cmd == "draw":
        score = draw(a.shot, a.out, layout, anchor)
        print(f"anchor score={score:.3f} -> {a.out}")
        return 0
    lines = fixture_lines(load_fixtures(a.fixture_dir, layout), layout, anchor)
    if a.holdout and a.holdout not in lines:
        p.error(f"holdout {a.holdout!r} is not a labelled shop fixture")
    rep = build(lines, a.holdout, a.tolerance)
    for s in rep.skipped:
        print(f"skipped {s}")
    for c in rep.conflicts:
        print("identical bitmap for chars " + " ".join(map(repr, c)))
    if rep.multi:
        print("multi-char templates: " + " ".join(map(repr, rep.multi)))
    if rep.unknown_chars:
        print("holdout label chars read as '?': " + " ".join(map(repr, rep.unknown_chars)))
    rep.glyphs.save(a.out)
    print(f"trained={rep.trained} glyphs, holdout lines={rep.holdout_lines}, "
          f"wrong={rep.wrong}, unknown={rep.unknown}")
    return 1 if rep.wrong > 0 else 0


if __name__ == "__main__":
    sys.exit(main())
