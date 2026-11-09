"""Click-through transparent always-on-top overlay (Tk) that draws the side panel and the EXP box."""
from __future__ import annotations

import ctypes
import logging
import tkinter as tk
import tkinter.font as tkfont
from collections import OrderedDict
from dataclasses import dataclass

from maplestory_classic_tool import draw
from maplestory_classic_tool.exp import exp_prims
from maplestory_classic_tool.sidelist import OTHER, PROFIT, panel_prims

log = logging.getLogger("maplestory_classic_tool")

KEY = "#010101"
PHOTO_CACHE_MAX = 128  # decoded icon PhotoImages kept across redraws (keyed by their base64 PNG)

GWL_EXSTYLE = -20
WS_EX_TRANSPARENT = 0x20
WS_EX_TOOLWINDOW = 0x80
WS_EX_LAYERED = 0x80000
WS_EX_NOACTIVATE = 0x08000000
# Windows 10 2004+: the window is left out of screen captures (desktop duplication, BitBlt, Snipping Tool,
# OBS) but stays visible on the monitor. The overlay's own capture must not see it: the EXP box covers the
# status bar's "LV." anchor and level tiles, and seeing itself made the box find no bar, hide, find the bar,
# show again (flicker), and copy its own pixels as the level.
WDA_EXCLUDEFROMCAPTURE = 0x11


@dataclass(frozen=True)
class Label:
    x: int
    y: int
    text: str
    color: str


def geometry_string(vx: int, vy: int, vw: int, vh: int) -> str:
    """Tk geometry for a window at virtual-screen origin; "+-1920" is a negative x offset."""
    return f"{vw}x{vh}+{vx}+{vy}"


def to_screen(anns, client_xy, scale: float) -> list[Label]:
    cx, cy = client_xy
    return [Label(round(a.x * scale + cx), round(a.y * scale + cy), a.text,
                  PROFIT if a.status == "profit" else OTHER) for a in anns]


class Overlay:
    def __init__(self, root: "tk.Toplevel | tk.Tk"):
        self.root = root
        u = ctypes.windll.user32
        self.vx, self.vy = u.GetSystemMetrics(76), u.GetSystemMetrics(77)
        vw, vh = u.GetSystemMetrics(78), u.GetSystemMetrics(79)
        root.overrideredirect(True)
        root.attributes("-topmost", True)
        root.configure(bg=KEY)
        root.attributes("-transparentcolor", KEY)
        root.geometry(geometry_string(self.vx, self.vy, vw, vh))
        self.canvas = tk.Canvas(root, bg=KEY, highlightthickness=0, bd=0)
        self.canvas.pack(fill="both", expand=True)
        self._images = []  # PhotoImages on the canvas; Tk drops an image whose Python object is collected
        self._photos: OrderedDict = OrderedDict()  # b64 PNG -> PhotoImage (LRU, main thread only)
        self._fonts: dict = {}  # (px, bold) -> tkfont.Font for measuring
        self.measure = draw.cached_measure(self._measure)  # Tk font metrics are slow; texts repeat
        root.update_idletasks()
        self.hwnd = self._click_through()
        self.excluded = self._exclude_from_capture()
        root.withdraw()  # hidden until deiconify() (scanning On)

    def _click_through(self) -> None:
        u = ctypes.windll.user32
        u.GetParent.restype = ctypes.c_void_p
        u.GetParent.argtypes = [ctypes.c_void_p]
        u.GetWindowLongW.argtypes = [ctypes.c_void_p, ctypes.c_int]
        u.SetWindowLongW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_long]
        hwnd = u.GetParent(self.root.winfo_id()) or self.root.winfo_id()
        style = u.GetWindowLongW(hwnd, GWL_EXSTYLE)
        u.SetWindowLongW(hwnd, GWL_EXSTYLE, style | WS_EX_LAYERED | WS_EX_TRANSPARENT
                         | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE)
        return hwnd

    def _exclude_from_capture(self) -> bool:
        """Keep the overlay out of screen captures (it stays visible); False when Windows refuses."""
        try:
            u = ctypes.windll.user32
            u.SetWindowDisplayAffinity.argtypes = [ctypes.c_void_p, ctypes.c_uint]
            if u.SetWindowDisplayAffinity(self.hwnd, WDA_EXCLUDEFROMCAPTURE):
                return True
            log.warning("overlay not excluded from capture (error %d); the EXP box may flicker",
                        ctypes.GetLastError())
        except Exception:  # noqa: BLE001
            log.warning("overlay not excluded from capture", exc_info=True)
        return False

    def capture_affinity(self) -> int:
        """The window's display affinity as Windows reports it (WDA_EXCLUDEFROMCAPTURE when excluded)."""
        u = ctypes.windll.user32
        u.GetWindowDisplayAffinity.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint)]
        v = ctypes.c_uint(0)
        u.GetWindowDisplayAffinity(self.hwnd, ctypes.byref(v))
        return v.value

    def withdraw(self) -> None:
        self.root.withdraw()

    def deiconify(self) -> None:
        self.root.deiconify()
        self.root.attributes("-topmost", True)
        if not self.excluded:
            self.excluded = self._exclude_from_capture()

    def photo(self, b64: str) -> "tk.PhotoImage":
        """PhotoImage for a base64 PNG, decoded once and reused across redraws (bounded LRU)."""
        img = self._photos.get(b64)
        if img is not None:
            self._photos.move_to_end(b64)
            return img
        img = self._photos[b64] = tk.PhotoImage(master=self.canvas, data=b64)
        while len(self._photos) > PHOTO_CACHE_MAX:
            self._photos.popitem(last=False)  # still referenced by _images if on the canvas now
        return img

    def _measure(self, text: str, px: float, bold: bool) -> float:
        """Width of `text` in Segoe UI at `px` pixels (fonts cached per size)."""
        key = (max(6, round(px)), bool(bold))
        font = self._fonts.get(key)
        if font is None:
            font = self._fonts[key] = tkfont.Font(root=self.canvas, family=draw.FONT_FAMILY, size=-key[0],
                                                  weight="bold" if bold else "normal")
        return font.measure(text)

    def _draw(self, prims, tag: str) -> list:
        """Draw screen-pixel primitives under `tag`; returns the PhotoImages placed (to keep alive)."""
        c = self.canvas
        ox, oy = -self.vx, -self.vy
        images = []
        for q in prims:
            if isinstance(q, draw.Rect) and q.radius < 1:  # square corners: a plain rectangle
                c.create_rectangle(q.x0 + ox, q.y0 + oy, q.x1 + ox, q.y1 + oy, fill=q.fill or "",
                                   outline=q.outline or "", width=max(1, round(q.width)) if q.outline else 0,
                                   tags=tag)
            elif isinstance(q, draw.Rect):
                pts = [v for pt in draw.round_rect_points(q.x0 + ox, q.y0 + oy, q.x1 + ox, q.y1 + oy, q.radius)
                       for v in pt]
                c.create_polygon(pts, fill=q.fill or "", outline=q.outline or "",
                                 width=max(1, round(q.width)) if q.outline else 0, tags=tag)
            elif isinstance(q, draw.Gradient):
                for y, xa, xb, color in draw.gradient_spans(q.x0 + ox, q.y0 + oy, q.x1 + ox, q.y1 + oy, q.top,
                                                            q.bottom, q.radius):
                    if xb > xa:
                        c.create_line(xa, y, xb, y, fill=color, tags=tag)
            elif isinstance(q, draw.Line):
                w = max(1, round(q.width))
                c.create_rectangle(q.x0 + ox, q.y0 + oy, max(q.x1, q.x0 + w) + ox, max(q.y1, q.y0 + w) + oy,
                                   fill=q.color, outline="", tags=tag)
            elif isinstance(q, draw.Text):
                font = (draw.FONT_FAMILY, -max(6, round(q.px)), "bold" if q.bold else "normal")
                c.create_text(round(q.x + ox), round(q.y + oy), text=q.text, fill=q.color, font=font,
                              anchor=q.anchor, tags=tag)
            elif isinstance(q, draw.Image):
                img = self.photo(q.data)
                images.append(img)
                c.create_image(round(q.x + ox), round(q.y + oy), image=img, anchor="nw", tags=tag)
        return images

    def show_panel(self, p) -> None:
        """Draw one side panel (screen-pixel Panel from sidelist.panel_to_screen)."""
        self.canvas.delete("lbl")
        prims, _, _ = panel_prims(p, self.measure)
        self._images = self._draw(prims, "lbl")

    def clear(self) -> None:
        """Remove the side panel (the EXP box stays)."""
        self.canvas.delete("lbl")
        self._images = []

    def show_exp(self, box) -> None:
        """Draw the EXP box (screen-pixel exp.ExpBox), or remove it when box is None."""
        self.canvas.delete("exp")
        if box is not None:
            self._draw(exp_prims(box, self.measure), "exp")

    def clear_exp(self) -> None:
        self.canvas.delete("exp")

    def status(self, text: str | None) -> None:
        self.canvas.delete("status")
        if text:
            self.canvas.create_text(20, 20, text=text, fill=OTHER, anchor="nw",
                                    font=("Segoe UI", -24, "bold"), tags="status")
