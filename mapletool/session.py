"""LiveSession: owns the scan worker, hotkeys and overlay visibility (start/stop-able)."""
from __future__ import annotations

import atexit
import gc
import logging
import queue
import threading
import time

from mapletool.config import data_dir, load_settings, save_settings
from mapletool.winutil import log_memory

log = logging.getLogger("mapletool")

JOIN_TIMEOUT = 1.0


class LiveSession:
    """State tuples: ("off",) ("starting",) ("waiting",) ("no_shop",) ("shop", n) ("no_data",).

    pump() must be called from the Tk main thread; it applies queued worker
    messages to the overlay and updates `state` (calling `on_state`).
    """

    def __init__(self, root, overlay, build=None, find_rect=None, grabber_factory=None,
                 hotkeys_factory=None, save=save_settings, settings_path=None, sync_fn=None, exp_build=None):
        self.root = root
        self.overlay = overlay
        self._build = build
        self._find_rect = find_rect
        self._grabber_factory = grabber_factory
        self._hotkeys_factory = hotkeys_factory
        self._save = save
        self._settings_path = settings_path
        self.sync_fn = sync_fn
        self._exp_build = exp_build  # exp_build(ocr) -> ExpReader (default live.build_exp_reader)
        self._exp_reader = None
        self._loop = None  # the running LiveLoop (its ExpTracker is the EXP session)
        self.on_state = None
        self.state = ("off",)
        self.running = False
        self._no_data = False
        self._worker_state = ("waiting",)
        self._built = None  # (scanner, settings, grabber)
        self._syncing = False
        self._ui: queue.Queue = queue.Queue()
        self._req: queue.Queue = queue.Queue()
        self._stop_evt = None
        self._thread = None
        self._hotkeys = None
        self._save_lock = threading.Lock()  # worker (base confirmed) and UI (show all) both save

    # -- state -------------------------------------------------------------
    def _set_state(self, state) -> None:
        if self._no_data and state[0] in ("waiting", "no_shop", "shop"):
            state = ("no_data",)
        if state != self.state:
            self.state = state
            if self.on_state:
                self.on_state(state)

    # -- lazy build --------------------------------------------------------
    def _ensure_built(self):
        if self._built is None:
            build = self._build
            if build is None:
                from mapletool.live import build_scanner as build
            scanner, settings = build(sync_if_empty=False)
            factory = self._grabber_factory
            if factory is None:
                from mapletool.capture import Grabber as factory
            self._built = (scanner, settings, factory(scanner.layout.base_size))
            exp_build = self._exp_build
            if exp_build is None:
                from mapletool.live import build_exp_reader as exp_build
            try:
                self._exp_reader = exp_build(getattr(scanner, "fallback", None))  # shares the OCR engine
            except Exception:  # noqa: BLE001  (no EXP box; shops still work)
                log.exception("EXP reader unavailable")
                self._exp_reader = None
            # COM/WinRT objects (OCR engine, dxcam) must be released before interpreter teardown;
            # registered after their modules were imported, so this runs before their atexit hooks.
            atexit.register(self.close)
            log.info("startup; catalog items=%d", len(scanner.catalog))
        return self._built

    def _settings_file(self):
        return self._settings_path or data_dir() / "settings.json"

    def _save_settings(self, settings) -> None:
        with self._save_lock:
            self._save(settings, self._settings_file())

    def _maybe_sync(self, scanner, settings) -> None:
        if self._syncing:
            return
        if not (len(scanner.catalog) == 0 or scanner.catalog.is_stale(settings.catalog_max_age_days, time.time())):
            return
        self._syncing = True
        ui, req = self._ui, self._req

        def job():
            try:
                sync_fn = self.sync_fn
                if sync_fn is None:
                    import requests

                    from mapletool.catalog import sync as sync_fn
                    http = requests.Session()
                else:
                    http = None
                cat = sync_fn(data_dir() / "catalog.json", http)
                log.info("catalog sync ok; items=%d", len(cat))
            except Exception:  # noqa: BLE001
                log.exception("catalog sync failed")
                return
            finally:
                self._syncing = False
            if len(cat) == 0:
                ui.put(("status", "No price data"))
            else:
                ui.put(("status", None))
                req.put(("catalog", cat))

        threading.Thread(target=job, daemon=True, name="catalog-sync").start()

    # -- lifecycle ---------------------------------------------------------
    def _map_reader(self):
        """Minimap reader for the EXP map-change reset; None when its templates cannot be loaded."""
        try:
            from mapletool.live import build_map_reader
            return build_map_reader()
        except Exception:  # noqa: BLE001
            log.warning("minimap reader unavailable; EXP sessions only restart with On", exc_info=True)
            return None

    def _meso_reader(self):
        """Inventory meso reader for the meso/h line; None when its template cannot be loaded."""
        try:
            from mapletool.live import build_meso_reader
            return build_meso_reader()
        except Exception:  # noqa: BLE001
            log.warning("meso reader unavailable; the EXP box shows no mesos/hour", exc_info=True)
            return None

    def start(self) -> None:
        if self.running:
            return
        from mapletool.live import LiveLoop
        self._set_state(("starting",))
        try:
            scanner, settings, grabber = self._ensure_built()
        except Exception:
            log.exception("start failed")
            self._set_state(("off",))
            raise
        find_rect = self._find_rect
        if find_rect is None:
            from mapletool.capture import find_client_rect as find_rect
        hk_factory = self._hotkeys_factory
        if hk_factory is None:
            from mapletool.hotkeys import HotkeyThread as hk_factory
        scanner.reset()
        self._worker_state = ("waiting",)
        self._no_data = len(scanner.catalog) == 0
        if self._no_data:
            self._ui.put(("status", "No price data"))
        self._maybe_sync(scanner, settings)
        loop = LiveLoop(scanner, scanner.layout, settings, find_rect, grabber.grab, self._ui, self._req,
                        on_state=lambda s: self._ui.put(("state", s)), save_settings=self._save_settings,
                        regrab=getattr(grabber, "rebase", None), crops=getattr(grabber, "crops", None),
                        exp_reader=self._exp_reader, map_reader=self._map_reader(),
                        meso_reader=self._meso_reader())
        self._loop = loop
        stop = threading.Event()

        def worker():
            while not stop.is_set():
                t0 = time.monotonic()
                interval = loop.step()
                stop.wait(max(0.0, interval - (time.monotonic() - t0)))

        self._stop_evt = stop
        self._thread = threading.Thread(target=worker, daemon=True, name="scan-worker")
        self._thread.start()
        req = self._req
        try:
            self._hotkeys = hk_factory({settings.hotkey_scan: lambda: req.put("rescan"),
                                        settings.hotkey_clear: lambda: req.put("clear")})
            self._hotkeys.start()
        except Exception:
            # never leave a worker running behind a session that reports Off; keep the build so
            # close()/atexit can still release the capture and OCR objects
            log.exception("start failed")
            stop.set()
            self._thread.join(JOIN_TIMEOUT)
            if not self._thread.is_alive():
                self._thread = None
            if self._hotkeys is not None:
                try:
                    self._hotkeys.stop()
                except Exception:  # noqa: BLE001
                    pass
                self._hotkeys = None
            self._set_state(("off",))
            raise
        self.running = True
        self.overlay.deiconify()
        log_memory("start")
        self._set_state(("no_data",) if self._no_data else self._worker_state)

    def stop(self) -> None:
        if not self.running:
            return
        self.running = False
        self._stop_evt.set()
        self._thread.join(JOIN_TIMEOUT)
        if self._thread.is_alive():
            log.warning("scan worker did not stop within %.1fs", JOIN_TIMEOUT)  # kept for close()
        else:
            self._thread = None
        if self._hotkeys is not None:
            self._hotkeys.stop()
            self._hotkeys = None
        self.overlay.clear()
        self.overlay.clear_exp()
        self.overlay.withdraw()
        log_memory("stop")
        self._set_state(("off",))

    def close(self) -> None:
        """Stop and release capture/OCR resources explicitly (idempotent; also run at exit).

        Root cause of the exit segfault: the WinRT OcrEngine (and dxcam's COM objects) were left
        for the interpreter's final garbage collection, which runs after COM/WinRT teardown.
        """
        try:
            self.stop()
        except Exception:  # noqa: BLE001  (at exit the Tk overlay may already be destroyed)
            log.warning("stop during close failed", exc_info=True)
        built, self._built = self._built, None
        if built is None:
            return
        scanner, _, grabber = built
        if self._thread is not None and self._thread.is_alive():
            # keep everything so a later close() (the atexit retry) can still release it
            log.warning("scan worker still running; not releasing capture resources yet")
            self._built = built
            return
        close = getattr(grabber, "close", None)
        if close is not None:
            try:
                close()
            except Exception:  # noqa: BLE001
                log.exception("grabber close failed")
        scanner.fallback = None
        if self._exp_reader is not None:
            self._exp_reader.ocr = None  # the shared OCR engine must go before interpreter teardown too
        self._exp_reader = None
        self._loop = None
        del built, scanner, grabber
        gc.collect()

    def set_show_all(self, value: bool) -> None:
        value = bool(value)
        if self._built is None:
            # Off and never started: only touch the settings file, never build scanner/dxcam.
            settings = load_settings(self._settings_file())
        else:
            settings = self._built[1]
        settings.show_all = value
        try:
            self._save_settings(settings)
        except Exception:  # noqa: BLE001
            log.exception("could not save settings")
        if self.running:
            self._req.put(("show_all", value))

    def set_exp_tracker(self, value: bool) -> None:
        value = bool(value)
        settings = load_settings(self._settings_file()) if self._built is None else self._built[1]
        settings.exp_tracker = value
        try:
            self._save_settings(settings)
        except Exception:  # noqa: BLE001
            log.exception("could not save settings")
        if self.running:
            self._req.put(("exp_tracker", value))

    def set_block_alt_shift(self, value: bool) -> None:
        """Persist the "block Alt+Shift" choice (the blocker itself lives in the control panel)."""
        settings = load_settings(self._settings_file()) if self._built is None else self._built[1]
        settings.block_alt_shift = bool(value)
        try:
            self._save_settings(settings)
        except Exception:  # noqa: BLE001
            log.exception("could not save settings")

    # -- UI pump (main thread) ---------------------------------------------
    def pump(self) -> None:
        try:
            while True:
                msg = self._ui.get_nowait()
                kind = msg[0]
                if kind == "status":
                    self._no_data = msg[1] == "No price data"
                    self.overlay.status(msg[1])
                    if self.running:
                        self._set_state(("no_data",) if self._no_data else self._worker_state)
                elif not self.running:
                    continue  # stale worker output while Off
                elif kind == "clear":
                    self.overlay.clear()
                elif kind == "panel":
                    self.overlay.show_panel(msg[1])
                elif kind == "exp":
                    self.overlay.show_exp(msg[1])
                elif kind == "state":
                    self._worker_state = msg[1]
                    self._set_state(msg[1])
        except queue.Empty:
            pass
