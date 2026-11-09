import statistics
import time
from pathlib import Path

import cv2
import numpy as np
import pytest

from maplestory_classic_tool.calibrate import load_fixtures
from maplestory_classic_tool.catalog import fold_il
from maplestory_classic_tool.detect import ShopLocator
from maplestory_classic_tool.glyphs import GlyphSet
from maplestory_classic_tool.layout import load_layout
from maplestory_classic_tool.shop import PRICE_RE, parse_price, row_inks, to_listing

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures" / "shops"
ASSETS = ROOT / "maplestory_classic_tool" / "assets"


@pytest.fixture(scope="module")
def layout():
    return load_layout(ASSETS / "layout.json")


@pytest.fixture(scope="module")
def anchor():
    return cv2.imread(str(ASSETS / "anchor.png"), cv2.IMREAD_GRAYSCALE)


@pytest.fixture(scope="module")
def glyphs():
    return GlyphSet.load(ASSETS / "glyphs.json")


@pytest.fixture(scope="module")
def fixtures(layout):
    return load_fixtures(FIXTURES, layout)


@pytest.fixture(scope="module")
def shops(fixtures):
    return [f for f in fixtures if f.shop]


def test_locate_on_each_shop_fixture(shops, anchor, layout):
    loc = ShopLocator(anchor)
    assert len(shops) >= 7
    for f in shops:
        loc.reset()
        pos = loc.locate(f.gray)
        assert pos is not None, f.name
        h, w = anchor.shape
        assert 0 <= pos[0] <= f.gray.shape[1] - w and 0 <= pos[1] <= f.gray.shape[0] - h
        ox, oy = layout.row_origin(pos, layout.rows - 1)
        assert oy + layout.row_pitch <= f.gray.shape[0], f.name


def test_locate_negatives_none(fixtures, anchor):
    negs = [f for f in fixtures if not f.shop]
    assert {f.name for f in negs} == {"noshop_npc", "noshop_fm", "noshop_grind", "noshop_partialinv"}
    loc = ShopLocator(anchor)
    for f in negs:
        assert loc.locate(f.gray) is None, f.name


def test_negative_clears_cache(shops, fixtures, anchor):
    loc = ShopLocator(anchor)
    assert loc.locate(shops[0].gray) is not None
    neg = next(f for f in fixtures if not f.shop)
    assert loc.locate(neg.gray) is None
    assert loc._pos is None


def test_locate_cached_position_reused(shops, anchor):
    loc = ShopLocator(anchor)
    pos = loc.locate(shops[0].gray)
    assert loc._pos == pos
    assert loc.locate(shops[0].gray) == pos


def test_locate_after_window_moved(shops, anchor):
    # convention: np.roll(gray, (21, 37), axis=(0, 1)) moves content 21 rows down, 37 cols right
    gray = shops[0].gray
    loc = ShopLocator(anchor)
    x, y = loc.locate(gray)
    moved = np.roll(gray, (21, 37), axis=(0, 1))
    assert loc.locate(moved) == (x + 37, y + 21)


@pytest.mark.perf
def test_locate_uses_cache_fast(shops, anchor):
    gray = shops[0].gray
    loc = ShopLocator(anchor)
    assert loc.locate(gray) is not None
    times = []
    for _ in range(200):
        t = time.perf_counter()
        loc.locate(gray)
        times.append(time.perf_counter() - t)
    med = statistics.median(times) * 1000
    print(f"cached locate median {med:.4f} ms")
    assert med < 0.2
    full = []
    for _ in range(20):
        loc.reset()
        t = time.perf_counter()
        loc.locate(gray)
        full.append(time.perf_counter() - t)
    print(f"full search median {statistics.median(full) * 1000:.2f} ms")


def _expected(f):
    return {r: row for r, row in enumerate(f.rows) if isinstance(row, dict)}


def test_row_inks_skips_sold_and_empty(shops, anchor, layout):
    for f in shops:
        axy = ShopLocator(anchor).locate(f.gray)
        got = {ri.row for ri in row_inks(f.gray, axy, layout)}
        occluded = {r for r, row in enumerate(f.rows) if row == "occluded"}
        assert got - occluded == set(_expected(f)), f.name
        for ri in row_inks(f.gray, axy, layout):
            assert ri.name.dtype == bool and ri.price.dtype == bool


def test_row_inks_skips_rows_outside_frame(layout):
    gray = np.zeros((100, 100), np.uint8)
    assert row_inks(gray, (50, 50), layout) == []


def test_parse_price():
    assert parse_price("100 for 5,000 mesos") == (100, 5000)
    assert parse_price("1 for 2,147,483,647 mesos") == (1, 2147483647)
    assert PRICE_RE.match("1 for 3,000 mesos")


def test_parse_price_collapses_digit_gap():
    assert parse_price("1 for 1 2,000 mesos") == (1, 12000)


def test_parse_price_unknown_glyph():
    assert parse_price("1 for 5,?00 mesos") == (None, None)
    assert parse_price("garbage") == (None, None)


def test_to_listing():
    ok = to_listing(2, "Maple Staff", "3 for 4,500 mesos", 0)
    assert (ok.row, ok.name, ok.qty, ok.price, ok.uncertain) == (2, "Maple Staff", 3, 4500, False)
    assert to_listing(0, "X", "3 for 4,500 mesos", 1).uncertain
    assert to_listing(0, "X", "?", 0).uncertain
    assert to_listing(0, "  ", "3 for 4,500 mesos", 0).uncertain
    assert not to_listing(0, "Na?me", "3 for 4,500 mesos", 0).uncertain


def test_rows_read_match_labels(shops, anchor, layout, glyphs):
    checked = 0
    for f in shops:
        axy = ShopLocator(anchor).locate(f.gray)
        exp = _expected(f)
        got = {}
        for ri in row_inks(f.gray, axy, layout):
            if f.rows[ri.row] == "occluded":
                continue
            n, p = glyphs.read(ri.name), glyphs.read(ri.price)
            got[ri.row] = to_listing(ri.row, n.text, p.text, p.unknown)
        assert set(got) == set(exp), f.name
        for r, row in exp.items():
            lst = got[r]
            assert not lst.uncertain, (f.name, r, lst)
            assert (lst.qty, lst.price) == (row["qty"], row["price"]), (f.name, r)
            if "?" in lst.name and set(fold_il(row["name"])) - set(glyphs.chars()) - {" "}:
                continue  # e.g. "LUK": no U/K templates; the OCR fallback reads these names
            assert fold_il(lst.name) == fold_il(row["name"]), (f.name, r)
            checked += 1
    assert checked > 20


def test_parse_price_rejects_malformed_grouping():
    for bad in ("1 for ,899 mesos", "1 for 8,00 mesos", "1 for 1,,899 mesos",
                "1,0 for 5 mesos", "1 for 1234,567 mesos"):
        assert parse_price(bad) == (None, None), bad


def test_parse_price_accepts_well_formed():
    assert parse_price("1 for 3,000 mesos") == (1, 3000)
    assert parse_price("100 for 5,000 mesos") == (100, 5000)
    assert parse_price("1 for 2,147,483,647 mesos") == (1, 2147483647)
    assert parse_price("1 for 99 mesos") == (1, 99)


REJECT_PAIRS = [
    ("1 for 1?? ?????", "1 for 1 mesos"),
    ("1 for 4?? ?????", "1 for 4 mesos"),
    ("1 for ?", "1 for 5 mesos"),
    ("1 for ??", "1 for 5 mesos"),
    ("?", "1 for 5 mesos"),
    ("??", "1 for 5 mesos"),
    ("1 for 1?,000 mesos", "1 for 1,000 mesos"),
    ("1 for ?? mesos", "1 for 88 mesos"),        # no known price digit
    ("? for 1,000 mesos", "1 for 1,000 mesos"),  # no known qty digit
    ("1 for 1?? ?????", "1 for 100 mesos"),      # glyph lacks known "mesos"
    ("1 for ??,000 mesos", "1 for 8,000 mesos"),   # exact length: true 88,000
    ("1 for 5?? mesos", "1 for 58 mesos"),
    ("1 for ?,500 mesos", "1 for 0,500 mesos"),    # 0 is a known glyph, cannot hide in ?
    ("1 for ?00 mesos", "1 for 000 mesos"),
    ("1 ? for 5,000 mesos", "180 for 5,000 mesos"),
    ("? 1 for 3,000 mesos", "11 for 3,000 mesos"),  # leading ? in qty
    ("1 for ?,000 mesos", "1 for 3,000 mesos"),     # 8 read as 3
]
ACCEPT_PAIRS = [
    ("1 for ?,000 mesos", "1 for 8,000 mesos"),
    ("1 for ??,000 mesos", "1 for 88,000 mesos"),
    ("1 for 12,000 mesos??", "1 for 12,000 mesos"),
    ("1 for 1 ?,000 mesos", "1 for 18,000 mesos"),  # digit-gap collapse with ?
    ("10? for 5,000 mesos", "108 for 5,000 mesos"),
]


@pytest.mark.parametrize("glyph,ocr", REJECT_PAIRS)
def test_agrees_with_glyphs_rejects(glyph, ocr):
    from maplestory_classic_tool.shop import agrees_with_glyphs
    assert not agrees_with_glyphs(glyph, ocr, "8")


@pytest.mark.parametrize("glyph,ocr", ACCEPT_PAIRS)
def test_agrees_with_glyphs_accepts(glyph, ocr):
    from maplestory_classic_tool.shop import agrees_with_glyphs
    assert agrees_with_glyphs(glyph, ocr, "8")


def test_agrees_with_glyphs_all_digits_known():
    from maplestory_classic_tool.shop import agrees_with_glyphs
    assert not agrees_with_glyphs("1 for 3,?00 mesos", "1 for 3,800 mesos", "")
    assert agrees_with_glyphs("1 for 3,000 mesos??", "1 for 3,000 mesos", "")


def test_agrees_with_glyphs():
    from maplestory_classic_tool.shop import agrees_with_glyphs as ag
    assert ag("1 for 1 ,?99 mesos ??", "1 for 1,899 mesos", "8")  # trailing fringe dropped
    assert ag("1 for 439 mesos ??", "1 for 439 mesos", "8")
    assert ag("1 for 3,?00 mesos", "1 for 3,800 mesos", "8")
    assert not ag("1 for 3,?00 mesos", "1 for 3,000 mesos", "8")  # 0 is a known glyph
    assert not ag("1 for 3,?00 mesos", "1 for 3,80 mesos", "8")   # dropped digit
    assert not ag("1 for ?99 mesos", "1 for 99 mesos", "8")
    assert not ag("", "1 for 5 mesos", "8")                         # nothing known: stays unknown


def test_parse_price_rejects_leading_zero():
    for t in ("1 for 000 mesos", "1 for 0,500 mesos", "0 for 5 mesos", "01 for 5 mesos"):
        assert parse_price(t) == (None, None)
    assert parse_price("1 for 500 mesos") == (1, 500)


def test_glyphset_chars(glyphs):
    chars = glyphs.chars()
    assert set("0123456789,") <= chars and {"K", "L", "N", "U", "X"} <= chars


def test_agreement_never_lowers_price_or_raises_qty_bruteforce():
    import itertools
    import random
    from maplestory_classic_tool.shop import agrees_with_glyphs
    rng = random.Random(1234)
    qtys = ["1", "8", "18", "108", "88"]
    prices = ["8,000", "88,000", "18", "800", "1,888", "58", "8", "380,000"]
    digits = "0123456789,"

    def variants(w):
        out = {w}
        for i in range(len(w) + 1):
            for d in digits:
                out.add(w[:i] + d + w[i:])
        for i in range(len(w)):
            out.add(w[:i] + w[i + 1:])
            for d in digits:
                out.add(w[:i] + d + w[i + 1:])
        return out

    def value(w):
        _, p = parse_price(f"1 for {w} mesos")
        return p

    checked = 0
    for q, pr in itertools.product(qtys, prices):
        q8 = [i for i, c in enumerate(q) if c == "8"]
        p8 = [i for i, c in enumerate(pr) if c == "8"]
        for qm in itertools.product([0, 1], repeat=len(q8)):
            for pm in itertools.product([0, 1], repeat=len(p8)):
                qs, ps = list(q), list(pr)
                for i, m in zip(q8, qm):
                    if m:
                        qs[i] = "?"
                for i, m in zip(p8, pm):
                    if m:
                        ps[i] = "?"
                qs, ps = "".join(qs), "".join(ps)
                glyph = f"{qs} for {ps} mesos"
                cands = [(q2, pr) for q2 in variants(q)] + [(q, p2) for p2 in variants(pr)]
                cands = rng.sample(cands, min(len(cands), 120))
                for q2, p2 in cands:
                    ocr = f"{q2} for {p2} mesos"
                    if not agrees_with_glyphs(glyph, ocr, "8"):
                        continue
                    checked += 1
                    qty_o, price_o = parse_price(ocr)
                    if qty_o is None:
                        continue
                    assert price_o >= value(pr) and qty_o <= int(q.replace(",", "")), (glyph, ocr)
    assert checked > 0

# --- name OCR fallback agreement (review round 2) ------------------------------
from maplestory_classic_tool.shop import name_agrees  # noqa: E402


@pytest.mark.parametrize("glyph,ocr", [
    ("Earring ?U? Scroll: Inter...", "Earring LUK Scroll: Inter..."),
    ("Gloves Attack Scroll: lnter...", "Gloves Attack Scroll: Inter..."),  # I/l fold
    ("Wolbi Throwing St?rs", "Wolbi Throwing Stars"),
    # real glyph reads of tests/fixtures/shops/windowed1600_shop.png (no U/K/N/X templates):
    # stroke fragments next to an unknown glyph read as thin glyphs ("l", ".")
    ("Earring l.....?l? Scroll: lnter...", "Earring LUK Scroll: Inter..."),
    ("Earring ll?T Scroll: lnterm...", "Earring INT Scroll: Interm..."),
    ("Earring DE? Scroll: lnter...", "Earring DEX Scroll: Inter..."),
    ("Earring DE? Scroll: lnter...", "Earring dex  Scroll:Inter..."),  # case and spaces ignored
])
def test_name_agrees_accepts(glyph, ocr):
    assert name_agrees(glyph, ocr)


@pytest.mark.parametrize("glyph,ocr", [
    ("Earring ?U? Scroll", "Earring STR Scroll"),
    ("Earring ll?T Scroll: lnterm...", "Earring DEX Scroll: Interm..."),
    ("Earring DE? Scroll: lnter...", "Earring DEX Scroll: Interm..."),
    ("Wolbi Throwing St?rs", "Wolbi Throwing Stars Deluxe"),
    ("Wolbi Throwing St?rs", "Wolbi Thrown Stars"),
    ("Earring ?U? Scroll", "Earring LUKKKK Scroll"),  # one unknown glyph is at most 3 characters
    ("", "Wolbi Throwing Stars"),  # no glyph evidence at all
    ("???", "Wolbi Throwing Stars"),
    ("Red Potion", ""),
])
def test_name_agrees_rejects(glyph, ocr):
    assert not name_agrees(glyph, ocr)
