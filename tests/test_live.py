import queue
import zlib

import numpy as np
import pytest

from maplestory_classic_tool.capture import find_client_rect
from maplestory_classic_tool.config import Settings
from maplestory_classic_tool.evaluate import ScanResult
from maplestory_classic_tool.hotkeys import vk
from maplestory_classic_tool.live import HEAL_EVERY, LiveLoop
from maplestory_classic_tool.model import Annotation, Item, Listing, Verdict
from maplestory_classic_tool.overlay import Label, to_screen


def test_find_client_rect_missing_window_none():
    assert find_client_rect("no such window 7f3a") is None


def test_to_screen_offsets():
    assert to_screen([Annotation(0, 10, 20, "NPC +5", "profit")], (100, 200), 1.0) == [
        Label(110, 220, "NPC +5", "#3ddc84")]
    assert to_screen([Annotation(0, 100, 40, "NPC ?", "unknown")], (0, 0), 2.811) == [
        Label(281, 112, "NPC ?", "#b0b0b0")]


def test_hotkey_vk_codes():
    assert vk("F8") == 0x77
    assert vk("F9") == 0x78
    assert vk("F1") == 0x70
    assert vk("F12") == 0x7B
    with pytest.raises(ValueError):
        vk("X")


class FakeLayout:
    base_size = (1366, 768)
    label_dx = 0
    window_box = (-141, -58, 523, 406)

    def row_origin(self, anchor, row):
        return anchor[0], anchor[1] + 40 * row

    def window_rect(self, a):
        return (a[0] - 141, a[1] - 58, 523, 406)


class FakeScanner:
    def __init__(self, results):
        self.results = list(results)
        self.resets = 0
        self.catalog = "old"
        self.layout = FakeLayout()

    def reset(self):
        self.resets += 1

    def scan(self, gray):
        return self.results.pop(0)


def make_loop(scanner, rect=(0, 0, 2732, 1536), settings=None, clock=None, score=None):
    state = {"rect": rect, "bases": [], "saved": [], "scored": []}
    ui, req = queue.Queue(), queue.Queue()

    def grab(r, base):
        state["bases"].append(base)
        return np.zeros((base[1], base[0]), np.uint8)
    kw = {"clock": clock} if clock else {}
    if score is not None:
        def score_fn(gray):
            state["scored"].append(gray.shape[1])
            return score(gray.shape[1])
        kw["score_fn"] = score_fn
        kw["regrab"] = lambda r, base: np.zeros((base[1], base[0]), np.uint8)
    loop = LiveLoop(scanner, scanner.layout, settings or Settings(), lambda t: state["rect"], grab, ui, req,
                    save_settings=lambda s: state["saved"].append(dict(s.base_sizes)), **kw)
    return loop, state, ui, req


class ScoreScanner(FakeScanner):
    """Finds the anchor whenever the frame's base width scores >= 0.92 (like ShopLocator)."""

    def __init__(self, score):
        super().__init__([])
        self.score = score

    def scan(self, gray):
        return profit_result() if self.score(gray.shape[1]) >= 0.92 else NONE


def widths(table, default=0.3):
    return lambda w: table.get(w, default)


def drain(q):
    out = []
    while not q.empty():
        out.append(q.get_nowait())
    return out


def verdict(row, name, price, status="profit", profit=5):
    npc = None if status == "unknown" else price + profit
    item = None if status == "unknown" else Item(zlib.crc32(name.encode()), name, 1)
    return Verdict(Listing(row, name, 1, price, status == "unknown"), item, npc,
                   profit if status != "unknown" else None, status)


def profit_result(changed=True, rows=None, shop="s1", anchor=(563, 210)):
    rows = rows if rows is not None else [verdict(0, "A", 10)]
    return ScanResult(anchor, rows, changed, shop)


NONE = ScanResult(None, [], False)


def test_rescan_request_resets_scanner():
    sc = FakeScanner([NONE])
    loop, _, _, req = make_loop(sc)
    req.put("rescan")
    loop.step()
    assert sc.resets == 1


def test_catalog_swap_resets_and_replaces():
    sc = FakeScanner([NONE])
    loop, _, _, req = make_loop(sc)
    req.put(("catalog", "new"))
    loop.step()
    assert sc.catalog == "new" and sc.resets == 1


def test_missing_window_posts_clear_and_idles():
    loop, _, ui, _ = make_loop(FakeScanner([]), rect=None)
    assert loop.step() == 0.25
    assert ("clear",) in drain(ui)


def test_interval_active_vs_idle():
    sc = FakeScanner([NONE, ScanResult((5, 6), [], True, "s")])
    loop, _, _, _ = make_loop(sc)
    assert loop.step() == 0.25
    assert loop.step() == 0.1


def test_panel_posted_then_cleared_without_anchor():
    sc = FakeScanner([profit_result(), profit_result(False), NONE])
    loop, _, ui, _ = make_loop(sc)
    loop.step()
    loop.step()  # rows show once seen in two observes
    msgs = drain(ui)
    assert msgs[-1][0] == "panel"
    p = msgs[-1][1]
    assert p.title == "1 items · 1 deals" and p.lines[0][0] == "+5  A  1 for 10"
    assert p.scale == pytest.approx(2732 / 1366)
    loop.step()
    assert drain(ui) == [("clear",)]


def test_clear_request_suppresses_until_list_changes():
    ab = [verdict(0, "A", 10), verdict(1, "B", 20)]
    sc = FakeScanner([profit_result(), profit_result(False), profit_result(False), profit_result(False),
                      profit_result(rows=ab), profit_result(rows=ab)])
    loop, _, ui, req = make_loop(sc)
    loop.step()
    loop.step()
    drain(ui)
    req.put("clear")
    loop.step()
    assert drain(ui) == [("clear",)]
    loop.step()
    assert drain(ui) == []
    loop.step()  # B seen once: pending, the visible list did not change
    assert drain(ui) == []
    loop.step()
    assert [m[0] for m in drain(ui)] == ["panel"]


def test_unchanged_frame_does_not_repost():
    sc = FakeScanner([profit_result(), profit_result(False), profit_result(False)])
    loop, _, ui, _ = make_loop(sc)
    loop.step()
    loop.step()
    drain(ui)
    loop.step()
    assert drain(ui) == []


def test_window_move_reposts_panel():
    sc = FakeScanner([profit_result(), profit_result(False), profit_result(False)])
    loop, state, ui, _ = make_loop(sc)
    loop.step()
    loop.step()  # the row is listed (its width is part of the panel's)
    x0 = drain(ui)[-1][1].x
    state["rect"] = (50, 60, 2732, 1536)
    loop.step()
    msgs = drain(ui)
    assert msgs and msgs[0][0] == "panel" and msgs[0][1].x == x0 + 50


def test_shop_window_drag_reposts_panel():
    sc = FakeScanner([profit_result(), profit_result(False), profit_result(anchor=(600, 220))])
    loop, _, ui, _ = make_loop(sc)
    loop.step()
    loop.step()
    x0 = drain(ui)[-1][1].x
    loop.step()
    assert drain(ui)[-1][1].x == x0 + 2 * 37


def test_state_counts_accumulated_deals_across_scroll():
    first = [verdict(0, "A", 10), verdict(1, "B", 10, "no_profit", -3)]
    second = [verdict(0, "B", 10, "no_profit", -3), verdict(1, "C", 30, profit=9)]
    states = []
    sc = FakeScanner([profit_result(rows=first), profit_result(rows=first), profit_result(rows=second),
                      profit_result(rows=second)])
    loop, _, ui, _ = make_loop(sc)
    loop._on_state = states.append
    for _ in range(4):
        loop.step()
    assert states[-1] == ("shop", 2)
    p = [m for m in drain(ui) if m[0] == "panel"][-1][1]
    assert p.title == "3 items · 2 deals"
    assert [t for t, _ in p.lines] == ["+9  C  1 for 30", "+5  A  1 for 10"]


def test_show_all_request_reposts_with_all_rows():
    rows = [verdict(0, "A", 10), verdict(1, "B", 10, "no_profit", -3)]
    sc = FakeScanner([profit_result(rows=rows), profit_result(rows=rows), profit_result(False, rows=rows)])
    loop, _, ui, req = make_loop(sc)
    loop.step()
    loop.step()
    assert len(drain(ui)[-1][1].lines) == 1
    req.put(("show_all", True))
    loop.step()
    msgs = drain(ui)
    assert msgs[-1][0] == "panel" and len(msgs[-1][1].lines) == 2


def test_absence_over_one_second_forgets_list():
    t = {"now": 0.0}
    a, b, c = (profit_result(rows=[verdict(0, n, 10)]) for n in "ABC")
    sc = FakeScanner([a, a, NONE, NONE, b, b, NONE, c, c])
    loop, _, ui, _ = make_loop(sc, clock=lambda: t["now"])
    for now in (0.0, 0.1, 0.5, 1.5, 1.6, 1.7):  # 1.5: > 1 s since A was last seen (0.1)
        t["now"] = now
        loop.step()
    assert [r.name for r in loop.listing.rows] == ["B"]
    for now in (2.0, 2.3, 2.4):  # brief absence
        t["now"] = now
        loop.step()
    assert [r.name for r in loop.listing.rows] == ["B", "C"]


def test_new_shop_identity_resets_list():
    a = profit_result(rows=[verdict(0, "A", 10)], shop="s1")
    z = profit_result(rows=[verdict(0, "Z", 10)], shop="s2")
    sc = FakeScanner([a, a, z, z])
    loop, _, ui, _ = make_loop(sc)
    for _ in range(4):
        loop.step()
    assert [r.name for r in loop.listing.rows] == ["Z"]
    assert drain(ui)[-1][1].title == "1 items · 1 deals"


def test_rescan_clears_list():
    sc = FakeScanner([profit_result(), NONE])
    loop, _, _, req = make_loop(sc)
    loop.step()
    req.put("rescan")
    loop.step()
    assert loop.listing.rows == []


def test_probing_round_robin_until_anchor_then_confirm_and_save():
    class Probe(FakeScanner):
        def scan(self, gray):
            if gray.shape == (768, 1230):
                return profit_result()
            return NONE
    sc = Probe([])
    settings = Settings(base_sizes={"3840x2160": [1366, 768]})
    loop, state, ui, _ = make_loop(sc, rect=(4229, 381, 2560, 1600), settings=settings)
    for _ in range(3):
        loop.step()
    # one candidate per poll: the 768 family, primary first
    assert state["bases"] == [(1228, 768), (1226, 768), (1230, 768)]
    assert settings.base_sizes["2560x1600"] == [1230, 768]
    assert state["saved"] == [{"3840x2160": [1366, 768], "2560x1600": [1230, 768]}]
    p = [m for m in drain(ui) if m[0] == "panel"][-1][1]
    assert p.scale == pytest.approx(2560 / 1230)

    class Never(Probe):
        def scan(self, gray):
            return NONE
    loop.scanner = Never([])
    for _ in range(5):  # confirmed: only that base from now on, even with no shop open
        loop.step()
    assert set(state["bases"][3:]) == {(1230, 768)}
    assert len(state["saved"]) == 1


def test_correct_setup_confirms_on_first_shop_frame():
    score = widths({1228: 0.9994, 1230: 0.9971, 1226: 0.97, 1227: 0.9707, 1229: 0.9711})
    settings = Settings(base_sizes={})
    loop, state, _, _ = make_loop(ScoreScanner(score), rect=(4229, 381, 2560, 1600), settings=settings,
                                  score=score)
    loop.step()
    assert state["bases"] == [(1228, 768)]
    assert settings.base_sizes == {"2560x1600": [1228, 768]}
    assert sorted(state["scored"]) == [1226, 1227, 1228, 1229, 1230]  # whole family scored on that frame


def test_best_pick_beats_first_neighbour_that_passed():
    # the probed primary (1228) passes 0.92, but 1230 is the true width
    score = widths({1228: 0.93, 1230: 0.999, 1229: 0.96, 1231: 0.97})
    settings = Settings(base_sizes={})
    loop, state, ui, _ = make_loop(ScoreScanner(score), rect=(4229, 381, 2560, 1600), settings=settings,
                                   score=score)
    loop.step()
    assert settings.base_sizes == {"2560x1600": [1230, 768]}
    assert not [m for m in drain(ui) if m[0] == "panel"]  # that frame was read at the wrong base
    loop.step()
    assert state["bases"][-1] == (1230, 768)
    assert [m for m in drain(ui) if m[0] == "panel"]


def test_probe_hit_below_confirm_min_keeps_probing():
    score = widths({1228: 0.93})
    settings = Settings(base_sizes={})
    loop, state, _, _ = make_loop(ScoreScanner(score), rect=(4229, 381, 2560, 1600), settings=settings,
                                  score=score)
    loop.step()
    loop.step()
    assert settings.base_sizes == {} and state["bases"] == [(1228, 768), (1226, 768)]


def test_self_heal_when_shop_appears():
    score = widths({1226: 0.96, 1228: 0.999})
    settings = Settings(base_sizes={"2560x1600": [1226, 768]})  # wrong but still matching cached value
    loop, state, _, _ = make_loop(ScoreScanner(score), rect=(4229, 381, 2560, 1600), settings=settings,
                                  score=score)
    loop.step()
    assert settings.base_sizes["2560x1600"] == [1228, 768]
    assert state["saved"] == [{"2560x1600": [1228, 768]}]
    loop.step()
    assert state["bases"][-1] == (1228, 768)


def test_no_heal_within_margin():
    score = widths({1228: 0.9994, 1230: 0.9971})
    settings = Settings(base_sizes={"2560x1600": [1228, 768]})
    loop, state, _, _ = make_loop(ScoreScanner(score), rect=(4229, 381, 2560, 1600), settings=settings,
                                  score=score)
    for _ in range(3):
        loop.step()
    assert settings.base_sizes["2560x1600"] == [1228, 768] and state["saved"] == []
    assert len(state["scored"]) == 5  # family scored once, when the shop appeared; not every frame


def test_self_heal_from_never_matching_cache_by_idle_probe():
    score = widths({1228: 0.999})
    settings = Settings(base_sizes={"2560x1600": [1280, 800]})  # e.g. a stale value from an older build
    loop, state, _, _ = make_loop(ScoreScanner(score), rect=(4229, 381, 2560, 1600), settings=settings,
                                  score=score)
    for _ in range(HEAL_EVERY):
        loop.step()
    assert settings.base_sizes["2560x1600"] == [1228, 768]
    loop.step()
    assert state["bases"][-1] == (1228, 768)


def test_idle_heal_probe_is_rate_limited():
    score = widths({}, default=0.1)  # no shop on screen
    loop, state, _, _ = make_loop(ScoreScanner(score), rect=(0, 0, 3840, 2160), score=score)
    for _ in range(4 * HEAL_EVERY):
        loop.step()
    assert len(state["scored"]) == 4  # one extra score per HEAL_EVERY idle polls
    assert set(state["bases"]) == {(1366, 768)}


def test_confirmed_cache_used_without_probing():
    sc = FakeScanner([NONE] * 3)
    loop, state, _, _ = make_loop(sc, rect=(0, 0, 3840, 2160))
    for _ in range(3):
        loop.step()
    assert state["bases"] == [(1366, 768)] * 3 and state["saved"] == []


def test_client_resize_changes_base_and_resets_scanner():
    sc = FakeScanner([NONE, NONE])
    loop, state, _, _ = make_loop(sc, rect=(0, 0, 3840, 2160))
    loop.step()
    state["rect"] = (0, 0, 2560, 1600)
    loop.step()
    assert state["bases"] == [(1366, 768), (1228, 768)] and sc.resets == 1


def test_window_base_log_lines(caplog):
    class Probe(FakeScanner):
        n = 0

        def scan(self, gray):
            self.n += 1
            return profit_result() if gray.shape == (768, 1226) and self.n > 2 else NONE
    loop, state, _, _ = make_loop(Probe([]), rect=(4229, 381, 2560, 1600), settings=Settings(base_sizes={}))
    with caplog.at_level("INFO", logger="maplestory_classic_tool"):
        for _ in range(12):
            loop.step()
    lines = [r.getMessage() for r in caplog.records if r.getMessage().startswith("window rect=")]
    assert lines[0] == "window rect=(4229, 381, 2560, 1600) base=1228x768 (probing)"
    assert lines[-1] == "window rect=(4229, 381, 2560, 1600) base=1226x768 (confirmed)"
    assert len(lines) == 2  # no line per probe rotation


def test_worker_survives_exception():
    class Boom(FakeScanner):
        def scan(self, gray):
            raise RuntimeError("x")
    loop, _, _, _ = make_loop(Boom([]))
    assert loop.step() == 0.25


def test_error_after_panel_posts_clear():
    class Flaky(FakeScanner):
        def scan(self, gray):
            if not self.results:
                raise RuntimeError("x")
            return self.results.pop(0)
    loop, _, ui, _ = make_loop(Flaky([profit_result()]))
    loop.step()
    drain(ui)
    assert loop.step() == 0.25
    assert drain(ui) == [("clear",)]


def test_memory_logged_every_five_minutes(caplog):
    t = {"now": 0.0}
    loop, _, _, _ = make_loop(FakeScanner([NONE] * 10), clock=lambda: t["now"])
    with caplog.at_level("INFO", logger="maplestory_classic_tool"):
        loop.step()
        t["now"] = 299.0
        loop.step()
        assert not [r for r in caplog.records if "working set" in r.getMessage()]
        t["now"] = 300.0
        loop.step()
        t["now"] = 310.0
        loop.step()
        mem = [r.getMessage() for r in caplog.records if "working set" in r.getMessage()]
        assert len(mem) == 1 and "MB" in mem[0]


def test_working_set_bytes_positive():
    from maplestory_classic_tool.winutil import working_set_bytes
    assert working_set_bytes() > 10 * 1024 * 1024


@pytest.mark.live
def test_live_end_to_end():
    from maplestory_classic_tool.capture import Grabber
    from maplestory_classic_tool.layout import BaseResolver
    from maplestory_classic_tool.live import build_scanner
    scanner, settings = build_scanner(sync_if_empty=False)
    rect = find_client_rect(settings.window_title)
    assert rect is not None
    base, _ = BaseResolver(settings.base_sizes).current(rect[2:])
    gray = Grabber(scanner.layout.base_size).grab(rect, base)
    assert scanner.scan(gray).anchor is not None


def test_geometry_string_negative_origin():
    from maplestory_classic_tool.overlay import geometry_string
    assert geometry_string(-1920, 0, 5760, 1080) == "5760x1080+-1920+0"
    assert geometry_string(0, 0, 7680, 2160) == "7680x2160+0+0"


def test_worker_error_log_throttled(caplog):
    class Boom:
        layout = FakeLayout()
        catalog = "x"
        def reset(self): pass
        def scan(self, gray): raise ValueError("boom")

    clock = {"t": 1000.0}
    loop, _, ui, _ = make_loop(Boom())
    loop._clock = lambda: clock["t"]
    with caplog.at_level("ERROR", logger="maplestory_classic_tool"):
        for _ in range(20):
            loop.step()
        recs = [r for r in caplog.records if r.levelname == "ERROR"]
        assert len(recs) == 1 and recs[0].exc_info  # full traceback once
        clock["t"] += 61
        loop.step()
        recs = [r for r in caplog.records if r.levelname == "ERROR"]
        assert len(recs) == 2
        assert "19 suppressed" in recs[1].getMessage()
        # a different message logs immediately with traceback
        Boom.scan = lambda self, gray: (_ for _ in ()).throw(KeyError("other"))
        loop.step()
        recs = [r for r in caplog.records if r.levelname == "ERROR"]
        assert len(recs) == 3 and recs[2].exc_info


def test_list_cleared_when_base_heals():
    table = {1226: 0.96, 1228: 0.95}
    score = lambda w: table.get(w, 0.3)  # noqa: E731
    settings = Settings(base_sizes={"2560x1600": [1226, 768]})
    sc = ScoreScanner(score)
    loop, state, _, _ = make_loop(sc, rect=(4229, 381, 2560, 1600), settings=settings, score=score)
    loop.step()
    loop.step()
    assert [r.name for r in loop.listing.rows] == ["A"]  # read at the old base 1226
    sc.score = lambda w: 0.1  # shop closed briefly
    loop.step()
    table[1228] = 0.999
    sc.score = score  # shop visible again; 1228 now wins by the heal margin
    loop.step()
    assert settings.base_sizes["2560x1600"] == [1228, 768]
    assert loop.listing.rows == [] and loop.listing._shop is None


def test_list_cleared_when_client_resize_changes_base():
    a = profit_result()
    sc = FakeScanner([a, a, a])
    loop, state, _, _ = make_loop(sc, rect=(0, 0, 3840, 2160))
    loop.step()
    loop.step()
    assert loop.listing.rows
    state["rect"] = (0, 0, 2560, 1600)
    loop.step()
    assert state["bases"][-1] == (1228, 768)
    assert loop.listing.item_count == 0  # only one observe at the new base so far


def test_no_shop_identity_skips_accumulation():
    rows = [verdict(0, "A", 10)]
    sc = FakeScanner([profit_result(rows=rows, shop=None)] * 3)
    loop, _, _, _ = make_loop(sc)
    for _ in range(3):
        loop.step()
    assert loop.listing.rows == [] and loop.listing.shop_id is None


def test_state_and_title_count_only_listed_rows():
    """An unreadable row is neither listed nor counted (status deal count and panel title)."""
    rows = [verdict(0, "A", 10), verdict(1, "Mystery", 99, "unknown"), verdict(2, "B", 20, "no_profit", -3),
            verdict(3, "Junk", 1, "unknown")]
    states = []
    sc = FakeScanner([profit_result(rows=rows), profit_result(False, rows=rows)])
    loop, _, ui, _ = make_loop(sc, settings=Settings(show_all=True))
    loop._on_state = states.append
    loop.step()
    loop.step()
    p = [m for m in drain(ui) if m[0] == "panel"][-1][1]
    assert p.title == "2 items · 1 deals"
    assert [t.split("  ")[1] for t, _ in p.lines] == ["A", "B"]
    assert states[-1] == ("shop", 1)


class IconLayout(FakeLayout):
    icon_box = (-37, 3, 35, 35)

    def icon_rect(self, anchor, row):
        x, y = self.row_origin(anchor, row)
        return (x - 37, y + 3, 35, 35)


def test_loop_crops_icons_for_listed_rows_into_panel():
    rows = [verdict(0, "A", 10), verdict(1, "Mystery", 99, "unknown")]
    sc = FakeScanner([profit_result(rows=rows), profit_result(False, rows=rows)])
    sc.layout = IconLayout()
    asked = []

    def crops(rect, base, boxes):
        asked.append(list(boxes))
        return [np.full((35, 35, 3), 90, np.uint8) for _ in boxes]
    loop, _, ui, _ = make_loop(sc)
    loop._crops = crops
    loop.step()
    loop.step()
    assert asked and all(len(b) == 1 for b in asked)  # only the listed row is cropped
    assert asked[0][0] == (563 - 37, 210 + 3, 35, 35)
    p = [m for m in drain(ui) if m[0] == "panel"][-1][1]
    assert len(p.icons) == 1 and isinstance(p.icons[0], str)


# --- EXP tracker in the loop -----------------------------------------------------------------

from pathlib import Path  # noqa: E402

from maplestory_classic_tool.exp import ExpReading, load_statusbar  # noqa: E402

SB = load_statusbar(Path(__file__).resolve().parents[1] / "maplestory_classic_tool" / "assets" / "layout.json")


class FakeExpReader:
    def __init__(self, readings):
        self.readings = list(readings)
        self.calls = []
        self.layout = SB

    def read(self, gray, crop):
        self.calls.append(gray.shape)
        r = self.readings.pop(0) if len(self.readings) > 1 else self.readings[0]
        return r


BAR = (286, 735)


def exp_loop(readings, settings=None, scanner=None, rect=(0, 0, 2732, 1536)):
    t = {"now": 0.0}
    loop, state, ui, req = make_loop(scanner or FakeScanner([NONE] * 100), rect=rect, settings=settings,
                                     clock=lambda: t["now"])
    reader = FakeExpReader(readings)
    loop.exp_reader = reader
    return loop, reader, ui, req, t


def exp_msgs(ui):
    return [m for m in drain(ui) if m[0] == "exp"]


def test_exp_read_at_most_once_per_second_with_no_shop():
    loop, reader, ui, _, t = exp_loop([ExpReading(BAR, 134_202, 50.32)])
    for k in range(25):
        t["now"] = k / 10
        loop.step()
    assert len(reader.calls) == 3  # t = 0.0, 1.0, 2.0
    msgs = exp_msgs(ui)
    assert msgs and msgs[0][1] is not None
    box = msgs[0][1]
    scale = 2732 / 1366
    assert box.x == round((BAR[0] + SB.panel[0]) * scale)
    assert box.y == round((BAR[1] + SB.panel[1]) * scale)
    assert loop.exp.exp == 134_202


def test_exp_box_not_reposted_when_unchanged():
    loop, reader, ui, _, t = exp_loop([ExpReading(BAR, 134_202, 50.32)])
    for k in range(5):
        t["now"] = float(k)
        loop.step()
    assert len(reader.calls) == 5 and len(exp_msgs(ui)) == 1


def test_exp_disabled_never_reads():
    loop, reader, ui, _, t = exp_loop([ExpReading(BAR, 1, 1.0)], settings=Settings(exp_tracker=False))
    for k in range(5):
        t["now"] = float(k)
        loop.step()
    assert reader.calls == [] and exp_msgs(ui) == []


def test_exp_toggle_off_hides_box_and_stops_reading():
    loop, reader, ui, req, t = exp_loop([ExpReading(BAR, 134_202, 50.32)])
    loop.step()
    assert exp_msgs(ui)[-1][1] is not None
    req.put(("exp_tracker", False))
    t["now"] = 1.0
    loop.step()
    assert exp_msgs(ui) == [("exp", None)]
    t["now"] = 2.0
    loop.step()
    assert len(reader.calls) == 1
    req.put(("exp_tracker", True))
    t["now"] = 2.1
    loop.step()
    assert len(reader.calls) == 2 and exp_msgs(ui)[-1][1] is not None


def test_exp_box_hidden_when_status_bar_not_found():
    loop, reader, ui, _, t = exp_loop([ExpReading(BAR, 134_202, 50.32), None])
    loop.step()
    t["now"] = 1.0
    loop.step()
    assert [m[1] is None for m in exp_msgs(ui)] == [False, True]


def test_exp_box_hidden_when_window_lost():
    loop, reader, ui, _, t = exp_loop([ExpReading(BAR, 134_202, 50.32)])
    loop.step()
    loop.find_rect = lambda title: None
    t["now"] = 1.0
    loop.step()
    assert exp_msgs(ui)[-1] == ("exp", None)


def test_exp_unreadable_text_still_shows_box_without_values():
    loop, reader, ui, _, t = exp_loop([ExpReading(BAR)])
    loop.step()
    box = exp_msgs(ui)[-1][1]
    assert box is not None and loop.exp.exp is None


def test_exp_read_alongside_shop_scanning():
    sc = FakeScanner([profit_result(), profit_result(False)] * 5)
    loop, reader, ui, _, t = exp_loop([ExpReading(BAR, 134_202, 50.32)], scanner=sc)
    for k in range(4):
        t["now"] = k * 0.5
        loop.step()
    kinds = [m[0] for m in drain(ui)]
    assert "panel" in kinds and "exp" in kinds
    assert len(reader.calls) == 2


def test_exp_read_at_primary_base_while_probing():
    loop, reader, ui, _, t = exp_loop([ExpReading(BAR, 134_202, 50.32)], settings=Settings(base_sizes={}),
                                      rect=(0, 0, 2560, 1600))
    loop._regrab = lambda r, base: np.zeros((base[1], base[0]), np.uint8)
    loop.resolver.miss((2560, 1600))  # probing has moved past the primary candidate
    loop.step()
    assert reader.calls == [(768, 1228)]


def test_held_shop_keeps_list_and_panel_over_time():
    """A hover over the shop's buttons (scanner holds the anchor, no identity) never clears or forgets the list."""
    t = {"now": 0.0}
    a = profit_result(rows=[verdict(0, "A", 10)])
    held = ScanResult(a.anchor, a.verdicts, False, None)
    sc = FakeScanner([a, a] + [held] * 30 + [a])
    loop, _, ui, _ = make_loop(sc, clock=lambda: t["now"])
    for k in range(33):
        t["now"] = k * 0.1
        loop.step()
    assert [r.name for r in loop.listing.rows] == ["A"]
    assert not [m for m in drain(ui) if m[0] == "clear"]


class FakeMapReader:
    def __init__(self, ids):
        self.ids = list(ids)
        self.calls = 0

    def read(self, gray):
        self.calls += 1
        return self.ids.pop(0) if len(self.ids) > 1 else self.ids[0]


def _map_id(k):
    from maplestory_classic_tool.mapid import MapId
    ink = np.zeros((28, 96), bool)
    ink[:, k * 30:k * 30 + 30] = True
    return MapId("expanded", ink)


def _run_exp_with_maps(maps, seconds):
    exp = [ExpReading(BAR, 100_000 + 1_000 * k, 10.0 + 0.1 * k) for k in range(seconds + 1)]
    loop, reader, ui, _, t = exp_loop(exp)
    loop.map_reader = FakeMapReader(maps)
    for k in range(seconds + 1):
        t["now"] = float(k)
        loop.step()
    return loop


def test_exp_session_restarts_after_30s_on_another_map():
    a, b = _map_id(0), _map_id(1)
    loop = _run_exp_with_maps([a] * 10 + [b] * 40, 49)  # left A at t=10, reset due at t=40
    assert loop.map_watch.home is b
    assert loop.exp.gained == 9_000  # counted again from t=40 (samples 40..49)


def test_exp_session_kept_when_back_within_30s():
    a, b = _map_id(0), _map_id(1)
    loop = _run_exp_with_maps([a] * 10 + [b] * 20 + [a] * 20, 49)
    assert loop.map_watch.home is a and loop.exp.gained == 49_000


def test_no_map_reads_when_exp_tracker_off():
    loop, reader, ui, _, t = exp_loop([ExpReading(BAR, 1, 0.1)], settings=Settings(exp_tracker=False))
    loop.map_reader = FakeMapReader([_map_id(0)])
    for k in range(5):
        t["now"] = float(k)
        loop.step()
    assert loop.map_reader.calls == 0


class FakeMesoReader:
    def __init__(self, readings):
        self.readings = list(readings)
        self.calls = 0

    def read(self, gray):
        from maplestory_classic_tool.meso import MesoReading
        self.calls += 1
        v = self.readings.pop(0) if len(self.readings) > 1 else self.readings[0]
        return MesoReading(v is not None, v)


def test_meso_line_rate_and_open_inv():
    loop, reader, ui, _, t = exp_loop([ExpReading(BAR, 100_000, 10.0)])
    loop.meso_reader = FakeMesoReader([1000 + 100 * (k // 2) for k in range(130)] + [None])
    for k in range(131):
        t["now"] = float(k)
        loop.step()
    assert loop.meso.gained > 0
    box = [m for m in exp_msgs(ui) if m[1] is not None][-1][1]
    assert [s for s, _ in box.lines[2]] == ["Meso/h ", "open inv"]  # the last read: inventory closed
    assert loop.meso.rate(130.0) is not None  # still tracked while the line says open inv


def test_map_change_resets_meso_session_too():
    a, b = _map_id(0), _map_id(1)
    exp = [ExpReading(BAR, 100_000 + 1_000 * k, 10.0 + 0.1 * k) for k in range(50)]
    loop, reader, ui, _, t = exp_loop(exp)
    loop.map_reader = FakeMapReader([a] * 10 + [b] * 40)
    loop.meso_reader = FakeMesoReader([5000 + 10 * (k // 2) for k in range(50)])
    for k in range(50):
        t["now"] = float(k)
        loop.step()
    assert loop.map_watch.home is b and loop.meso.mesos is not None and loop.meso.gained < 100
