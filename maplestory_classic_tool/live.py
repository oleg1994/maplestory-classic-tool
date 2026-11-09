"""Live runtime: testable worker loop plus the Tk/thread wiring."""
from __future__ import annotations

import ctypes
import logging
import queue
import signal
import threading
import time
from pathlib import Path

import cv2
import requests

from maplestory_classic_tool.catalog import Catalog, sync
from maplestory_classic_tool.config import data_dir, load_settings
from maplestory_classic_tool.detect import ShopLocator
from maplestory_classic_tool.mapid import MAP_RESET_S, MapWatch, MinimapReader
from maplestory_classic_tool.meso import MesoReader, MesoTracker
from maplestory_classic_tool.exp import (READ_INTERVAL, MesoLine, ExpReader, ExpTracker, StatusBarLocator, build_exp_box,
                           exp_box_to_screen, load_statusbar)
from maplestory_classic_tool.fallback_ocr import WindowsOcr
from maplestory_classic_tool.glyphs import GlyphSet
from maplestory_classic_tool.evaluate import ScanResult
from maplestory_classic_tool.layout import PROBE_MIN, BaseResolver, load_layout
from maplestory_classic_tool.scanner import Scanner
from maplestory_classic_tool.sidelist import ShopListing, build_panel, panel_to_screen
from maplestory_classic_tool.winutil import SingleInstance, log_memory

log = logging.getLogger("maplestory_classic_tool")

ASSETS = Path(__file__).resolve().parent / "assets"  # frozen: datas map to maplestory_classic_tool/assets under _MEIPASS
IDLE_INTERVAL = 0.25
ERROR_LOG_INTERVAL = 60.0
MEMORY_LOG_INTERVAL = 300.0
HEAL_EVERY = 4  # idle polls between self-heal probes of a confirmed base (~1 s at IDLE_INTERVAL)
NO_SHOP = ScanResult(None, [], False)


def build_scanner(catalog_path: Path | None = None, sync_if_empty: bool = True):
    """Construct (Scanner, Settings) from the bundled assets and user data dir."""
    catalog_path = Path(catalog_path) if catalog_path else data_dir() / "catalog.json"
    catalog = Catalog.load(catalog_path)
    if sync_if_empty and len(catalog) == 0:
        catalog = sync(catalog_path, requests.Session())
    layout = load_layout(ASSETS / "layout.json")
    anchor = cv2.imread(str(ASSETS / "anchor.png"), cv2.IMREAD_GRAYSCALE)
    glyphs = GlyphSet.load(ASSETS / "glyphs.json")
    settings = load_settings(data_dir() / "settings.json")
    ocr = WindowsOcr.create()
    if ocr is None:
        log.warning("Windows OCR unavailable - install English OCR language pack; "
                    "unknown glyphs will stay unknown")
    scanner = Scanner(ShopLocator(anchor), layout, glyphs, catalog, settings, ocr)
    return scanner, settings


def build_exp_reader(ocr):
    """ExpReader from the bundled status-bar anchor and layout; `ocr` may be None (bar found, no values)."""
    anchor = cv2.imread(str(ASSETS / "statusbar_anchor.png"), cv2.IMREAD_GRAYSCALE)
    return ExpReader(StatusBarLocator(anchor), load_statusbar(ASSETS / "layout.json"), ocr)


def build_meso_reader():
    """MesoReader from the bundled inventory "MESOS" label template."""
    label = cv2.imread(str(ASSETS / "meso_label.png"), cv2.IMREAD_GRAYSCALE)
    if label is None:
        raise FileNotFoundError("meso label template missing from assets")
    return MesoReader(label)


def build_map_reader():
    """MinimapReader from the bundled WORLD-button and "MINI MAP" label templates."""
    world = cv2.imread(str(ASSETS / "minimap_world.png"), cv2.IMREAD_GRAYSCALE)
    label = cv2.imread(str(ASSETS / "minimap_label.png"), cv2.IMREAD_GRAYSCALE)
    if world is None or label is None:
        raise FileNotFoundError("minimap templates missing from assets")
    return MinimapReader(world, label)


class LiveLoop:
    """Owns the Scanner. step() handles queued requests, then one frame.

    Requests (req queue): "rescan", "clear", ("show_all", bool), ("exp_tracker", bool), ("catalog", Catalog).
    UI messages (ui queue): ("clear",), ("panel", sidelist.Panel in screen pixels), ("status", text),
    ("exp", exp.ExpBox in screen pixels or None).

    EXP: with an exp_reader and settings.exp_tracker, the status bar is read from the current frame at
    most once per READ_INTERVAL (independent of shops); the box is hidden while the bar is not found.
    With a map_reader the minimap's map name is read at the same time: after MAP_RESET_S off the
    session's map the EXP and meso sessions start over (mapid.MapWatch). With a meso_reader the item
    inventory's meso box is read too (the box's third line: mesos/hour, or "open inv").
    """

    def __init__(self, scanner, layout, settings, find_rect, grab, ui_queue, req_queue, clock=time.monotonic,
                 on_state=None, save_settings=None, score_fn=None, regrab=None, crops=None, exp_reader=None,
                 map_reader=None, meso_reader=None):
        self._clock = clock
        self._on_state = on_state
        self._save_settings = save_settings
        self._state = None
        self._errors: dict = {}  # message -> [last_logged, suppressed]
        self.scanner = scanner
        self.layout = layout
        self.settings = settings
        self.find_rect = find_rect
        self.grab = grab  # grab(rect, base) -> gray at base
        self.ui = ui_queue
        self.req = req_queue
        self.resolver = BaseResolver(settings.base_sizes, on_confirm=self._on_confirm)
        if score_fn is None:  # anchor NCC of a base frame; fakes without a locator skip best-pick/heal
            score_fn = getattr(getattr(scanner, "locator", None), "score", None)
        self._score_fn = score_fn
        self._regrab = regrab or grab  # regrab(rect, base): the last captured frame at another base
        self._crops = crops  # crops(rect, base, boxes): BGR icon crops of the last frame (Grabber.crops)
        self._had_anchor = False
        self._last_scores = None
        self._idle_polls = 0
        self._heal_idx = 0
        self.listing = ShopListing()
        self._window = False
        self._active = False
        self._cleared = False
        self._suppressed = False
        self._base = None
        self._window_sig = None
        self._posted = None  # inputs of the panel on screen, None when cleared
        self._mem_next = clock() + MEMORY_LOG_INTERVAL
        self.exp_reader = exp_reader
        self.exp = ExpTracker()  # a new loop (each On) starts a new session
        self.map_reader = map_reader
        self.map_watch = MapWatch()
        self.meso_reader = meso_reader
        self.meso = MesoTracker()
        self._exp_next = None  # clock time of the next EXP read
        self._exp_posted = None  # the EXP box on screen (None: hidden)

    @property
    def active_interval(self) -> float:
        return 1.0 / max(1, self.settings.poll_hz)

    def _set_state(self, state) -> None:
        if state != self._state:
            self._state = state
            if self._on_state:
                self._on_state(state)

    def _on_confirm(self, key, base) -> None:
        # copy-on-write: the UI thread may be serializing settings concurrently
        self.settings.base_sizes = {**self.settings.base_sizes, key: [base[0], base[1]]}
        log.info("base confirmed: client %s -> %dx%d %s", key, base[0], base[1], self._last_scores or "")
        if self._save_settings is not None:
            try:
                self._save_settings(self.settings)
            except Exception:  # noqa: BLE001
                log.exception("could not save settings")

    def _note_window(self, rect, base, confirmed) -> None:
        sig = (rect, base if confirmed else None, confirmed)
        if sig != self._window_sig:
            self._window_sig = sig
            log.info("window rect=%s base=%dx%d (%s)", rect, base[0], base[1],
                     "confirmed" if confirmed else "probing")

    def _scores(self, rect, bases) -> dict:
        """Anchor score of each base on the current frame (rounded, for the log)."""
        out = {}
        for b in dict.fromkeys(bases):
            out[b] = float(self._score_fn(self._regrab(rect, b)))
        self._last_scores = {f"{b[0]}x{b[1]}": round(v, 4) for b, v in out.items()}
        return out

    def _switch_base(self, base) -> None:
        """Rows read at another base are not comparable (and may be misreads): start over."""
        self.scanner.reset()
        self.listing.clear()
        self._base = base

    def _heal_probe(self, rect, client, base):
        """Confirmed base sees no shop: every HEAL_EVERY polls score one other family width. If it shows
        a shop, score the family on that frame and re-confirm when another base wins by HEAL_MARGIN."""
        self._idle_polls += 1
        if self._score_fn is None or self._idle_polls % HEAL_EVERY:
            return None
        others = [b for b in self.resolver.family(client) if b != base]
        if not others:
            return None
        cand = others[self._heal_idx % len(others)]
        self._heal_idx += 1
        if self._score_fn(self._regrab(rect, cand)) < PROBE_MIN:
            return None
        return self.resolver.heal(client, self._scores(rect, self.resolver.family(client) + [base]))

    def _post_clear(self, force=False) -> None:
        self._posted = None
        if force or not self._cleared:
            self.ui.put(("clear",))
            self._cleared = True

    def _post_panel(self, rect, base, anchor) -> None:
        if self._suppressed:
            return
        key = (rect, base, anchor, self.listing.version, self.settings.show_all)
        if key == self._posted:
            return
        panel = build_panel(self.listing, anchor, self.layout, self.settings.show_all, frame_w=base[0],
                            frame_h=base[1])
        self.ui.put(("panel", panel_to_screen(panel, (rect[0], rect[1]), rect[2] / base[0])))
        self._posted = key
        self._cleared = False

    def _icons(self, rect, base, result):
        """{row: BGR icon crop} for the rows the list will take (catalog match, known verdict)."""
        if self._crops is None or getattr(self.layout, "icon_box", None) is None:
            return None
        rows = [v.listing.row for v in result.verdicts if v.item is not None and v.status != "unknown"]
        if not rows:
            return None
        crops = self._crops(rect, base, [self.layout.icon_rect(result.anchor, r) for r in rows])
        return {r: c for r, c in zip(rows, crops) if c is not None}

    def _drain_requests(self) -> None:
        while True:
            try:
                r = self.req.get_nowait()
            except queue.Empty:
                return
            if r == "rescan":
                self.scanner.reset()
                self.listing.clear()
                self._suppressed = False
            elif r == "clear":
                self._suppressed = True
                self._post_clear(force=True)
            elif isinstance(r, tuple) and r[0] == "exp_tracker":
                self.settings.exp_tracker = bool(r[1])
                self._exp_next = None  # read on the next frame when turned on
                if not self.settings.exp_tracker:
                    self._post_exp(None)
            elif isinstance(r, tuple) and r[0] == "show_all":
                self.settings.show_all = bool(r[1])
                self._suppressed = False
            elif isinstance(r, tuple) and r[0] == "catalog":
                self.scanner.catalog = r[1]
                self.scanner.reset()
                self.listing.clear()
                self._suppressed = False

    def _post_exp(self, box) -> None:
        if box != self._exp_posted:
            self._exp_posted = box
            self.ui.put(("exp", box))

    def _crop_one(self, rect, base, box):
        if self._crops is None:
            return None
        return self._crops(rect, base, [box])[0]

    def _exp_step(self, rect, client, base, confirmed, gray, now) -> None:
        """At most once per READ_INTERVAL: read the status bar of this frame and post the EXP box."""
        if self.exp_reader is None or not self.settings.exp_tracker:
            return
        if self._exp_next is not None and now < self._exp_next:
            return
        self._exp_next = now + READ_INTERVAL
        if not confirmed:  # probing another base: the bar is read at the family's primary width
            family = self.resolver.family(client)
            if family and family[0] != base:
                base = family[0]
                gray = self._regrab(rect, base)
        if self.map_reader is not None and self.map_watch.observe(self.map_reader.read(gray), now):
            log.info("map changed %.0fs ago: new EXP / meso session", MAP_RESET_S)
            self.exp.reset()
            self.meso.reset()
        meso = self._meso_line(gray, now)
        reading = self.exp_reader.read(gray, lambda box: self._crop_one(rect, base, box))
        if reading is None:
            self._post_exp(None)
            return
        if reading.exp is not None:
            self.exp.add(reading.exp, reading.pct, now)
        lay = self.exp_reader.layout
        box = build_exp_box(reading.anchor, lay, self.exp, now, frame_w=base[0], meso=meso)
        lx, ly, lw, lh = lay.level_box
        level = self._crop_one(rect, base, (reading.anchor[0] + lx, reading.anchor[1] + ly, lw, lh))
        self._post_exp(exp_box_to_screen(box, (rect[0], rect[1]), rect[2] / base[0], level))

    def _meso_line(self, gray, now) -> MesoLine:
        """The inventory's mesos this frame -> the third line of the EXP box."""
        if self.meso_reader is None:
            return MesoLine(False)
        m = self.meso_reader.read(gray)
        if m.mesos is not None:
            self.meso.add(m.mesos, now)
        return MesoLine(m.visible, self.meso.rate(now), self.meso.gained)

    def _frame(self) -> float:
        now = self._clock()
        rect = self.find_rect(self.settings.window_title)
        if rect is None:
            if self._window:
                log.info("game window lost")
            self._window = False
            self._active = False
            self.listing.absent(now)
            self._set_state(("waiting",))
            self._post_clear()
            self._post_exp(None)
            return IDLE_INTERVAL
        if not self._window:
            log.info("game window found: %s", rect)
            self._window = True
        client = (rect[2], rect[3])
        base, confirmed = self.resolver.current(client)
        self._note_window(rect, base, confirmed)
        if self._base is not None and base != self._base:
            self._switch_base(base)
        self._base = base
        gray = self.grab(rect, base)
        self._exp_step(rect, client, base, confirmed, gray, now)
        result = self.scanner.scan(gray)
        if result.anchor is not None and (not confirmed or not self._had_anchor):
            # a shop just appeared: pick the best-scoring base on this frame (first confirm or self-heal)
            self._had_anchor = True
            if self._score_fn is None:
                if not confirmed:
                    self.resolver.confirm(client, base)
                chosen = base
            else:
                scores = self._scores(rect, self.resolver.family(client) + [base])
                chosen = self.resolver.heal(client, scores) if confirmed else self.resolver.choose(client, scores)
                if chosen is None:
                    chosen = base if confirmed else None
            if chosen is None:  # probe passed the locator but nothing reached CONFIRM_MIN
                result = NO_SHOP
            elif chosen != base:  # this frame was read at the wrong base; rescan at the chosen one
                self._note_window(rect, chosen, True)
                self._switch_base(chosen)
                return self.active_interval
            else:
                self._note_window(rect, base, True)
        if result.anchor is None:
            self._had_anchor = False
            if not confirmed:
                self.resolver.miss(client)
            else:
                healed = self._heal_probe(rect, client, base)
                if healed is not None:
                    self._switch_base(healed)
            self._active = False
            self.listing.absent(now)
            self._set_state(("no_shop",))
            self._post_clear()
            return IDLE_INTERVAL
        self._active = True
        # no identity (id boxes off-frame): do not merge this frame into any shop's list
        if result.shop_id is not None and self.listing.observe(result.shop_id, result.verdicts, now,
                                                                self._icons(rect, base, result)):
            self._suppressed = False
        self._set_state(("shop", self.listing.deal_count))
        self._post_panel(rect, base, result.anchor)
        return self.active_interval

    def _log_error(self, exc: Exception) -> None:
        """Full traceback the first time a message occurs, then at most once per interval."""
        msg = f"{type(exc).__name__}: {exc}"
        now = self._clock()
        entry = self._errors.get(msg)
        if entry is None:
            if len(self._errors) > 100:
                self._errors.clear()
            self._errors[msg] = [now, 0]
            log.error("worker error", exc_info=exc)
        elif now - entry[0] >= ERROR_LOG_INTERVAL:
            log.error("worker error (repeated, %d suppressed): %s", entry[1], msg)
            entry[0], entry[1] = now, 0
        else:
            entry[1] += 1

    def _maybe_log_memory(self) -> None:
        now = self._clock()
        if now >= self._mem_next:
            self._mem_next = now + MEMORY_LOG_INTERVAL
            log_memory("periodic")

    def step(self) -> float:
        """Run one iteration; return seconds to wait before the next."""
        try:
            self._maybe_log_memory()
            self._drain_requests()
            return self._frame()
        except Exception as exc:  # noqa: BLE001
            self._log_error(exc)
            self._active = False
            self._post_clear()
            return IDLE_INTERVAL


def _setup_logging() -> None:
    logging.basicConfig(filename=str(data_dir() / "log.txt"), level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def set_dpi_aware() -> None:
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:  # noqa: BLE001
        ctypes.windll.user32.SetProcessDPIAware()


def run_live() -> int:
    """Headless overlay: always on, no control window."""
    set_dpi_aware()
    _setup_logging()
    instance = SingleInstance()
    if not instance.acquire():
        log.error("MapleStory Classic Tool is already running; exiting")
        return 1
    import tkinter as tk

    from maplestory_classic_tool.overlay import Overlay
    from maplestory_classic_tool.session import LiveSession

    root = tk.Tk()
    root.withdraw()
    overlay = Overlay(tk.Toplevel(root))
    session = LiveSession(root, overlay)

    def pump():
        try:
            session.pump()
        except Exception:  # noqa: BLE001
            log.exception("ui pump error")
        try:
            root.after(16, pump)
        except tk.TclError:
            pass  # root destroyed

    def quit_(*_):
        session.close()  # release OCR/dxcam before Tk teardown and interpreter exit
        root.destroy()

    signal.signal(signal.SIGINT, quit_)
    session.start()
    root.after(16, pump)
    try:
        root.mainloop()
    except KeyboardInterrupt:
        pass
    finally:
        session.close()
        instance.release()
    return 0


def selftest() -> int:
    """Smoke test for the frozen exe: no windows, no network."""
    _setup_logging()
    t0 = time.monotonic()
    try:
        import numpy as np

        from maplestory_classic_tool.hotkeys import vk  # noqa: F401  (import check)
        scanner, _ = build_scanner(sync_if_empty=False)
        w, h = scanner.layout.base_size
        result = scanner.scan(np.zeros((h, w), np.uint8))
        ocr = WindowsOcr.create()
        bar = build_exp_reader(ocr).read(np.zeros((h, w), np.uint8), lambda box: None)
        minimap = build_map_reader().read(np.zeros((h, w), np.uint8))  # the minimap templates are bundled
        inventory = build_meso_reader().read(np.zeros((h, w), np.uint8)).visible  # and the meso label
        import dxcam  # noqa: F401

        from maplestory_classic_tool.capture import DxcamBackend
        outputs = len(DxcamBackend().outputs())  # enumerates monitors only; nothing is captured
        import tkinter  # noqa: F401  (frozen build must bundle Tk; no window is created)
        import maplestory_classic_tool.panel  # noqa: F401
        import maplestory_classic_tool.session  # noqa: F401
        import mss  # noqa: F401
        msg = (f"selftest ok: catalog={len(scanner.catalog)} anchor={result.anchor} statusbar={bar} "
               f"minimap={minimap} inventory={inventory} ocr={'yes' if ocr else 'no'} outputs={outputs} {time.monotonic() - t0:.2f}s")
        log.info(msg)
        print(msg)
        return 0
    except Exception as exc:  # noqa: BLE001
        log.exception("selftest failed")
        print(f"selftest FAILED: {type(exc).__name__}: {exc}")
        return 1
