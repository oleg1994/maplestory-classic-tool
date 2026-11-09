import json
from pathlib import Path

import cv2
import numpy as np
import pytest

from maplestory_classic_tool.calibrate import (
    Line, build, collapse_digit_spaces, fixture_lines, load_fixtures, match_anchor, price_text,
)
from maplestory_classic_tool.catalog import fold_il
from maplestory_classic_tool.glyphs import GlyphSet, binarize, column_runs
from maplestory_classic_tool.layout import Layout, load_layout, to_base

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures" / "shops"
ASSETS = ROOT / "maplestory_classic_tool" / "assets"
HOLDOUT = "shop6"  # only fixture whose rows appear nowhere else


@pytest.fixture(scope="module")
def layout():
    return load_layout(ASSETS / "layout.json")


@pytest.fixture(scope="module")
def anchor():
    return cv2.imread(str(ASSETS / "anchor.png"), cv2.IMREAD_GRAYSCALE)


@pytest.fixture(scope="module")
def fixtures(layout):
    return load_fixtures(FIXTURES, layout)


@pytest.fixture(scope="module")
def lines(fixtures, layout, anchor):
    return fixture_lines(fixtures, layout, anchor)


# --- layout -----------------------------------------------------------------

def test_load_layout_types(layout):
    assert layout.base_size == (1366, 768)
    assert isinstance(layout.anchor_to_grid, tuple)
    assert isinstance(layout.name_box, tuple) and len(layout.name_box) == 4
    assert isinstance(layout.price_box, tuple) and len(layout.price_box) == 4
    assert layout.rows == 6 and layout.row_pitch == 40


def test_row_origin(layout):
    ax, ay = 100, 200
    dx, dy = layout.anchor_to_grid
    assert layout.row_origin((ax, ay), 0) == (ax + dx, ay + dy)
    assert layout.row_origin((ax, ay), 3) == (ax + dx, ay + dy + 3 * layout.row_pitch)


def test_to_base_resizes_and_passes_through():
    big = np.full((2160, 3840), 200, np.uint8)
    small = to_base(big, (1366, 768))
    assert small.shape == (768, 1366)
    assert to_base(small, (1366, 768)) is small


# --- fixtures, anchor, boxes ------------------------------------------------

def test_fixture_labels_well_formed(fixtures, layout):
    assert {f.name for f in fixtures} >= {"shop1", "shop7", "noshop_npc", "noshop_fm"}
    for f in fixtures:
        assert f.gray.shape[0] == layout.base_size[1]  # base height is always 768; width follows the aspect
        if f.shop:
            assert len(f.rows) == layout.rows, f.name
        else:
            assert f.rows == []


def test_anchor_matches_shops_not_negatives(fixtures, anchor):
    for f in fixtures:
        _, score = match_anchor(f.gray, anchor)
        if f.shop:
            assert score >= 0.92, (f.name, score)
        else:
            assert score < 0.7, (f.name, score)


def _box(gray, origin, box, ink_max_gray):
    x, y, w, h = box
    ox, oy = origin
    return binarize(gray[oy + y: oy + y + h, ox + x: ox + x + w], ink_max_gray)


def test_layout_boxes_cover_text(fixtures, layout, anchor):
    checked = 0
    for f in fixtures:
        if not f.shop:
            continue
        axy, _ = match_anchor(f.gray, anchor)
        for r, row in enumerate(f.rows):
            if row == "occluded":
                continue
            origin = layout.row_origin(axy, r)
            name = _box(f.gray, origin, layout.name_box, layout.ink_max_gray)
            price = _box(f.gray, origin, layout.price_box, layout.ink_max_gray)
            if row is None:
                assert not name.any() and not price.any(), (f.name, r)
            else:
                assert name.any() and price.any(), (f.name, r)
                # text must not touch the box edges (box not clipping glyphs)
                for ink in (name, price):
                    assert not ink[0].any() and not ink[-1].any(), (f.name, r)
                    assert not ink[:, 0].any() and not ink[:, -1].any(), (f.name, r)
            checked += 1
    assert checked > 20


def test_price_text_default_format(layout):
    assert price_text(1, 3000, layout.price_format) == "1 for 3,000 mesos"
    assert price_text(100, 2147483647, layout.price_format) == "100 for 2,147,483,647 mesos"


# --- calibration --------------------------------------------------------------

def test_collapse_digit_spaces():
    assert collapse_digit_spaces("1 for 1 2,000 mesos") == "1 for 12,000 mesos"
    assert collapse_digit_spaces("1 for 1 1 , 1 5 0 mesos") == "1 for 11,150 mesos"
    assert collapse_digit_spaces("Level 1 Item") == "Level 1 Item"


def test_build_from_fixtures_no_wrong_on_holdout(lines):
    rep = build(lines, holdout=HOLDOUT)
    assert rep.holdout_lines > 0
    assert rep.wrong == 0
    assert rep.unknown == len(rep.unknown_chars)
    assert rep.trained > 30
    assert rep.glyphs.tolerance == 0.0


_SHAPES = {
    "a": ["111", "101", "111", "101", "101"],
    "b": ["110", "101", "110", "101", "110"],
    "c": ["111", "100", "100", "100", "111"],
}


def _render(text, gap=1, space=4):
    cols, pending = [], 0
    for ch in text:
        if ch == " ":
            pending = space
            continue
        if cols:
            cols.extend([np.zeros(5, bool)] * pending)
        cols.extend(np.array([[c == "1" for c in r] for r in _SHAPES[ch]], bool).T)
        pending = gap
    return np.stack(cols, axis=1)


def test_glyph_alignment_skips_mismatched_lines():
    good = Line("t1", "ab", _render("ab"))
    bad = Line("t1", "ca", _render("cab"))  # 3 runs, label has 2 chars
    rep = build({"t1": [good, bad]})
    assert len(rep.skipped) == 1 and "'ca'" in rep.skipped[0]
    assert set(rep.glyphs.exact.values()) == {"a", "b"}  # 'c' never trained


def _kerned(a, b):
    """a and b overlapping by one column (like the game's "Att"): not splittable."""
    ga, gb = _render(a), _render(b)
    out = np.zeros((5, ga.shape[1] + gb.shape[1] - 1), bool)
    out[:, :ga.shape[1]] |= ga
    out[:, ga.shape[1] - 1:] |= gb
    return out


def test_build_learns_multichar_template_from_touching_run():
    touching = _kerned("b", "c")
    ink = np.concatenate([_render("a"), np.zeros((5, 1), bool), touching], axis=1)
    lines = [Line("t1", "ab", _render("ab")), Line("t1", "ca", _render("ca")),
             Line("t1", "abc", ink)]
    rep = build({"t1": lines})
    assert rep.multi == ["bc"] and rep.skipped == []
    assert rep.glyphs.read(ink).text == "abc"


def test_build_explains_touching_known_glyphs_by_split():
    # "ab" touching (one run, splittable into known a + b), then an unseen "c" run
    touching = np.concatenate([_render("a"), _render("b")], axis=1)
    ink = np.concatenate([touching, np.zeros((5, 1), bool), _render("c")], axis=1)
    rep = build({"t1": [Line("t1", "ab", _render("ab")), Line("t1", "abc", ink)]})
    assert rep.skipped == [] and rep.multi == []
    assert rep.glyphs.read(ink).text == "abc"


def test_build_skips_word_with_two_unmatched_runs():
    touching = _kerned("b", "c")
    ink = np.concatenate([touching, np.zeros((5, 1), bool), _render("a"),
                          np.zeros((5, 1), bool), touching], axis=1)
    rep = build({"t1": [Line("t1", "ab", _render("ab")), Line("t1", "bcabc", ink)]})
    assert rep.multi == [] and len(rep.skipped) == 1 and "'bcabc'" in rep.skipped[0]


def test_touching_hint_learns_two_joined_pairs_in_one_word():
    """Like "Dexterity" ("xt" and "ty" each drawn as one run): the label names the joined pairs."""
    bc = _kerned("b", "c")
    ink = np.concatenate([bc, np.zeros((5, 1), bool), _render("a"), np.zeros((5, 1), bool), _kerned("c", "a")],
                         axis=1)
    rep = build({"t1": [Line("t1", "ab", _render("ab")), Line("t1", "bcaca", ink, ("bc", "ca"))]})
    assert rep.skipped == [] and rep.multi == ["bc", "ca"]
    assert rep.glyphs.read(ink).text == "bcaca"


def test_build_folds_capital_i_into_l():
    rep = build({"t1": [Line("t1", "Ib", _render("ab"))]})
    assert set(rep.glyphs.exact.values()) == {"l", "b"}


def test_build_reports_identical_bitmaps():
    rep = build({"t1": [Line("t1", "ab", _render("ab")), Line("t1", "c", _render("a"))]})
    assert rep.conflicts == [("a", "c")]


_DIGITISH = set("0123456789,")


def test_space_gap_between_intra_and_inter_word_gaps(lines):
    # digit-digit gaps are excluded: digits are tabular, the gap after "1" equals a word
    # gap; collapse_digit_spaces repairs those reads
    rep = build(lines)
    intra, inter = [], []
    for ls in lines.values():
        for ln in ls:
            runs = column_runs(ln.ink)
            if len(runs) != len(ln.text.replace(" ", "")):
                continue
            i, prev, space = 0, None, False
            for ch in ln.text:
                if ch == " ":
                    space = True
                    continue
                if i:
                    gap = runs[i][0] - runs[i - 1][1]
                    if space:
                        inter.append(gap)
                    elif not (prev in _DIGITISH and ch in _DIGITISH):
                        intra.append(gap)
                prev, space, i = ch, False, i + 1
    assert intra and inter
    assert max(intra) < rep.glyphs.space_gap <= min(inter)


def test_shipped_glyphs_read_every_labelled_line(lines):
    gs = GlyphSet.load(ASSETS / "glyphs.json")
    known = set(gs.chars()) | {" "}
    skipped = []
    for ls in lines.values():
        for ln in ls:
            r = gs.read(ln.ink)
            got = collapse_digit_spaces(r.text)
            if r.unknown and set(fold_il(ln.text)) - known:  # e.g. "LUK": no U/K templates (OCR fills it)
                skipped.append(ln.source)
                continue
            assert (got, r.unknown) == (collapse_digit_spaces(fold_il(ln.text)), 0), ln.source
    # retrained with the 1228-base captures: K/L/N/U/X and 8 have templates, every labelled line reads
    assert skipped == [], skipped


def test_fixture_json_rows_valid():
    for p in FIXTURES.glob("*.json"):
        data = json.loads(p.read_text(encoding="utf-8"))
        assert isinstance(data["shop"], bool)
        for row in data["rows"]:
            assert row is None or row == "occluded" or (
                isinstance(row["name"], str) and isinstance(row["qty"], int)
                and isinstance(row["price"], int)), (p.name, row)
