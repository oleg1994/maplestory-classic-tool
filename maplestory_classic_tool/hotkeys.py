"""Global hotkeys via RegisterHotKey on a dedicated message-loop thread."""
from __future__ import annotations

import ctypes
import logging
import threading
from ctypes import wintypes
from typing import Callable

log = logging.getLogger(__name__)

WM_HOTKEY = 0x0312
WM_QUIT = 0x0012
WM_USER = 0x0400


def vk(name: str) -> int:
    n = name.upper()
    if not (n.startswith("F") and n[1:].isdigit() and 1 <= int(n[1:]) <= 12):
        raise ValueError(f"unsupported hotkey: {name}")
    return 0x70 + int(n[1:]) - 1


class HotkeyThread(threading.Thread):
    def __init__(self, bindings: dict[str, Callable[[], None]]):
        super().__init__(daemon=True, name="hotkeys")
        self.bindings = bindings
        self._tid = None
        self._ready = threading.Event()

    def run(self) -> None:
        user32 = ctypes.windll.user32
        msg = wintypes.MSG()
        user32.PeekMessageW(ctypes.byref(msg), None, WM_USER, WM_USER, 0)  # create the thread's queue
        self._tid = ctypes.windll.kernel32.GetCurrentThreadId()
        self._ready.set()
        actions = {}
        for i, (name, cb) in enumerate(self.bindings.items(), start=1):
            try:
                code = vk(name)
            except ValueError as e:
                log.error("%s", e)
                continue
            if user32.RegisterHotKey(None, i, 0, code):
                actions[i] = cb
                log.info("hotkey registered: %s", name)
            else:
                log.error("RegisterHotKey failed for %s (error %d)", name, ctypes.GetLastError())
        if not actions:
            return
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            if msg.message == WM_HOTKEY and msg.wParam in actions:
                try:
                    actions[msg.wParam]()
                except Exception:  # noqa: BLE001
                    log.exception("hotkey callback failed")
        for i in actions:
            user32.UnregisterHotKey(None, i)

    def stop(self) -> None:
        if not self.is_alive():
            return
        if self._ready.wait(1.0) and self._tid:
            ctypes.windll.user32.PostThreadMessageW(self._tid, WM_QUIT, 0, 0)
        self.join(1.0)
