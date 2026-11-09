import threading
import time

import numpy as np

from maplestory_classic_tool.config import Settings
from maplestory_classic_tool.evaluate import ScanResult
from maplestory_classic_tool.model import Item, Listing, Verdict
from maplestory_classic_tool.session import LiveSession
from maplestory_classic_tool.sidelist import Panel


class FakeLayout:
    base_size = (1366, 768)
    label_dx = 0

    def row_origin(self, anchor, row):
        return anchor[0], anchor[1] + 40 * row

    def window_rect(self, a):
        return (a[0] - 141, a[1] - 58, 523, 406)


class FakeCatalog:
    def __init__(self, n=5, stale=False):
        self.n, self.stale = n, stale

    def __len__(self):
        return self.n

    def is_stale(self, days, now):
        return self.stale


class FakeScanner:
    def __init__(self, catalog=None):
        self.layout = FakeLayout()
        self.catalog = FakeCatalog() if catalog is None else catalog
        self.resets = 0
        self.scans = 0

    def reset(self):
        self.resets += 1

    def scan(self, gray):
        self.scans += 1
        v = Verdict(Listing(0, "A", 1, 5, False), Item(1, "A", 10), 10, 5, "profit")
        return ScanResult((563, 210), [v], self.scans == 1, "shop")


class FakeOverlay:
    def __init__(self):
        self.calls = []

    def show_panel(self, panel):
        self.calls.append(("show", panel))

    def clear(self):
        self.calls.append(("clear",))

    def status(self, text):
        self.calls.append(("status", text))

    def withdraw(self):
        self.calls.append(("withdraw",))

    def deiconify(self):
        self.calls.append(("deiconify",))

    def show_exp(self, box):
        self.calls.append(("exp", box))

    def clear_exp(self):
        self.calls.append(("clear_exp",))


class FakeHotkeys:
    instances = []

    def __init__(self, bindings):
        self.bindings = bindings
        self.started = self.stopped = 0
        FakeHotkeys.instances.append(self)

    def start(self):
        self.started += 1

    def stop(self):
        self.stopped += 1


class FakeGrabber:
    def __init__(self, size):
        self.closed = 0

    def grab(self, rect, base):
        return np.zeros((base[1], base[0]), np.uint8)

    def close(self):
        self.closed += 1


class FakeExpReader:
    def __init__(self, ocr):
        from pathlib import Path

        from maplestory_classic_tool.exp import load_statusbar
        self.ocr = ocr
        self.layout = load_statusbar(Path(__file__).resolve().parents[1] / "maplestory_classic_tool" / "assets" / "layout.json")
        self.reads = 0

    def read(self, gray, crop):
        from maplestory_classic_tool.exp import ExpReading
        self.reads += 1
        return ExpReading((286, 735), 134_202, 50.32)


def make(scanner=None, settings=None, settings_path="x.json"):
    FakeHotkeys.instances = []
    scanner = scanner or FakeScanner()
    settings = settings or Settings(poll_hz=100, show_all=False, base_sizes={"1366x768": [1366, 768]})
    builds = []

    def build(**kw):
        builds.append(kw)
        return scanner, settings

    saved = []
    ov = FakeOverlay()
    s = LiveSession(None, ov, build=build, find_rect=lambda t: (0, 0, 1366, 768),
                    grabber_factory=FakeGrabber,
                    hotkeys_factory=FakeHotkeys, save=lambda st, p: saved.append(st.show_all),
                    settings_path=settings_path, exp_build=FakeExpReader)
    return s, scanner, settings, ov, builds, saved


def wait(cond, t=3.0):
    end = time.time() + t
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.01)
    return False


def workers():
    return [t for t in threading.enumerate() if t.name == "scan-worker"]


def test_start_runs_worker_hotkeys_overlay():
    s, sc, st, ov, builds, _ = make()
    try:
        s.start()
        assert s.running and len(workers()) == 1
        assert len(FakeHotkeys.instances) == 1 and FakeHotkeys.instances[0].started == 1
        assert set(FakeHotkeys.instances[0].bindings) == {"F8", "F9"}
        assert ("deiconify",) in ov.calls
        assert builds == [{"sync_if_empty": False}]
        assert wait(lambda: sc.scans > 0)
    finally:
        s.stop()


def test_stop_joins_and_cleans_up():
    s, sc, st, ov, _, _ = make()
    s.start()
    thread = workers()[0]
    s.stop()
    assert not s.running and not thread.is_alive() and workers() == []
    assert FakeHotkeys.instances[0].stopped == 1
    assert ov.calls[-3:] == [("clear",), ("clear_exp",), ("withdraw",)]


def test_restart_resets_scanner_and_new_worker():
    s, sc, *_ = make()
    s.start()
    s.stop()
    before = sc.resets
    s.start()
    try:
        assert sc.resets == before + 1 and len(workers()) == 1
        assert len(FakeHotkeys.instances) == 2
    finally:
        s.stop()


def test_build_once_lazily():
    s, sc, st, ov, builds, _ = make()
    assert builds == []
    s.start()
    s.stop()
    s.start()
    s.stop()
    assert len(builds) == 1


def test_double_start_stop_noop():
    s, sc, st, ov, _, _ = make()
    s.stop()  # before start
    assert ov.calls == []
    s.start()
    s.start()
    try:
        assert len(workers()) == 1 and len(FakeHotkeys.instances) == 1
    finally:
        s.stop()
        s.stop()
    assert FakeHotkeys.instances[0].stopped == 1


def test_show_all_reaches_worker_and_reposts_labels():
    s, sc, st, ov, _, saved = make()
    try:
        s.start()
        assert wait(lambda: sc.scans > 0)
        s.pump()
        n_show = sum(1 for c in ov.calls if c[0] == "show")
        assert n_show >= 1
        s.set_show_all(True)
        assert saved == [True]
        assert wait(lambda: st.show_all is True)

        def reposted():
            s.pump()
            return sum(1 for c in ov.calls if c[0] == "show") > n_show
        assert wait(reposted)
    finally:
        s.stop()


def test_pump_applies_labels_and_states():
    s, sc, st, ov, _, _ = make()
    seen = []
    s.on_state = seen.append
    try:
        s.start()
        assert wait(lambda: sc.scans > 0)
        assert wait(lambda: (s.pump(), s.state)[1] == ("shop", 1))
        assert seen[0] == ("starting",) and seen[-1] == ("shop", 1)
        assert wait(lambda: (s.pump(), [c for c in ov.calls if c[0] == "show"][-1][1].title)[1]
                    == "1 items · 1 deals")
        panel = [c[1] for c in ov.calls if c[0] == "show"][-1]
        assert isinstance(panel, Panel) and panel.title == "1 items · 1 deals"
    finally:
        s.stop()
    assert s.state == ("off",)


def test_empty_catalog_state_no_data():
    s, sc, st, ov, _, _ = make(FakeScanner(FakeCatalog(0)))
    s.sync_fn = lambda path, sess: FakeCatalog(0)
    try:
        s.start()
        assert wait(lambda: (s.pump(), s.state)[1] == ("no_data",))
    finally:
        s.stop()


def test_stale_catalog_syncs_in_background_and_swaps():
    new = FakeCatalog(9)
    sc = FakeScanner(FakeCatalog(3, stale=True))
    s, sc, st, ov, _, _ = make(sc)
    s.sync_fn = lambda path, sess: new
    try:
        s.start()
        assert wait(lambda: (s.pump(), sc.catalog is new)[1])
    finally:
        s.stop()


def test_state_survives_catalog_sync_completion():
    sc = FakeScanner(FakeCatalog(3, stale=True))
    s, sc, st, ov, _, _ = make(sc)
    gate = threading.Event()
    new = FakeCatalog(9)

    def slow_sync(path, sess):
        gate.wait(3)
        return new
    s.sync_fn = slow_sync
    try:
        s.start()
        assert wait(lambda: (s.pump(), s.state)[1] == ("shop", 1))
        gate.set()
        assert wait(lambda: (s.pump(), sc.catalog is new)[1])
        # the swap clears the list; it is rebuilt after two observes (stability gate)
        assert wait(lambda: (s.pump(), s.state)[1] == ("shop", 1))
    finally:
        gate.set()
        s.stop()


def test_show_all_while_off_does_not_build(tmp_path):
    s, sc, st, ov, builds, saved = make(settings_path=tmp_path / "settings.json")
    s.set_show_all(True)
    assert builds == [] and saved == [True]
    assert not s.running and ov.calls == []


def test_close_releases_grabber_and_ocr_and_is_idempotent():
    s, sc, st, ov, builds, _ = make()
    sc.fallback = object()
    s.start()
    grabber = s._built[2]
    s.close()
    assert not s.running and grabber.closed == 1 and sc.fallback is None
    assert s._built is None and workers() == []
    s.close()
    assert grabber.closed == 1


def test_start_after_close_rebuilds():
    s, sc, st, ov, builds, _ = make()
    s.start()
    s.close()
    s.start()
    try:
        assert len(builds) == 2 and s.running
    finally:
        s.close()


def test_build_registers_atexit_close(monkeypatch):
    import maplestory_classic_tool.session as session_mod
    registered = []
    monkeypatch.setattr(session_mod.atexit, "register", lambda fn: registered.append(fn))
    s, *_ = make()
    s.start()
    try:
        assert registered == [s.close]
    finally:
        s.close()


def test_base_confirmation_is_saved(tmp_path):
    saved = []
    s, sc, st, ov, _, _ = make(settings=Settings(poll_hz=100, base_sizes={}))
    s._save = lambda settings, path: saved.append(dict(settings.base_sizes))
    try:
        s.start()
        assert wait(lambda: bool(saved))
        assert saved[0] == {"1366x768": [1366, 768]}
    finally:
        s.close()


def test_memory_logged_at_start_and_stop(caplog):
    s, *_ = make()
    with caplog.at_level("INFO", logger="maplestory_classic_tool"):
        s.start()
        s.stop()
    msgs = [r.getMessage() for r in caplog.records]
    assert any(m.startswith("memory (start)") for m in msgs)
    assert any(m.startswith("memory (stop)") for m in msgs)



def test_close_keeps_resources_when_worker_will_not_join(monkeypatch):
    import maplestory_classic_tool.session as session_mod
    monkeypatch.setattr(session_mod, "JOIN_TIMEOUT", 0.05)
    gate = threading.Event()

    class Stuck(FakeScanner):
        def scan(self, gray):
            gate.wait(5)
            return super().scan(gray)
    s, sc, *_ = make(Stuck())
    sc.fallback = object()
    s.start()
    assert wait(lambda: len(workers()) == 1)
    grabber = s._built[2]
    s.close()  # worker stuck in scan: nothing may be released, but nothing may be lost either
    assert grabber.closed == 0 and sc.fallback is not None and s._built is not None
    gate.set()
    assert wait(lambda: workers() == [])
    s.close()  # the atexit retry
    assert grabber.closed == 1 and sc.fallback is None and s._built is None


def test_start_failure_after_worker_started_stops_it():
    class BadHotkeys(FakeHotkeys):
        def start(self):
            raise RuntimeError("no hotkey thread")
    s, sc, st, ov, _, _ = make()
    s._hotkeys_factory = BadHotkeys
    try:
        s.start()
    except RuntimeError:
        pass
    else:
        raise AssertionError("start should re-raise")
    assert not s.running and s.state == ("off",)
    assert wait(lambda: workers() == [])
    assert s._built is not None  # kept so close()/atexit can release it
    grabber = s._built[2]
    s.close()
    assert grabber.closed == 1


# --- EXP tracker -------------------------------------------------------------------------------

def test_exp_box_reaches_overlay_and_is_cleared_on_stop():
    s, sc, st, ov, _, _ = make()
    try:
        s.start()
        assert wait(lambda: (s.pump(), any(c[0] == "exp" and c[1] is not None for c in ov.calls))[1])
        assert s._exp_reader.ocr is getattr(sc, "fallback", None)
    finally:
        s.stop()
    assert ("clear_exp",) in ov.calls


def test_exp_tracker_resets_on_each_start():
    s, sc, st, ov, _, _ = make()
    try:
        s.start()
        assert wait(lambda: s._loop is not None and s._loop.exp.exp == 134_202)
        first = s._loop.exp
        s.stop()
        s.start()
        assert s._loop.exp is not first and s._loop.exp.gained == 0
    finally:
        s.stop()


def test_set_exp_tracker_saves_and_reaches_worker():
    s, sc, st, ov, _, saved = make()
    try:
        s.start()
        assert wait(lambda: s._exp_reader.reads > 0)
        s.set_exp_tracker(False)
        assert st.exp_tracker is False and len(saved) == 1
        assert wait(lambda: (s.pump(), ("exp", None) in ov.calls)[1])
        n = s._exp_reader.reads
        time.sleep(1.2)
        assert s._exp_reader.reads == n
    finally:
        s.stop()


def test_set_exp_tracker_while_off_does_not_build(tmp_path):
    s, sc, st, ov, builds, saved = make(settings_path=tmp_path / "settings.json")
    s.set_exp_tracker(False)
    assert builds == [] and len(saved) == 1 and not s.running
    from maplestory_classic_tool.config import load_settings
    assert load_settings(tmp_path / "settings.json").exp_tracker is True  # the fake save did not write


def test_close_drops_the_exp_readers_ocr():
    s, sc, st, ov, builds, _ = make()
    sc.fallback = object()
    s.start()
    reader = s._exp_reader
    s.close()
    assert reader.ocr is None and s._exp_reader is None
