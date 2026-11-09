import json
import statistics
import subprocess
import sys
import time
from pathlib import Path

import cv2
import numpy as np
import pytest

from maplestory_classic_tool.catalog import Catalog
from maplestory_classic_tool.config import Settings
from maplestory_classic_tool.detect import ShopLocator
from maplestory_classic_tool.evaluate import annotations
from maplestory_classic_tool.glyphs import GlyphSet
from maplestory_classic_tool.layout import load_layout, to_base
from maplestory_classic_tool.model import Item
from maplestory_classic_tool.scanner import Scanner

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures" / "shops"
ASSETS = ROOT / "maplestory_classic_tool" / "assets"


def _labels(n):
    return json.loads((FIXTURES / f"shop{n}.json").read_text(encoding="utf-8"))["rows"]


def _frame(name, layout):
    g = cv2.imread(str(FIXTURES / name), cv2.IMREAD_GRAYSCALE)
    return to_base(g, layout.base_size)


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
def catalog():
    """Row 0 of shop2 profits (+100), row 1 does not (-1000 x ... npc total below price)."""
    rows = _labels(2)
    items = []
    for i, r in enumerate(rows):
        if not isinstance(r, dict):
            continue
        if i == 0:
            npc = (r["price"] + 100) // r["qty"]
        elif i == 1:
            npc = max(1, (r["price"] - 1000) // r["qty"])
        else:
            npc = 1
        items.append(Item(1000 + i, r["name"], npc))
    return Catalog(items, 0)


def make(layout, anchor, glyphs, catalog, fallback=None):
    return Scanner(ShopLocator(anchor), layout, glyphs, catalog, Settings(), fallback)


@pytest.fixture
def frame2(layout):
    return _frame("shop2.png", layout)


def test_scan_fixture_verdicts(layout, anchor, glyphs, catalog, frame2):
    res = make(layout, anchor, glyphs, catalog).scan(frame2)
    labels = _labels(2)
    assert res.anchor is not None and res.changed
    by_row = {v.listing.row: v for v in res.verdicts}
    for i, r in enumerate(labels):
        if isinstance(r, dict):
            l = by_row[i].listing
            assert (l.qty, l.price) == (r["qty"], r["price"]), i
    assert by_row[0].status == "profit" and by_row[0].profit == 100
    assert by_row[1].status == "no_profit" and by_row[1].profit < 0


def test_scan_unchanged_frame_not_changed(layout, anchor, glyphs, catalog, frame2):
    sc = make(layout, anchor, glyphs, catalog)
    first = sc.scan(frame2)
    second = sc.scan(frame2)
    assert not second.changed and second.verdicts == first.verdicts


def test_scan_no_shop_clears(layout, anchor, glyphs, catalog, frame2):
    sc = make(layout, anchor, glyphs, catalog)
    assert sc.scan(frame2).verdicts
    res = sc.scan(_frame("noshop_fm.png", layout))
    assert res.anchor is None and res.verdicts == []


def test_annotations_show_all_toggle(layout, anchor, glyphs, catalog, frame2):
    res = make(layout, anchor, glyphs, catalog).scan(frame2)
    few = annotations(res, layout, False)
    many = annotations(res, layout, True)
    assert [a.row for a in few] == [0] and few[0].text == "NPC +100"
    assert len(many) == len(res.verdicts) > len(few)
    ox, oy = layout.row_origin(res.anchor, 0)
    assert (few[0].x, few[0].y) == (ox + layout.label_dx, oy)
    assert any(a.status == "no_profit" and a.text.startswith("NPC ") for a in many)


class FakeOcr:
    def __init__(self, name_text, price_text):
        self.name_text, self.price_text, self.calls = name_text, price_text, []

    def read(self, crop):
        self.calls.append(crop.shape)
        return self.name_text if len(self.calls) % 2 else self.price_text  # name, then price per row


def test_fallback_policy(layout, anchor, glyphs, catalog, frame2):
    class EmptyGlyphs:
        """Glyph set that reads nothing: every box has unknown glyphs."""
        def read(self, ink):
            from maplestory_classic_tool.glyphs import ReadResult
            return ReadResult("", 3)

    fb = FakeOcr("Wolbi Throwing Stars", "1 for 3,000 mesos")
    sc = Scanner(ShopLocator(anchor), layout, EmptyGlyphs(), catalog, Settings(), fb)
    v0 = sc.scan(frame2).verdicts[0]
    # no known glyph to agree with: the OCR name is not trusted (it could map to a wrong item)
    assert v0.listing.name == ""
    # glyphs read nothing known: OCR price has no positional evidence, so it stays unknown
    assert (v0.listing.qty, v0.listing.price, v0.listing.uncertain) == (None, None, True)

    # unparseable fallback price: stays uncertain -> unknown
    fb2 = FakeOcr("Wolbi Throwing Stars", "garbage")
    sc2 = Scanner(ShopLocator(anchor), layout, EmptyGlyphs(), catalog, Settings(), fb2)
    v = sc2.scan(frame2).verdicts[0]
    assert v.listing.uncertain and v.status == "unknown"


@pytest.mark.perf
def test_scan_cold_under_5ms(layout, anchor, glyphs, catalog, frame2):
    sc = make(layout, anchor, glyphs, catalog)
    sc.scan(frame2)
    times = []
    for _ in range(50):
        sc._texts.clear()
        sc._last_key = None
        t = time.perf_counter()
        sc.scan(frame2)
        times.append(time.perf_counter() - t)
    med = statistics.median(times) * 1000
    print(f"cold median {med:.3f} ms")
    assert med < 5


@pytest.mark.perf
def test_scan_unchanged_under_1ms(layout, anchor, glyphs, catalog, frame2):
    sc = make(layout, anchor, glyphs, catalog)
    sc.scan(frame2)
    times = []
    for _ in range(50):
        t = time.perf_counter()
        sc.scan(frame2)
        times.append(time.perf_counter() - t)
    med = statistics.median(times) * 1000
    print(f"unchanged median {med:.3f} ms")
    assert med < 1


def test_cli_image_mode(tmp_path, catalog):
    cat_path = tmp_path / "catalog.json"
    catalog.save(cat_path)
    out = tmp_path / "out.png"
    proc = subprocess.run(
        [sys.executable, "-m", "maplestory_classic_tool", "--image", str(FIXTURES / "shop2.png"),
         "--out", str(out), "--show-all", "--catalog", str(cat_path)],
        capture_output=True, encoding="utf-8", cwd=ROOT, timeout=120)
    assert proc.returncode == 0, proc.stderr
    assert out.exists() and cv2.imread(str(out)) is not None
    lines = [l for l in proc.stdout.splitlines() if l.startswith("row=")]
    expected = [r for r in _labels(2) if isinstance(r, dict)]
    assert len(lines) == len(expected)
    assert "status=profit" in lines[0]


class _ForcedPriceGlyphs:
    """Real glyph reads for names; every price box reads as partially unknown."""
    def __init__(self, real, price_text, drop=""):
        self.real, self.price_text, self.drop = real, price_text, drop

    def chars(self):
        return self.real.chars() - set(self.drop)

    def read(self, ink):
        res = self.real.read(ink)
        if " for " in res.text:
            from maplestory_classic_tool.glyphs import ReadResult
            return ReadResult(self.price_text, 1)
        return res


class _PriceOcr:
    def __init__(self, price_text):
        self.price_text = price_text

    def read(self, crop):
        return self.price_text


@pytest.mark.parametrize("ocr_text", [
    "1 for 300 mesos",        # dropped digit
    "1 for 3,00 mesos",       # dropped digit, bad grouping
    "1 for ,000 mesos",       # leading comma
    "1 for 3,,000 mesos",     # doubled comma
    "1 for 2,500 mesos",      # digit disagrees with glyph read
    "1 for 3 000 mesos",      # gap collapses to 3000 but comma is missing
])
def test_misread_fallback_price_never_profits(layout, anchor, glyphs, catalog, frame2, ocr_text):
    sc = Scanner(ShopLocator(anchor), layout, _ForcedPriceGlyphs(glyphs, "1 for 3,?00 mesos", drop="8"),
                 catalog, Settings(), _PriceOcr(ocr_text))
    res = sc.scan(frame2)
    assert res.verdicts
    assert all(v.status != "profit" for v in res.verdicts)
    assert all(v.status == "unknown" for v in res.verdicts)


def test_agreeing_fallback_price_still_accepted(layout, anchor, glyphs, catalog, frame2):
    sc = Scanner(ShopLocator(anchor), layout, _ForcedPriceGlyphs(glyphs, "1 for 3,000 mesos ??"),
                 catalog, Settings(), _PriceOcr("1 for 3,000 mesos"))
    v0 = sc.scan(frame2).verdicts[0]
    assert v0.status == "profit" and v0.listing.price == 3000


_SCANNER_REJECT = [
    ("1 for 1?? ?????", "1 for 1 mesos"),
    ("1 for 4?? ?????", "1 for 4 mesos"),
    ("1 for ?", "1 for 5 mesos"),
    ("1 for ??", "1 for 5 mesos"),
    ("?", "1 for 5 mesos"),
    ("??", "1 for 5 mesos"),
    ("1 for 1?,000 mesos", "1 for 1,000 mesos"),
    ("1 for ?? mesos", "1 for 88 mesos"),
    ("? for 1,000 mesos", "1 for 1,000 mesos"),
    ("1 for 1?? ?????", "1 for 100 mesos"),
    ("1 for ??,000 mesos", "1 for 8,000 mesos"),   # true 88,000
    ("1 for 5?? mesos", "1 for 58 mesos"),
    ("1 for ?,500 mesos", "1 for 0,500 mesos"),
    ("1 for ?00 mesos", "1 for 000 mesos"),
    ("1 ? for 5,000 mesos", "180 for 5,000 mesos"),
    ("? 1 for 3,000 mesos", "11 for 3,000 mesos"),
    ("1 for ?,000 mesos", "1 for 3,000 mesos"),    # 8 read as 3
]


@pytest.mark.parametrize("glyph_text,ocr_text", _SCANNER_REJECT)
def test_dropped_digit_pairs_stay_unknown(layout, anchor, glyphs, frame2, glyph_text, ocr_text):
    cheap = Catalog([Item(1000 + i, r["name"], 1000000) for i, r in enumerate(_labels(2))
                     if isinstance(r, dict)], 0)
    sc = Scanner(ShopLocator(anchor), layout, _ForcedPriceGlyphs(glyphs, glyph_text, drop="8"),
                 cheap, Settings(), _PriceOcr(ocr_text))
    res = sc.scan(frame2)
    assert res.verdicts
    assert all(v.status != "profit" for v in res.verdicts)
    assert all(v.status == "unknown" for v in res.verdicts)


def test_agreeing_pair_with_gap_gets_ocr_price(layout, anchor, glyphs, catalog, frame2):
    # a glyph set without an "8" template: OCR may fill exactly the digits it cannot read
    sc = Scanner(ShopLocator(anchor), layout, _ForcedPriceGlyphs(glyphs, "1 for 3,?00 mesos", drop="8"),
                 catalog, Settings(), _PriceOcr("1 for 3,800 mesos"))
    v0 = sc.scan(frame2).verdicts[0]
    assert (v0.listing.qty, v0.listing.price, v0.listing.uncertain) == (1, 3800, False)


def test_scanner_unknown_chars_from_glyphset(layout, anchor, glyphs, catalog):
    sc = make(layout, anchor, glyphs, catalog)
    assert sc._unknown_chars == ""  # every digit has a template since the 1228-base retrain
    sc = Scanner(ShopLocator(anchor), layout, _ForcedPriceGlyphs(glyphs, "", drop="8"), catalog, Settings(), None)
    assert sc._unknown_chars == "8"


def test_glyphs_without_chars_fail_closed(layout, anchor, glyphs, frame2):
    class NoChars:
        def __init__(self, real):
            self.real = real

        def read(self, ink):
            res = self.real.read(ink)
            if " for " in res.text:
                from maplestory_classic_tool.glyphs import ReadResult
                return ReadResult("1 for ?,000 mesos", 1)
            return res

    cheap = Catalog([Item(1000 + i, r["name"], 1000000) for i, r in enumerate(_labels(2))
                     if isinstance(r, dict)], 0)
    sc = Scanner(ShopLocator(anchor), layout, NoChars(glyphs), cheap, Settings(),
                 _PriceOcr("1 for 3,000 mesos"))
    assert sc._unknown_chars == ""
    res = sc.scan(frame2)
    assert res.verdicts and all(v.status == "unknown" for v in res.verdicts)


def test_shop_id_groups_fixtures(layout, anchor, glyphs, catalog):
    """Same seller + title -> same identity across captures (scrolled or not); others differ."""
    ids = {}
    for n in range(1, 8):
        sc = make(layout, anchor, glyphs, catalog)
        res = sc.scan(_frame(f"shop{n}.png", layout))
        assert res.anchor is not None and res.shop_id is not None
        ids[n] = res.shop_id
    assert ids[1] == ids[7]
    assert ids[4] == ids[5] == ids[6]
    assert len({ids[1], ids[2], ids[3], ids[4]}) == 4


def test_shop_id_none_without_anchor(layout, anchor, glyphs, catalog):
    res = make(layout, anchor, glyphs, catalog).scan(_frame("noshop_fm.png", layout))
    assert res.anchor is None and res.shop_id is None


def test_shop_id_on_unchanged_frame(layout, anchor, glyphs, catalog, frame2):
    sc = make(layout, anchor, glyphs, catalog)
    a = sc.scan(frame2)
    b = sc.scan(frame2)
    assert not b.changed and b.shop_id == a.shop_id


def test_layout_has_window_and_id_boxes(layout):
    assert layout.window_box == (-141, -58, 523, 406)
    assert len(layout.shop_id_boxes) == 2


@pytest.mark.parametrize("size", [(2560, 1440), (1920, 1080)])
def test_resized_capture_confirms_1366_and_reads_same_rows(layout, anchor, glyphs, catalog, frame2, size):
    """A 16:9 client of another size: the resolver probes, confirms (1366, 768), same rows read."""
    import queue

    from maplestory_classic_tool.live import LiveLoop

    full = cv2.resize(cv2.imread(str(FIXTURES / "shop2.png"), cv2.IMREAD_GRAYSCALE), size,
                      interpolation=cv2.INTER_AREA)
    expected = [(v.item.name, v.listing.qty, v.listing.price, v.status)  # the list shows catalog names
                for v in make(layout, anchor, glyphs, catalog).scan(frame2).verdicts]
    settings = Settings(base_sizes={})
    saved = []
    ui = queue.Queue()
    loop = LiveLoop(make(layout, anchor, glyphs, catalog), layout, settings,
                    lambda t: (0, 0, size[0], size[1]), lambda r, b: to_base(full, b), ui, queue.Queue(),
                    save_settings=lambda s: saved.append(dict(s.base_sizes)))
    for _ in range(6):
        loop.step()
    key = f"{size[0]}x{size[1]}"
    assert settings.base_sizes == {key: [1366, 768]} and saved == [{key: [1366, 768]}]
    got = [(r.name, r.qty, r.price, r.status) for r in loop.listing.rows]
    assert got == expected
    panels = []
    while not ui.empty():
        m = ui.get_nowait()
        if m[0] == "panel":
            panels.append(m[1])
    assert panels and panels[-1].title == "6 items · 1 deals"
    assert panels[-1].x < round(422 * size[0] / 1366)  # left of the shop window on screen


@pytest.mark.parametrize("size", [None, (2560, 1440)])
def test_cli_image_mode_draws_side_panel(tmp_path, catalog, size):
    src = FIXTURES / "shop2.png"
    if size is not None:  # another 16:9 size: --image probes the base like live mode
        img = cv2.resize(cv2.imread(str(src)), size, interpolation=cv2.INTER_AREA)
        src = tmp_path / "shop2_small.png"
        cv2.imwrite(str(src), img)
    cat_path = tmp_path / "catalog.json"
    catalog.save(cat_path)
    out = tmp_path / "out.png"
    proc = subprocess.run(
        [sys.executable, "-m", "maplestory_classic_tool", "--image", str(src), "--out", str(out), "--catalog", str(cat_path)],
        capture_output=True, encoding="utf-8", cwd=ROOT, timeout=120, env=dict(__import__("os").environ,
                                                                       LOCALAPPDATA=str(tmp_path)))
    assert proc.returncode == 0, proc.stderr
    assert "base=1366x768" in proc.stdout and "6 items · 1 deals" in proc.stdout
    assert len([l for l in proc.stdout.splitlines() if l.startswith("row=")]) == 6
    res = cv2.imread(str(out))
    scale = res.shape[1] / 1366
    # the panel's white title strip (shop-window style) is just left of the shop window's top-left corner
    x, y = round((422 - 8 - 10) * scale), round((152 + 4 + 9) * scale)
    assert tuple(int(c) for c in res[y, x]) == (0xff, 0xff, 0xff)
    # the frame band above it is the shop frame's blue-grey
    from maplestory_classic_tool.draw import bgr
    from maplestory_classic_tool.sidelist import SHOP_FRAME
    assert tuple(int(c) for c in res[round((152 + 2.5) * scale), x]) == bgr(SHOP_FRAME)


def _loop_on(full, layout, scanner, settings):
    import queue

    from maplestory_classic_tool.live import LiveLoop
    h, w = full.shape
    return LiveLoop(scanner, layout, settings, lambda t: (4229, 381, w, h), lambda r, b: to_base(full, b),
                    queue.Queue(), queue.Queue(), save_settings=lambda s: None)


def test_windowed_2560x1600_confirms_1228_and_reads_rows(layout, anchor, glyphs, catalog):
    """Real 2560x1600 windowed capture: base is (1228, 768), not the 16:10 standard (1280, 800)."""
    from maplestory_classic_tool.fallback_ocr import WindowsOcr
    data = json.loads((FIXTURES / "windowed1600_shop.json").read_text(encoding="utf-8"))
    full = cv2.imread(str(FIXTURES / "windowed1600_shop.png"), cv2.IMREAD_GRAYSCALE)
    assert (full.shape[1], full.shape[0]) == tuple(data["client_size"]) == (2560, 1600)
    ocr = WindowsOcr.create()
    settings = Settings(base_sizes={})
    own = Catalog([Item(2000 + i, x["name"], 1) for i, x in enumerate(data["rows"])], 0)
    loop = _loop_on(full, layout, make(layout, anchor, glyphs, own, fallback=ocr), settings)
    loop.step()  # the primary candidate confirms on the first frame with a shop
    assert settings.base_sizes == {"2560x1600": [1228, 768]}
    loop.step()  # rows show once seen in two observes
    rows = loop.listing.rows
    assert [(r.qty, r.price) for r in rows] == [(x["qty"], x["price"]) for x in data["rows"]]
    assert [r.name for r in rows] == [x["name"] for x in data["rows"]]  # catalog names, all listed


def test_windowed_scores_pick_true_width(layout, anchor):
    """Measured neighbourhood: 1228 is the maximum of the family on the real capture."""
    from maplestory_classic_tool.layout import base_family
    full = cv2.imread(str(FIXTURES / "windowed1600_shop.png"), cv2.IMREAD_GRAYSCALE)
    loc = ShopLocator(anchor)
    scores = {b: loc.score(to_base(full, b)) for b in base_family(2560, 1600) + [(1280, 800)]}
    assert max(scores, key=scores.get) == (1228, 768) and scores[(1228, 768)] > 0.99
    assert scores[(1280, 800)] < 0.6


def test_4k_shop2_confirms_1366(layout, anchor, glyphs, catalog):
    full = cv2.imread(str(FIXTURES / "shop2.png"), cv2.IMREAD_GRAYSCALE)
    settings = Settings(base_sizes={})
    loop = _loop_on(full, layout, make(layout, anchor, glyphs, catalog), settings)
    loop.step()
    loop.step()
    assert settings.base_sizes == {"3840x2160": [1366, 768]}
    assert len(loop.listing.rows) == 6


def test_cli_image_mode_windowed_capture(tmp_path, catalog):
    cat_path = tmp_path / "catalog.json"
    catalog.save(cat_path)
    (tmp_path / "maplestory_classic_tool").mkdir()
    (tmp_path / "maplestory_classic_tool" / "settings.json").write_text('{"base_sizes": {}}', encoding="utf-8")  # no seed
    proc = subprocess.run(
        [sys.executable, "-m", "maplestory_classic_tool", "--image", str(FIXTURES / "windowed1600_shop.png"),
         "--catalog", str(cat_path)], capture_output=True, encoding="utf-8", cwd=ROOT, timeout=120,
        env=dict(__import__("os").environ, LOCALAPPDATA=str(tmp_path)))
    assert proc.returncode == 0, proc.stderr
    assert "base=1228x768" in proc.stdout
    assert len([l for l in proc.stdout.splitlines() if l.startswith("row=")]) == 6



def _id_scanner(boxes):
    from types import SimpleNamespace as NS
    lay = NS(shop_id_boxes=boxes)
    sc = Scanner.__new__(Scanner)
    sc.layout = lay
    return sc


def test_shop_id_clips_partially_offframe_boxes():
    """A title box partly above the frame still contributes its visible part (no collision)."""
    sc = _id_scanner(((0, -10, 20, 14), (0, 10, 20, 5)))
    a = np.full((40, 40), 255, np.uint8)
    b = a.copy()
    b[1, 5] = 0  # differs only in the visible part of the partly off-frame box
    assert sc._shop_id(a, (0, 5)) is not None
    assert sc._shop_id(a, (0, 5)) != sc._shop_id(b, (0, 5))
    # same visible content, different clipping -> different identity
    assert sc._shop_id(a, (0, 5)) != sc._shop_id(a, (0, 6))


def test_shop_id_none_when_boxes_fully_offframe():
    sc = _id_scanner(((0, -30, 20, 10), (0, -20, 20, 5)))
    assert sc._shop_id(np.full((40, 40), 255, np.uint8), (0, 5)) is None



class _ForcedNameGlyphs:
    """Real reads, but the first row's name reads with one unknown glyph."""
    def __init__(self, real):
        self.real = real

    def chars(self):
        return self.real.chars()

    def read(self, ink):
        res = self.real.read(ink)
        if res.text == "Wolbi Throwing Stars":
            from maplestory_classic_tool.glyphs import ReadResult
            return ReadResult("Wolbi Thr?wing Stars", 1)
        return res


class _NameOcr:
    def __init__(self, text):
        self.text = text

    def read(self, crop):
        return self.text


@pytest.mark.parametrize("ocr_name,expected", [
    ("Wolbi Throwing Stars", "Wolbi Throwing Stars"),    # agrees: accepted
    ("Ilbi Throwing Stars", "Wolbi Thr?wing Stars"),     # disagrees: glyph text kept
])
def test_name_fallback_needs_agreement(layout, anchor, glyphs, catalog, frame2, ocr_name, expected):
    sc = Scanner(ShopLocator(anchor), layout, _ForcedNameGlyphs(glyphs), catalog, Settings(), _NameOcr(ocr_name))
    v0 = sc.scan(frame2).verdicts[0]
    assert v0.listing.name == expected


STARS_CAT = Catalog([Item(297, "Kumbi Throwing Stars", 1500, True, 700), Item(417, "Silver Ore", 80),
                     Item(4000021, "Leather", 50), Item(4020001, "Emerald Ore", 150)], 0)


def _stars_frame():
    full = cv2.imread(str(FIXTURES / "windowed1600_stars.png"), cv2.IMREAD_GRAYSCALE)
    return to_base(full, (1228, 768))


def test_stars_fixture_star_rows_are_one_set_each(layout, anchor, glyphs):
    res = make(layout, anchor, glyphs, STARS_CAT).scan(_stars_frame())
    by_row = {v.listing.row: v for v in res.verdicts}
    assert sorted(by_row) == [0, 1, 2, 3, 4, 5]
    for r in (3, 4, 5):
        v = by_row[r]
        assert (v.item.id, v.listing.qty, v.listing.price) == (297, 700, 669999)
        assert (v.npc_total, v.status) == (1500, "no_profit")
    assert by_row[1].item.id == 417 and by_row[1].listing.price == 88 and by_row[1].status == "no_profit"


class _CursorGlyphs:
    """Row 0 reads like a cursor overlaps it: "??" after the name; optionally an unknown price digit."""
    def __init__(self, real, price_unknown):
        self.real, self.price_unknown = real, price_unknown

    def chars(self):
        return self.real.chars() - {"8"}

    def read(self, ink):
        from maplestory_classic_tool.glyphs import ReadResult
        res = self.real.read(ink)
        if res.text == "Wolbi Throwing Stars":
            return ReadResult("Wolbi Throwing Stars ??", 2)
        if self.price_unknown and res.text == "1 for 3,000 mesos":
            return ReadResult("1 for 3,?00 mesos", 1)
        return res


@pytest.mark.parametrize("price_unknown,expected", [(True, "unknown"), (False, "profit")])
def test_cursor_overlap_needs_fully_known_price(layout, anchor, glyphs, catalog, frame2, price_unknown, expected):
    fb = FakeOcr("Wolbi Throwing Stars II", "1 for 3,800 mesos")  # name agrees with "??" -> accepted
    if not price_unknown:
        fb = _NameOcr("Wolbi Throwing Stars II")
    sc = Scanner(ShopLocator(anchor), layout, _CursorGlyphs(glyphs, price_unknown), catalog, Settings(), fb)
    v0 = sc.scan(frame2).verdicts[0]
    assert v0.listing.name == "Wolbi Throwing Stars II"
    assert v0.status == expected


def test_layout_icon_box_measured_on_every_shop_fixture(layout, anchor):
    """icon_box = the item cell left of the name (image + stock number), with its 1 px border:
    border columns/rows are darker than the light separators just outside the box."""
    assert layout.icon_box == (-37, 3, 35, 35)
    bx, by, bw, bh = layout.icon_box
    for meta in sorted(FIXTURES.glob("*.json")):
        data = json.loads(meta.read_text(encoding="utf-8"))
        if not data["shop"]:
            continue
        base = tuple(data.get("base_size", (1366, 768)))
        g = to_base(cv2.imread(str(meta.with_suffix(".png")), cv2.IMREAD_GRAYSCALE), base).astype(int)
        ax, ay = ShopLocator(anchor).locate(g.astype(np.uint8))
        left, outside_l, right_out, top, outside_t, bottom_out = [], [], [], [], [], []
        for r in range(layout.rows):
            ox, oy = layout.row_origin((ax, ay), r)
            mid_y, mid_x = oy + by + bh // 2, ox + bx + bw // 2
            left.append(g[mid_y, ox + bx]); outside_l.append(g[mid_y, ox + bx - 1])
            right_out.append(g[mid_y, ox + bx + bw])
            top.append(g[oy + by, mid_x]); outside_t.append(g[oy + by - 1, mid_x])
            bottom_out.append(g[oy + by + bh, mid_x])
        med = lambda xs: int(np.median(xs))  # noqa: E731  (a row can be selected/highlighted)
        assert med(left) < med(outside_l) - 15 and med(right_out) >= 235, meta.name
        assert med(top) < med(outside_t) - 15 and med(bottom_out) >= 235, meta.name


def test_cli_image_mode_stars_panel_has_icons(tmp_path):
    cat_path = tmp_path / "catalog.json"
    STARS_CAT.save(cat_path)
    out = tmp_path / "out.png"
    (tmp_path / "maplestory_classic_tool").mkdir()
    (tmp_path / "maplestory_classic_tool" / "settings.json").write_text('{"base_sizes": {}}', encoding="utf-8")
    proc = subprocess.run(
        [sys.executable, "-m", "maplestory_classic_tool", "--image", str(FIXTURES / "windowed1600_stars.png"), "--out", str(out),
         "--show-all", "--catalog", str(cat_path)], capture_output=True, encoding="utf-8", cwd=ROOT, timeout=120,
        env=dict(__import__("os").environ, LOCALAPPDATA=str(tmp_path)))
    assert proc.returncode == 0, proc.stderr
    assert "base=1228x768" in proc.stdout
    assert "panel: 6 items · 1 deals" in proc.stdout  # Emerald Ore 150 > 99
    stars = [l for l in proc.stdout.splitlines() if l.startswith("row=") and "Kumbi" in l]
    assert len(stars) == 3 and all("npc_total=1500 " in l and "status=no_profit" in l for l in stars)
    m = [l for l in proc.stdout.splitlines() if l.startswith("drawn_rect=")]
    x, y, w, h = (int(v) for v in m[0].split("=")[1].split(","))
    img = cv2.imread(str(out))
    panel = img[y:y + h, x:x + w]
    # icon cells are light (the game's cream cell background), left of the rows under the title strip
    scale = 2560 / 1228
    col = panel[round(scale * (6 + 14)):, round(scale * 6):round(scale * (6 + 35))]  # icon column under the title
    assert (col.min(axis=2) > 180).mean() > 0.3



def test_shop4_truncated_axe_scroll_shows_ocr_text(layout, anchor, glyphs):
    """shop4 row 3 "Two-handed Axe Attack S..." matches four same-price tiers: shown as read."""
    from maplestory_classic_tool.sidelist import ShopListing
    rows = [r for r in _labels(4) if isinstance(r, dict) and not r["name"].startswith("Two-handed")]
    tiers = ["Chaos", "Greater", "Intermediate", "Lesser"]
    cat = Catalog([Item(13 + i, f"Two-handed Axe Attack Scroll: {t}", 1) for i, t in enumerate(tiers)]
                  + [Item(500 + i, r["name"], 1) for i, r in enumerate(rows)], 0)
    res = make(layout, anchor, glyphs, cat).scan(_frame("shop4.png", layout))
    v3 = {v.listing.row: v for v in res.verdicts}[3]
    assert v3.status == "no_profit" and v3.display == "Two-handed Axe Attack S..."
    sl = ShopListing(min_seen=1)
    sl.observe(res.shop_id, res.verdicts, 0.0)
    names = [r.name for r in sl.rows]
    assert "Two-handed Axe Attack S..." in names
    assert not any("Chaos" in n for n in names)



def _narrow_panel(x, grow_left, w=60):
    from maplestory_classic_tool.sidelist import Panel, PanelRow
    row = PanelRow("A Rather Long Item Name", "1 for 3,000 mesos", "+12,345", True,
                   "+12,345  A Rather Long Item Name  1 for 3,000")
    return Panel(x, 50, w, 90, "3 items · 1 deals", (row,), scale=2.0, row_h=34, grow_left=grow_left)


def test_draw_panel_grows_left_keeping_the_shop_side_edge():
    from maplestory_classic_tool.__main__ import draw_panel
    from maplestory_classic_tool.draw import CvRenderer
    img = np.zeros((300, 1600, 3), np.uint8)
    x, y, w, h = draw_panel(img, _narrow_panel(1000, True))
    assert x + w == 1060 and x < 1000  # right edge (towards the shop) fixed
    m = CvRenderer(img).measure
    assert w >= m("A Rather Long Item Name", 24, False) + m("+12,345", 20, True)
    assert tuple(int(c) for c in img[100, 1064]) == (0, 0, 0)  # nothing past the right edge (+ border)
    x2, _, w2, _ = draw_panel(np.zeros((300, 1600, 3), np.uint8), _narrow_panel(1000, False))
    assert x2 == 1000 and w2 == w  # flipped right of the shop: grows right


def test_draw_panel_partly_outside_the_image_is_clipped():
    from maplestory_classic_tool.__main__ import draw_panel
    img = np.zeros((300, 400, 3), np.uint8)
    x, _, w, _ = draw_panel(img, _narrow_panel(100, True))
    assert x < 0 and x + w == 160  # right edge still fixed at 160; the rest is clipped, no error


@pytest.mark.parametrize("name,base", [("windowed1600_stars", 1228), ("windowed1600_shop", 1228), ("shop2", 1366)])
def test_cli_image_panel_ends_before_shop_window(tmp_path, name, base, layout, anchor):
    cat_path = tmp_path / "catalog.json"
    rows = json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))["rows"]
    Catalog([Item(3000 + i, r["name"].rstrip("."), 1) for i, r in enumerate(rows)]
            + list(STARS_CAT.items), 0).save(cat_path)
    (tmp_path / "maplestory_classic_tool").mkdir()
    (tmp_path / "maplestory_classic_tool" / "settings.json").write_text('{"base_sizes": {}}', encoding="utf-8")
    proc = subprocess.run(
        [sys.executable, "-m", "maplestory_classic_tool", "--image", str(FIXTURES / f"{name}.png"), "--out", str(tmp_path / "o.png"),
         "--show-all", "--catalog", str(cat_path)], capture_output=True, encoding="utf-8", cwd=ROOT, timeout=120,
        env=dict(__import__("os").environ, LOCALAPPDATA=str(tmp_path)))
    assert proc.returncode == 0, proc.stderr
    drawn = [l for l in proc.stdout.splitlines() if l.startswith("drawn_rect=")]
    x, y, w, h = (int(v) for v in drawn[0].split("=")[1].split(","))
    full = cv2.imread(str(FIXTURES / f"{name}.png"), cv2.IMREAD_GRAYSCALE)
    scale = full.shape[1] / base
    ax, ay = ShopLocator(anchor).locate(to_base(full, (base, 768)))
    wx = layout.window_rect((ax, ay))[0]
    assert x >= 0 and (x + w) / scale <= wx - 8 + 0.5, (x, w, wx * scale)


# --- hover over the shop's buttons / cursor over the title -------------------------------------

def _hovered(frame, anchor_xy, shape):
    """The button block (the locator's template) changed as when the mouse hovers a button."""
    f = frame.copy()
    x, y = anchor_xy
    h, w = shape
    f[y:y + h, x:x + w] = 255 - f[y:y + h, x:x + w]
    return f


def _title_box(layout, anchor_xy):
    x, y, w, h = layout.shop_id_boxes[0]
    return anchor_xy[0] + x, anchor_xy[1] + y, w, h


def test_hovered_buttons_keep_the_shop_while_title_visible(layout, anchor, glyphs, catalog, frame2):
    sc = make(layout, anchor, glyphs, catalog)
    first = sc.scan(frame2)
    hovered = _hovered(frame2, first.anchor, anchor.shape)
    assert ShopLocator(anchor).locate(hovered) is None  # the template really fails
    held = sc.scan(hovered)
    assert held.anchor == first.anchor and held.verdicts == first.verdicts and not held.changed
    assert held.shop_id is None  # held frames are never merged into the list
    again = sc.scan(frame2)
    assert again.anchor == first.anchor and again.shop_id == first.shop_id


def test_shop_gone_when_title_also_changes(layout, anchor, glyphs, catalog, frame2):
    sc = make(layout, anchor, glyphs, catalog)
    first = sc.scan(frame2)
    f = _hovered(frame2, first.anchor, anchor.shape)
    x, y, w, h = _title_box(layout, first.anchor)
    f[y:y + h, x:x + w] = 0  # window closed: the title strip is gone too
    res = sc.scan(f)
    assert res.anchor is None and res.verdicts == []


def test_no_hold_without_a_previous_shop(layout, anchor, glyphs, catalog, frame2):
    sc = make(layout, anchor, glyphs, catalog)
    pos = ShopLocator(anchor).locate(frame2)
    assert sc.scan(_hovered(frame2, pos, anchor.shape)).anchor is None


def test_cursor_over_title_keeps_shop_identity(layout, anchor, glyphs, catalog, frame2):
    sc = make(layout, anchor, glyphs, catalog)
    first = sc.scan(frame2)
    x, y, w, h = _title_box(layout, first.anchor)
    f = frame2.copy()
    f[y + 2:y + 16, x + 40:x + 52] = 0  # the game's cursor sprite over the title text
    assert make(layout, anchor, glyphs, catalog).scan(f).shop_id != first.shop_id  # the raw hash differs
    assert sc.scan(f).shop_id == first.shop_id  # same shop while it stays open


def test_identity_recomputed_after_shop_closed(layout, anchor, glyphs, catalog, frame2):
    sc = make(layout, anchor, glyphs, catalog)
    first = sc.scan(frame2)
    assert sc.scan(_frame("noshop_fm.png", layout)).anchor is None
    other = sc.scan(_frame("shop3.png", layout))
    assert other.shop_id is not None and other.shop_id != first.shop_id


def test_dexterity_potion_reads_without_ocr(layout, anchor, glyphs):
    """shop8 (live 4K capture): x/y drawn joined as "xt" and "ty"; faded sold-out rows are never read."""
    cat = Catalog([Item(1, "Emerald Earrings", 4500), Item(2, "Red Cross Earrings", 3750),
                   Item(3, "Dexterity Potion", 250)], 0)
    res = Scanner(ShopLocator(anchor), layout, glyphs, cat, Settings(), None).scan(_frame("shop8.png", layout))
    got = {v.listing.row: (v.listing.name, v.listing.qty, v.listing.price, v.status) for v in res.verdicts}
    assert got == {0: ("Emerald Earrings", 1, 22000, "no_profit"), 1: ("Red Cross Earrings", 1, 5000, "no_profit"),
                   4: ("Dexterity Potion", 1, 300, "no_profit")}


@pytest.mark.parametrize("name, row, item", [("shop9.png", 2, "Aquamarine Ore"), ("shop10.png", 0, "Adamantium Ore")])
def test_a_and_q_read_without_ocr(layout, anchor, glyphs, name, row, item):
    """shop9 / shop10 (live 4K captures): a stand-alone capital A and a q."""
    cat = Catalog([Item(1, item, 1)], 0)
    res = Scanner(ShopLocator(anchor), layout, glyphs, cat, Settings(), None).scan(_frame(name, layout))
    v = {v.listing.row: v for v in res.verdicts}[row]
    assert v.listing.name == item and v.item is not None and v.status != "unknown"


NEW_LETTERS = [("shop11.png", 3, "Elixir"), ("shop12.png", 2, "Jr. Necki Skin"), ("shop12.png", 3, "Evil Eye Tail"),
               ("shop13.png", 4, "Yellow Square"), ("shop14.png", 4, "Yellow Square"),
               ("shop15.png", 0, "Red Ninja Sandals"), ("shop16.png", 2, "Blue Qi Pao"), ("shop17.png", 1, "Zard"),
               ("shop17.png", 5, "White Choco Bunny"), ("shop18.png", 0, "Vaulter 2000"), ("shop18.png", 5, "Red Viper")]


@pytest.mark.parametrize("name, row, item", NEW_LETTERS)
def test_new_letters_read_without_ocr(layout, anchor, glyphs, name, row, item):
    """shop11-17 (live 4K captures): x, y, J, Y, j, Q, Z and V each read from the glyph set alone."""
    cat = Catalog([Item(1, item, 1)], 0)
    res = Scanner(ShopLocator(anchor), layout, glyphs, cat, Settings(), None).scan(_frame(name, layout))
    v = {v.listing.row: v for v in res.verdicts}[row]
    assert v.listing.name == item and v.item is not None and v.status != "unknown"
