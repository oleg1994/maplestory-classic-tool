"""EXP tracker: status-bar locate, EXP text parsing, validation/rates, formatting, box placement."""
from pathlib import Path

import cv2
import numpy as np
import pytest

from mapletool.exp import (ExpReader, ExpTracker, StatusBarLocator, build_exp_box, exp_box_to_screen,
                           format_amount, format_duration, load_statusbar, parse_exp)
from mapletool.layout import base_family, crop_to_base, to_base

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "mapletool" / "assets"
SHOPS = ROOT / "tests" / "fixtures" / "shops"
SB = load_statusbar(ASSETS / "layout.json")
TRUTH = {"noshop_npc": (170, 0.06), "windowed1600_shop": (134202, 50.32), "windowed1600_stars": (134202, 50.32)}
TRUTH.update({f"shop{n}": (151688, 56.88) for n in range(8, 19)}, noshop_grind=(173685, 65.13),
             noshop_partialinv=(198106, 74.29))  # live captures of 2026-11-07, same EXP
FIXTURES = sorted(p.stem for p in SHOPS.glob("*.png"))
OCR_UNREADABLE = {f"shop{n}" for n in range(8, 19)} | {"noshop_grind", "noshop_partialinv"}  # Windows OCR garbles these EXP texts (rejected by parse_exp); the glyph reader reads them


def fixture(name):
    img = cv2.imread(str(SHOPS / f"{name}.png"), cv2.IMREAD_COLOR)
    base = base_family(img.shape[1], img.shape[0])[0]
    gray = to_base(cv2.cvtColor(img, cv2.COLOR_BGR2GRAY), base)
    return img, base, gray


def locator():
    return StatusBarLocator(cv2.imread(str(ASSETS / "statusbar_anchor.png"), cv2.IMREAD_GRAYSCALE))


# --- parsing ---------------------------------------------------------------------------

@pytest.mark.parametrize("text,want", [
    ("234 [0.09%]", (234, 0.09)),
    ("134,202 [50.32%]", (134202, 50.32)),
    ("134,202[50.32%]", (134202, 50.32)),
    ("  1,234,567 [ 99.99 % ] ", (1234567, 99.99)),
    ("170 0.06%", (170, 0.06)),  # green brackets are dropped before OCR
    ("134,202 so.32%", (134202, 50.32)),  # 5/0 read as s/o next to the dropped bracket
    ("134.202 50.32%", (134202, 50.32)),  # thousands comma read as a dot
    ("0 [0.00%]", (0, 0.0)),
    ("234 (0.09%)", (234, 0.09)),
    ("12 [5%]", (12, 5.0)),
    ("1234567 [5.00%]", (1234567, 5.0)),  # commas dropped by OCR: up to 7 plain digits
])
def test_parse_exp_good(text, want):
    assert parse_exp(text) == want


@pytest.mark.parametrize("text", [
    "", "EXP.", "234", "0.09%", "234 [0.09]", "23410.09%)", "234 [100.00%]", "234 [0.099%]",
    "1,23 [5.00%]", "12,3456 [5.00%]", "234 [0.09%] 77", "abc [1.00%]", "2a4 [0.09%]", "134,202 so",
    "234 0.03%x", "12345678 [5.00%]",
])
def test_parse_exp_rejects_mangled(text):
    assert parse_exp(text) is None


# --- formatting --------------------------------------------------------------------------

def test_format_amount():
    assert format_amount(0) == "0"
    assert format_amount(999) == "999"
    assert format_amount(1_000) == "1.0K"
    assert format_amount(12_340) == "12.3K"
    assert format_amount(312_450) == "312K"
    assert format_amount(1_240_000) == "1.2M"
    assert format_amount(15_960_000) == "16.0M"
    assert format_amount(2_100_000_000) == "2.1B"


def test_format_duration():
    assert format_duration(None) == "—"
    assert format_duration(5) == "0m 05s"
    assert format_duration(725) == "12m 05s"
    assert format_duration(3600 + 23 * 60 + 59) == "1h 23m"
    assert format_duration(100 * 3600) == "99h+"


# --- tracker -----------------------------------------------------------------------------

T = 266_700  # level total implied by 134,202 at 50.32%


def sample(tr, t, exp, pct=None):
    if pct is None:
        pct = int(exp / T * 10000) / 100  # the game shows 2 decimals
    return tr.add(exp, pct, now=t)


def test_tracker_session_gain_and_rate_need_60s_and_two_increases():
    tr = ExpTracker()
    assert sample(tr, 0, 100_000)
    assert tr.rate(0) is None and tr.gained == 0
    sample(tr, 30, 110_000)
    assert tr.gained == 10_000
    assert tr.rate(30) is None  # < 60 s
    sample(tr, 59, 110_000)
    assert tr.rate(59) is None
    sample(tr, 60, 120_000)
    assert tr.rate(60) == pytest.approx(20_000 / 60 * 3600)
    assert tr.time_to_level(60) == pytest.approx((T - 120_000) / (20_000 / 60 * 3600) * 3600, rel=0.03)


def test_tracker_rate_one_increase_is_unknown():
    tr = ExpTracker()
    sample(tr, 0, 100_000)
    sample(tr, 120, 150_000)
    assert tr.rate(120) is None and tr.time_to_level(120) is None


def test_tracker_window_rate_uses_trailing_ten_minutes():
    tr = ExpTracker()
    sample(tr, 0, 10_000)
    sample(tr, 60, 100_000)  # fast start ...
    for i in range(1, 21):  # ... then 1,000 per minute for 20 minutes
        sample(tr, 60 + 60 * i, 100_000 + 1_000 * i)
    now = 60 + 60 * 20
    assert tr.rate(now) == pytest.approx(1_000 * 60, rel=0.01)
    assert tr.gained == 110_000


def test_tracker_misread_is_discarded():
    tr = ExpTracker()
    sample(tr, 0, 100_000)
    sample(tr, 10, 101_000)
    sample(tr, 20, 102_000)
    assert not tr.add(1_020_000, 38.24, now=30)  # extra digit: implied total disagrees
    assert not tr.add(92_000, 34.49, now=31)  # lower within the level (consistent total, but backwards)
    assert not tr.add(103_000, 99.0, now=32)
    assert not tr.add(103_000, 100.0, now=33)
    assert not tr.add(103_000, -1.0, now=34)
    assert tr.gained == 2_000
    assert sample(tr, 40, 103_000)
    assert tr.gained == 3_000


def test_tracker_low_percent_samples_checked_for_consistency():
    tr = ExpTracker()
    sample(tr, 0, 260_000)
    sample(tr, 10, 265_000)
    assert not tr.add(200, 0.07, now=20)  # level-up (pct drop > 50): held until confirmed
    assert tr.level_ups == 0
    assert tr.add(260, 0.09, now=30)  # confirms it; the new level's total is unknown
    assert tr.level_ups == 1
    # below 1% no total is estimated, but the range a 2-decimal percent allows must still overlap
    assert not tr.add(2_600_000, 0.09, now=40)
    assert tr.exp == 260


def test_tracker_level_up_wraps_with_previous_total():
    tr = ExpTracker()
    sample(tr, 0, 260_000)
    sample(tr, 60, 265_000)
    assert not tr.add(1_500, 0.56, now=120)  # level-up held until the next differing sample
    assert tr.level_ups == 0 and tr.gained == 5_000
    assert tr.add(3_000, 1.12, now=180)  # confirms: wrapped with the old total, then the new level's gain
    assert tr.gained == pytest.approx(5_000 + (T - 265_000) + 3_000, abs=T * 0.01)


def test_tracker_reseeds_after_three_consistent_rejections():
    """A first misread (or EXP lost on death) must not lock the tracker out forever."""
    tr = ExpTracker()
    tr.add(900_000, 50.32, now=0)  # misread first sample: implied total 1.79M
    for t, e in ((10, 134_202), (20, 134_500), (30, 134_800)):
        tr.add(e, round(e / T * 100, 2), now=t)
    assert tr.exp == 134_800
    assert sample(tr, 40, 135_000)
    assert tr.gained == 200  # only gains after the re-seed count


def test_tracker_idle_freezes_rate():
    tr = ExpTracker()
    for i in range(11):
        sample(tr, 30 * i, 100_000 + 5_000 * i)
    active = tr.rate(300)
    assert active == pytest.approx(600_000, rel=0.01)
    assert not tr.idle(300 + 299)
    assert tr.idle(300 + 300)
    frozen = tr.rate(300 + 300)
    assert tr.rate(300 + 3000) == frozen  # no decay while idle
    assert frozen > 0.5 * active
    sample(tr, 4000, 160_000)  # gaining again: no longer idle
    assert not tr.idle(4000)


def test_tracker_unchanged_samples_do_not_count_as_increases():
    tr = ExpTracker()
    for t in range(0, 120, 10):
        sample(tr, t, 100_000)
    sample(tr, 130, 101_000)
    assert tr.rate(130) is None  # one increase only


def test_time_to_level_none_without_rate():
    tr = ExpTracker()
    sample(tr, 0, 100_000)
    assert tr.time_to_level(0) is None


# --- status bar: locate + read on fixtures --------------------------------------------------

@pytest.mark.parametrize("name", FIXTURES)
def test_statusbar_located_on_every_fixture(name):
    _, base, gray = fixture(name)
    pos = locator().locate(gray)
    assert pos is not None, name
    # the bar is centred: the LV box sits 397 px left of the centre at both bases
    assert pos[0] == base[0] // 2 - 397 + 0 and pos[1] == 735, (name, pos)


def test_statusbar_not_found_on_blank_frame():
    assert locator().locate(np.zeros((768, 1366), np.uint8)) is None
    assert locator().locate(np.full((768, 1228), 128, np.uint8)) is None


def test_statusbar_locate_is_cached_and_reverified():
    _, _, gray = fixture("shop2")
    loc = locator()
    pos = loc.locate(gray)
    calls = []
    real = cv2.matchTemplate
    try:
        cv2.matchTemplate = lambda *a, **k: calls.append(1) or real(*a, **k)
        assert loc.locate(gray) == pos
        assert calls == []  # verified in place, no search
        moved = np.roll(gray, -3, axis=1)
        assert loc.locate(moved) == (pos[0] - 3, pos[1])
        assert calls  # full search on miss
    finally:
        cv2.matchTemplate = real


class FakeOcr:
    def __init__(self, text="234 0.09%"):
        self.text = text
        self.calls = []

    def read(self, gray):
        self.calls.append(gray.shape)
        return self.text


def test_reader_crops_exp_text_box_and_reads_glyphs_without_ocr():
    img, base, gray = fixture("shop2")
    ocr = FakeOcr("999 [9.99%]")
    asked = []
    r = ExpReader(locator(), SB, ocr)
    reading = r.read(gray, lambda box: asked.append(box) or crop_to_base(img, base, box))
    assert reading.anchor == (base[0] // 2 - 397, 735)
    assert asked == [r.text_box(reading.anchor)]
    assert (reading.exp, reading.pct, reading.source) == (234, 0.09, "glyphs")
    assert ocr.calls == []  # the pixel-font reader was sure: no OCR


def test_reader_falls_back_to_ocr_when_a_glyph_is_unknown():
    img, base, gray = fixture("shop2")
    ocr = FakeOcr("234 [0.09%]")

    def crop(box):
        bgr = crop_to_base(img, base, box)
        bgr[3:10, 0:9] = 230  # a solid block: no glyph looks like it
        return bgr
    reading = ExpReader(locator(), SB, ocr).read(gray, crop)
    assert ocr.calls == [(SB.exp_text_box[3], SB.exp_text_box[2])]
    assert (reading.exp, reading.pct, reading.source) == (234, 0.09, "ocr")


@pytest.mark.parametrize("name", FIXTURES)
def test_glyph_reader_reads_every_fixture_at_both_bases(name):
    img, base, gray = fixture(name)
    reading = ExpReader(locator(), SB, None).read(gray, lambda box: crop_to_base(img, base, box))
    assert (reading.exp, reading.pct) == TRUTH.get(name, (234, 0.09)), reading


@pytest.mark.parametrize("size", [(2560, 1440), (3440, 1935), (2732, 1536)])
def test_glyph_reader_other_16_9_client_sizes(size):
    img = cv2.resize(fixture("noshop_npc")[0], size, interpolation=cv2.INTER_AREA)
    base = base_family(*size)[0]
    gray = to_base(cv2.cvtColor(img, cv2.COLOR_BGR2GRAY), base)
    reading = ExpReader(locator(), SB, None).read(gray, lambda box: crop_to_base(img, base, box))
    assert (reading.exp, reading.pct) == (170, 0.06)


def test_read_glyphs_unknown_or_unsure_gives_none():
    from mapletool.exp import read_glyphs
    blank = np.full((12, 102, 3), 60, np.uint8)
    assert read_glyphs(blank) == ""
    blob = blank.copy()
    blob[3:10, 10:19] = 230
    assert read_glyphs(blob) is None


def test_read_glyphs_synthetic_eight():
    """No fixture shows an 8; its template follows the font's 0/3/9 strokes."""
    from mapletool.exp import EXP_GLYPHS, GLYPH_TOP, read_glyphs
    img = np.full((12, 40, 3), 60, np.uint8)
    x = 2
    for ch in "18":
        rows = EXP_GLYPHS[ch]
        for dy, row in enumerate(rows):
            for dx, c in enumerate(row):
                if c == "#":
                    img[GLYPH_TOP + dy, x + dx] = 200
        x += len(rows[0]) + 2
    assert read_glyphs(img) == "18"


def test_reader_without_bar_returns_none_and_never_ocrs():
    ocr = FakeOcr()
    r = ExpReader(locator(), SB, ocr)
    assert r.read(np.zeros((768, 1366), np.uint8), lambda box: None) is None
    assert ocr.calls == []


def test_reader_unparsable_text_keeps_anchor():
    img, base, gray = fixture("shop2")
    reading = ExpReader(locator(), SB, FakeOcr("23410.09%)")).read(
        gray, lambda b: np.full((b[3], b[2], 3), 60, np.uint8))  # nothing legible
    assert reading.anchor is not None and reading.exp is None


try:
    from mapletool.fallback_ocr import WindowsOcr
    _OCR = WindowsOcr.create()
except Exception:  # noqa: BLE001
    _OCR = None


@pytest.mark.skipif(_OCR is None, reason="Windows OCR unavailable")
@pytest.mark.parametrize("name", FIXTURES)
def test_windows_ocr_fallback_reads_fixture_exp(name):
    """The OCR fallback alone (the pixel-font reader bypassed) on the binarised crop: the right value or
    nothing (shop8's "151,688 [56.88%]" comes back as "ISI SS.88%", which the parser rejects), never a wrong one."""
    from mapletool.exp import exp_ink
    img, base, gray = fixture(name)
    anchor = locator().locate(gray)
    x, y, w, h = SB.exp_text_box
    bgr = crop_to_base(img, base, (anchor[0] + x, anchor[1] + y, w, h))
    got = parse_exp(_OCR.read(exp_ink(bgr)))
    assert got == TRUTH.get(name, (234, 0.09)) or (got is None and name in OCR_UNREADABLE)


# --- box placement ---------------------------------------------------------------------------

def test_exp_box_covers_the_lv_box_and_name_panel():
    anchor = (286, 735)
    tr = ExpTracker()
    sample(tr, 0, 134_202, 50.32)
    box = build_exp_box(anchor, SB, tr, now=0, frame_w=1366)
    assert (box.x, box.y, box.w, box.h) == (anchor[0], anchor[1] - 1, 207, 32)
    assert box.x + box.w <= anchor[0] + 207  # stops at the separator before HP
    assert box.y + box.h <= 768  # inside the frame
    assert len(box.lines) == 3
    texts = "".join(t for line in box.lines for t, _ in line)
    assert "EXP/h" in texts and "—" in texts and "Lv up" in texts and "Meso/h open inv" in texts
    assert box.tails == ((), (), ())  # no gain yet, not idle
    assert box.level_w == SB.level_box[2]


def test_exp_box_meso_line():
    from mapletool.exp import EXP_SUB, EXP_VALUE, MesoLine
    tr = ExpTracker()
    box = build_exp_box((286, 735), SB, tr, now=0, meso=MesoLine(True, 452_000, 45_300))
    assert box.lines[2] == (("Meso/h ", box.lines[0][0][1]), ("452K", EXP_VALUE))
    assert box.tails[2] == (("+45.3K", EXP_SUB),)
    box = build_exp_box((286, 735), SB, tr, now=0, meso=MesoLine(True))
    assert [t for t, _ in box.lines[2]] == ["Meso/h ", "—"] and box.tails[2] == ()


def test_exp_box_panel_is_blank_in_the_fixtures():
    """The panel rect is the LV box and the job / name panel: the bright separator is just right of it."""
    for name in ("shop2", "windowed1600_stars"):
        img, base, gray = fixture(name)
        anchor = locator().locate(gray)
        x, y, w, h = SB.panel
        bgr = crop_to_base(img, base, (anchor[0] + x, anchor[1] + y, w, h))
        assert bgr.shape[:2] == (h, w)
        sep = crop_to_base(img, base, (anchor[0] + x + w + 1, anchor[1] + 14, 1, 1))[0, 0]
        assert int(sep.max()) > 100 and int(bgr[14, -1].max()) < 70
        lv = crop_to_base(img, base, (anchor[0] + x, anchor[1] + 14, 1, 1))[0, 0]
        assert int(lv.max()) < 110  # the left edge is inside the bar (dark), not its light bevel (~170)


def test_exp_box_values_with_rate():
    tr = ExpTracker()
    sample(tr, 0, 100_000)
    sample(tr, 30, 110_000)
    sample(tr, 60, 120_000)
    box = build_exp_box((286, 735), SB, tr, now=60, frame_w=1366)
    flat = [t for line in box.lines for t, _ in line]
    assert "1.2M" in flat
    assert any(t.endswith("m") or t.endswith("s") for t in flat if t[0].isdigit())
    assert "+20.0K" in [t for tail in box.tails for t, _ in tail]


def test_exp_box_marks_idle():
    tr = ExpTracker()
    for i in range(5):
        sample(tr, 30 * i, 100_000 + 5_000 * i)
    box = build_exp_box((286, 735), SB, tr, now=2000, frame_w=1366)
    assert box.tails[1] and box.tails[1][0][0] == "idle"


def test_exp_box_to_screen_scales():
    tr = ExpTracker()
    box = build_exp_box((286, 735), SB, tr, now=0, frame_w=1366)
    s = exp_box_to_screen(box, (100, 50), 2.0)
    assert (s.x, s.y, s.w, s.h) == (100 + 2 * box.x, 50 + 2 * box.y, 2 * box.w, 2 * box.h)
    assert s.scale == 2.0 and s.lines == box.lines


def test_exp_prims_style_and_segments():
    from mapletool import draw
    from mapletool.exp import BAR_BOTTOM, BAR_TOP, EXP_LABEL, EXP_SUB, EXP_VALUE, exp_prims
    tr = ExpTracker()
    sample(tr, 0, 100_000)
    sample(tr, 30, 110_000)
    sample(tr, 60, 120_000)
    box = exp_box_to_screen(build_exp_box((286, 735), SB, tr, now=60), (0, 0), 2.0)
    prims = exp_prims(box, lambda t, px, b: len(t) * px * 0.5)
    grad = [q for q in prims if isinstance(q, draw.Gradient)]
    assert grad and (grad[0].top, grad[0].bottom) == (BAR_TOP, BAR_BOTTOM)
    assert (grad[0].x0, grad[0].y0) == (box.x, box.y)
    fill = [q for q in prims if isinstance(q, draw.Rect)]
    assert fill and fill[0].fill == BAR_BOTTOM and fill[0].y1 == box.y + box.h and fill[0].x1 == box.x + box.w
    texts = [q for q in prims if isinstance(q, draw.Text)]
    assert [t.text for t in texts[:3]] == ["LV", "EXP/h ", "1.2M"]
    assert texts[1].color == EXP_LABEL and texts[2].color == EXP_VALUE
    assert texts[1].x > texts[0].x and texts[0].y < texts[-1].y  # segments left to right, line 2 below
    tail = [t for t in texts if t.anchor == "e"]
    assert [t.text for t in tail] == ["+20.0K"] and tail[0].color == EXP_SUB
    assert tail[0].x <= box.x + box.w and tail[0].y == texts[0].y
    assert all(box.x <= t.x <= box.x + box.w for t in texts)


def test_exp_prims_drop_tail_that_does_not_fit():
    from mapletool import draw
    from mapletool.exp import exp_prims
    tr = ExpTracker()
    sample(tr, 0, 100_000)
    sample(tr, 30, 110_000)
    sample(tr, 60, 120_000)
    box = build_exp_box((286, 735), SB, tr, now=60)
    prims = exp_prims(box, lambda t, px, b: len(t) * 12.0)  # very wide text
    assert not [q for q in prims if isinstance(q, draw.Text) and q.anchor == "e"]


@pytest.mark.perf
def test_exp_read_cost_is_small():
    import statistics
    import time
    img, base, gray = fixture("windowed1600_stars")
    r = ExpReader(locator(), SB, None)
    r.read(gray, lambda box: crop_to_base(img, base, box))
    times = []
    for _ in range(30):
        t = time.perf_counter()
        r.read(gray, lambda box: crop_to_base(img, base, box))
        times.append(time.perf_counter() - t)
    assert statistics.median(times) * 1000 < 5  # measured ~1.4 ms; runs at most once per second



# --- fix round 1 ---------------------------------------------------------------------------------

def test_false_level_up_is_discarded():
    """One misread like 45,000 [4.50%] while at 745,000 [74.50%] must not add ~1,000,000 gain."""
    tr = ExpTracker()
    total = 1_000_000
    for i, e in enumerate((740_000, 742_000, 745_000)):
        assert tr.add(e, e / total * 100, now=10 * i)
    assert not tr.add(45_000, 4.50, now=30)  # misread: held, not counted
    assert tr.gained == 5_000
    assert tr.add(746_000, 74.60, now=40)  # the real value again: the held level-up is dropped
    assert tr.level_ups == 0 and tr.gained == 6_000 and tr.exp == 746_000


def test_real_level_up_committed_on_confirmation_with_exact_gain():
    tr = ExpTracker()
    total = 1_000_000
    assert tr.add(990_000, 99.00, now=0)
    assert tr.add(995_000, 99.50, now=10)
    assert not tr.add(2_000, 0.18, now=20)  # new level (total ~1.1M): held
    assert not tr.add(2_000, 0.18, now=21)  # the same reading again does not confirm or drop it
    assert tr.level_ups == 0 and tr.gained == 5_000
    assert tr.add(3_100, 0.28, now=30)  # differing, agreeing: commit
    assert tr.level_ups == 1 and tr.exp == 3_100
    assert tr.gained == 5_000 + (total - 995_000) + 2_000 + 1_100


def test_held_level_up_dropped_when_next_sample_disagrees():
    tr = ExpTracker()
    total = 1_000_000
    tr.add(990_000, 99.00, now=0)
    tr.add(995_000, 99.50, now=10)
    assert not tr.add(2_000, 0.18, now=20)
    assert not tr.add(1_000, 0.09, now=30)  # lower than the held sample: both are suspect
    assert tr.level_ups == 0 and tr.gained == 5_000 and tr.exp == 995_000


def test_rate_after_idle_counts_active_time_only():
    tr = ExpTracker()
    for i in range(11):  # 5,000 every 30 s for 5 minutes: 600K/h
        sample(tr, 30 * i, 100_000 + 5_000 * i)
    before = tr.rate(300)
    resume = 300 + 47 * 60
    sample(tr, resume, 155_000)  # first gain after 47 minutes idle
    assert not tr.idle(resume)
    assert tr.rate(resume) == pytest.approx(before, rel=0.25)
    eta = tr.time_to_level(resume)
    assert eta is not None and eta < 3600  # not "59h 50m"


def _eight(rows):
    from mapletool.exp import EXP_GLYPHS, GLYPH_TOP
    img = np.full((12, 40, 3), 60, np.uint8)
    x = 2
    for pattern in (EXP_GLYPHS["1"], rows):
        for dy, row in enumerate(pattern):
            for dx, c in enumerate(row):
                if c == "#":
                    img[GLYPH_TOP + dy, x + dx] = 200
        x += len(pattern[0]) + 2
    return img


@pytest.mark.parametrize("name,rows", [
    ("middle bar on row 2", (".###.", "#...#", ".###.", "#...#", "#...#", "#...#", ".###.")),
    ("middle bar on row 4", (".###.", "#...#", "#...#", "#...#", ".###.", "#...#", ".###.")),
    ("pinched", (".###.", "#...#", ".#.#.", "..#..", ".#.#.", "#...#", ".###.")),
])
def test_off_pattern_eights_are_never_read_as_another_digit(name, rows):
    from mapletool.exp import read_glyphs
    got = read_glyphs(_eight(rows))
    assert got in (None, "18"), (name, got)


def test_glyph_min_score_keeps_every_fixture_glyph():
    from mapletool.exp import GLYPH_MIN_SCORE
    assert GLYPH_MIN_SCORE == 0.75  # real fixture glyphs score >= 0.879


def test_first_glyph_eight_crop_is_saved_once(tmp_path, caplog):
    from mapletool import exp as expmod
    img, base, gray = fixture("shop2")
    r = ExpReader(locator(), SB, None, sample_dir=tmp_path)
    calls = []
    real = expmod.read_glyphs
    try:
        expmod.read_glyphs = lambda bgr: calls.append(1) or "18 0.08%"
        with caplog.at_level("INFO", logger="mapletool"):
            first = r.read(gray, lambda box: crop_to_base(img, base, box))
        out = tmp_path / "exp_glyph_8.png"
        assert (first.exp, first.pct) == (18, 0.08) and out.exists()
        assert cv2.imread(str(out)).shape[:2] == (SB.exp_text_box[3], SB.exp_text_box[2])
        assert any("exp_glyph_8" in rec.getMessage() for rec in caplog.records)
        stamp = out.stat().st_mtime_ns
        r.read(gray, lambda box: crop_to_base(img, base, box))
        assert out.stat().st_mtime_ns == stamp  # saved only once
    finally:
        expmod.read_glyphs = real


def test_no_sample_saved_without_an_eight(tmp_path):
    img, base, gray = fixture("shop2")
    ExpReader(locator(), SB, None, sample_dir=tmp_path).read(gray, lambda box: crop_to_base(img, base, box))
    assert not (tmp_path / "exp_glyph_8.png").exists()


def test_exp_lines_share_one_column_right_of_the_level():
    from mapletool import draw
    from mapletool.exp import exp_prims
    tr = ExpTracker()
    box = exp_box_to_screen(build_exp_box((286, 735), SB, tr, now=0), (0, 0), 2.0)
    box = type(box)(box.x, box.y, box.w, box.h, box.lines, box.scale, box.tails, "png", SB.level_box[2])
    prims = exp_prims(box, lambda t, px, b: len(t) * px * 0.5)
    texts = [q for q in prims if isinstance(q, draw.Text) and q.anchor == "w"]
    starts = {t.text: t.x for t in texts}
    assert starts["EXP/h "] == starts["Lv up "] == starts["Meso/h "]
    level = [q for q in prims if isinstance(q, draw.Image)][0]
    assert starts["LV"] < starts["EXP/h "] and level.x < starts["EXP/h "]
