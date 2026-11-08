"""Screen capture of the game client area (reads pixels only)."""
from __future__ import annotations

import ctypes
import logging
import time
from ctypes import wintypes

import cv2
import numpy as np

from mapletool.layout import crop_to_base, to_base

log = logging.getLogger(__name__)

OUTPUT_RETRY_S = 30.0  # a failed dxcam output is tried again after this long (outputs re-listed)

user32 = ctypes.windll.user32
user32.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
user32.FindWindowW.restype = wintypes.HWND
user32.IsIconic.argtypes = [wintypes.HWND]
user32.GetClientRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
user32.ClientToScreen.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.POINT)]


def find_client_rect(title: str):
    """Screen (x, y, w, h) of the client area, or None if missing/minimized."""
    hwnd = user32.FindWindowW(None, title)
    if not hwnd or user32.IsIconic(hwnd):
        return None
    rc = wintypes.RECT()
    if not user32.GetClientRect(hwnd, ctypes.byref(rc)):
        return None
    w, h = rc.right - rc.left, rc.bottom - rc.top
    if w <= 0 or h <= 0:
        return None
    pt = wintypes.POINT(0, 0)
    if not user32.ClientToScreen(hwnd, ctypes.byref(pt)):
        return None
    return (pt.x, pt.y, w, h)


def pick_output(outputs, rect):
    """(key, (left, top, right, bottom) in that output's coordinates) for the output whose desktop
    rect fully contains the client rect (x, y, w, h); None if it spans outputs or is off-screen."""
    x, y, w, h = rect
    for key, (l, t, r, b) in outputs:
        if l <= x and t <= y and x + w <= r and y + h <= b:
            return key, (x - l, y - t, x - l + w, y - t + h)
    return None


class DxcamBackend:
    """dxcam outputs of every adapter, as [((device_idx, output_idx), desktop rect)]."""

    def __init__(self):
        import dxcam
        self._dxcam = dxcam
        self._factory = getattr(dxcam, "__factory")  # module-level singleton (no public listing API)
        self._outputs = []
        self.refresh()

    def refresh(self) -> None:
        """Re-read every output's desktop rect (monitors moved or changed mode)."""
        outputs = []
        for d, outs in enumerate(self._factory.outputs):
            for o, out in enumerate(outs):
                try:
                    out.update_desc()
                except Exception:  # noqa: BLE001
                    log.warning("dxcam output (%d, %d) desc refresh failed", d, o, exc_info=True)
                dc = out.desc.DesktopCoordinates
                outputs.append(((d, o), (dc.left, dc.top, dc.right, dc.bottom)))
        if outputs != self._outputs:
            log.info("dxcam outputs: %s", outputs)
        self._outputs = outputs

    def outputs(self):
        return self._outputs

    def create(self, key):
        cam = self._dxcam.create(device_idx=key[0], output_idx=key[1], output_color="BGR")
        if cam is None:
            raise RuntimeError("dxcam.create returned None")
        return cam


_DEFAULT = object()


class Grabber:
    """grab(rect, base) -> uint8 gray at base resolution; crops(rect, base, boxes) -> BGR icon crops.

    Uses the dxcam output that contains the window (any monitor); mss when the window spans
    outputs or that output's camera failed. Outputs are retried per frame, so moving the
    window back onto a working output returns to dxcam.
    """

    def __init__(self, base_size=(1366, 768), dx=_DEFAULT, mss_grab=None, clock=time.monotonic):
        self.base_size = tuple(base_size)
        self._clock = clock
        self._mss = None
        self._mss_grab = mss_grab or self._grab_mss
        self._cams: dict = {}
        self._failed: dict = {}  # output key -> time it failed (retried after OUTPUT_RETRY_S)
        self._backend_name = None
        self._last_full = None  # (rect, key, full-res gray)
        self._last_color = None  # (rect, full-res BGR/BGRA frame) for icon crops; never resized whole
        self._last = None  # (rect, base, base gray)
        if dx is _DEFAULT:
            try:
                dx = DxcamBackend()
            except Exception as e:  # noqa: BLE001
                log.warning("dxcam unavailable (%s); using mss", e)
                dx = None
        self._dx = dx

    def _grab_mss(self, rect) -> np.ndarray:
        import mss
        if self._mss is None:
            self._mss = mss.MSS()
        x, y, w, h = rect
        shot = self._mss.grab({"left": x, "top": y, "width": w, "height": h})
        return np.asarray(shot)  # BGRA; _grab_full keeps it for icon crops and converts to gray

    def _note_backend(self, name, rect) -> None:
        if name != self._backend_name:
            log.info("capture backend: %s (window %s)", name, rect)
            self._backend_name = name

    def _fail(self, key) -> None:
        self._failed[key] = self._clock()

    def _camera(self, key):
        cam = self._cams.get(key)
        if cam is not None:
            return cam
        try:
            cam = self._dx.create(key)
        except Exception as e:  # noqa: BLE001
            log.warning("dxcam output %s unavailable (%s); using mss there", key, e)
            self._fail(key)
            return None
        self._cams[key] = cam
        return cam

    def _retry_due(self) -> None:
        """Forget failures older than OUTPUT_RETRY_S, re-listing the outputs first."""
        now = self._clock()
        due = [k for k, t in self._failed.items() if now - t >= OUTPUT_RETRY_S]
        if not due:
            return
        refresh = getattr(self._dx, "refresh", None)
        if refresh is not None:
            try:
                refresh()
            except Exception:  # noqa: BLE001
                log.warning("dxcam output refresh failed", exc_info=True)
        for k in due:
            del self._failed[k]
            log.info("retrying dxcam output %s", k)

    def _grab_full(self, rect):
        """(key, full-res gray), or (key, None) when dxcam reports the frame unchanged."""
        if self._dx is not None and self._failed:
            self._retry_due()
        sel = pick_output(self._dx.outputs(), rect) if self._dx is not None else None
        if sel is not None and sel[0] not in self._failed:
            key, region = sel
            cam = self._camera(key)
            if cam is not None:
                try:
                    frame = cam.grab(region=region)
                except Exception as e:  # noqa: BLE001
                    log.warning("dxcam grab failed on output %s (%s); using mss there", key, e)
                    self._fail(key)
                    self._release(key)
                else:
                    self._note_backend(f"dxcam output {key}", rect)
                    if frame is not None:
                        self._last_color = (rect, frame)
                        return key, cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
                    last = self._last_full
                    if last is not None and last[0] == rect and last[1] == key:
                        return key, None
        self._note_backend("mss", rect)
        frame = self._mss_grab(rect)
        if frame.ndim == 2:  # gray-only source: no icon crops
            self._last_color = None
            return "mss", frame
        self._last_color = (rect, frame)
        code = cv2.COLOR_BGRA2GRAY if frame.shape[2] == 4 else cv2.COLOR_BGR2GRAY
        return "mss", cv2.cvtColor(frame, code)

    def grab(self, rect, base_size=None) -> np.ndarray:
        base = tuple(base_size) if base_size is not None else self.base_size
        key, gray = self._grab_full(rect)
        if gray is None:
            if self._last is not None and self._last[0] == rect and self._last[1] == base:
                return self._last[2]
            gray = self._last_full[2]
        else:
            self._last_full = (rect, key, gray)
        out = to_base(gray, base)
        self._last = (rect, base, out)
        return out

    def rebase(self, rect, base) -> np.ndarray:
        """The last captured frame at another base (no new capture), so several bases can be scored on
        the same frame; grabs if there is no frame for this rect yet."""
        if self._last_full is None or self._last_full[0] != rect:
            return self.grab(rect, base)
        return to_base(self._last_full[2], tuple(base))

    def crops(self, rect, base, boxes) -> list:
        """BGR crops of base-pixel boxes from the last captured colour frame of `rect` (None per box
        that is off-frame, or for every box without a colour frame). Only the boxes are resampled."""
        last = self._last_color
        if last is None or last[0] != rect:
            return [None] * len(boxes)
        return [None if b is None else crop_to_base(last[1], tuple(base), b) for b in boxes]

    def _release(self, key) -> None:
        cam = self._cams.pop(key, None)
        if cam is not None:
            try:
                cam.release()
            except Exception:  # noqa: BLE001
                log.warning("dxcam release failed", exc_info=True)

    def close(self) -> None:
        """Release dxcam cameras and mss explicitly (COM objects must not outlive the interpreter)."""
        for key in list(self._cams):
            self._release(key)
        if self._mss is not None:
            try:
                self._mss.close()
            except Exception:  # noqa: BLE001
                pass
            self._mss = None
        self._last_full = self._last = self._last_color = None
