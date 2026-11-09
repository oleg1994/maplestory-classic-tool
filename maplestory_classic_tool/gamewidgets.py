"""Tk widgets drawn in the game's window style (control window): frame, title bar, buttons, checkboxes.

Everything is drawn with Canvas shapes in colours sampled from the shop window (sidelist.SHOP_*);
no game art is used. Sizes are in 96-DPI pixels, multiplied by `scale_for(root)`.
"""
from __future__ import annotations

import base64
import ctypes
import logging
import struct
import tkinter as tk
from pathlib import Path

from maplestory_classic_tool.sidelist import (DEAL_BADGE, DEAL_BADGE_EDGE, SHOP_EDGE, SHOP_FRAME, SHOP_FRAME_HI,
                                SHOP_LIST_EDGE, SHOP_ROW_BG, SHOP_ROW_LINE, SHOP_TEXT,
                                SHOP_TITLE_BG, SHOP_TITLE_EDGE)

log = logging.getLogger(__name__)

FONT = "Segoe UI"
WINDOW_BG = "#e9edf1"  # inside the frame band
TITLE_TEXT = "#ffffff"
TEXT_SHADOW = "#4a535c"
CHECK_BOX = "#ffffff"
CHECK_TICK = DEAL_BADGE_EDGE
WARN_TEXT = "#c0392b"

# top / bottom of the bevel gradient, edge, text; green = SELLER INFO, orange = BUY ITEM / LEAVE STORE
_STYLES = {
    "green": ("#b3d36b", DEAL_BADGE, DEAL_BADGE_EDGE, "#ffffff"),
    "orange": ("#fbb162", "#e9781f", "#a4520f", "#ffffff"),
}
_DISABLED = ("#cdd2d7", "#b2b8be", "#8e969e", "#eef0f2")


def blend(a: str, b: str, t: float) -> str:
    """Colour between a (t=0) and b (t=1)."""
    ca = [int(a[i:i + 2], 16) for i in (1, 3, 5)]
    cb = [int(b[i:i + 2], 16) for i in (1, 3, 5)]
    return "#" + "".join(f"{round(x + (y - x) * t):02x}" for x, y in zip(ca, cb))


def button_colors(style: str, enabled: bool, hover: bool = False, pressed: bool = False) -> dict:
    """Fill gradient, edge and text colour of a game button in the given state."""
    if not enabled:
        top, bottom, edge, text = _DISABLED
    else:
        top, bottom, edge, text = _STYLES[style]
        if hover:
            top, bottom = blend(top, "#ffffff", 0.25), blend(bottom, "#ffffff", 0.18)
        if pressed:
            top, bottom = bottom, top
    return {"top": top, "bottom": bottom, "edge": edge, "text": text}


def scale_for(widget) -> float:
    """Screen pixels per 96-DPI pixel (the process is DPI aware, so Tk sees real pixels)."""
    try:
        return max(1.0, float(widget.winfo_fpixels("1i")) / 96.0)
    except tk.TclError:
        return 1.0


def _gradient(canvas, x0, y0, x1, y1, top, bottom, radius=2, tag=""):
    """Vertical gradient box with corners cut `radius` pixels (pixel-art rounding)."""
    h = max(1, int(y1 - y0))
    for i in range(h):
        inset = max(0, radius - i, radius - (h - 1 - i))
        canvas.create_line(x0 + inset, y0 + i, x1 - inset, y0 + i, fill=blend(top, bottom, i / max(1, h - 1)),
                           tags=tag)


def _outline(canvas, x0, y0, x1, y1, color, radius=2, tag=""):
    r = radius
    pts = [x0 + r, y0, x1 - r, y0, x1, y0 + r, x1, y1 - r, x1 - r, y1, x0 + r, y1, x0, y1 - r, x0, y0 + r]
    canvas.create_polygon(pts, outline=color, fill="", tags=tag)


def _shadow_text(canvas, x, y, text, font, color, shadow, anchor="center", tag=""):
    canvas.create_text(x + 1, y + 1, text=text, font=font, fill=shadow, anchor=anchor, tags=tag)
    canvas.create_text(x, y, text=text, font=font, fill=color, anchor=anchor, tags=tag)


class GameButton(tk.Canvas):
    """Bevelled caps-text button; `.state()` / `.instate()` / `.invoke()` like ttk.Button."""

    def __init__(self, master, text, command=None, style="green", width=96, height=24, scale=1.0, bg=WINDOW_BG):
        self._s = scale
        super().__init__(master, width=round(width * scale), height=round(height * scale), bg=bg,
                         highlightthickness=0, bd=0, cursor="hand2")
        self.text, self.command, self.style = text.upper(), command, style
        self._enabled, self._hover, self._pressed = True, False, False
        self._font = (FONT, 9, "bold")
        self.bind("<Enter>", lambda e: self._set(hover=True))
        self.bind("<Leave>", lambda e: self._set(hover=False, pressed=False))
        self.bind("<ButtonPress-1>", lambda e: self._set(pressed=True))
        self.bind("<ButtonRelease-1>", self._release)
        self.bind("<Configure>", lambda e: self.redraw())
        self.redraw()

    def _set(self, **kw):
        for k, v in kw.items():
            setattr(self, "_" + k, v)
        self.redraw()

    def _release(self, event):
        inside = 0 <= event.x < self.winfo_width() and 0 <= event.y < self.winfo_height()
        was = self._pressed
        self._set(pressed=False)
        if was and inside:
            self.invoke()

    def invoke(self):
        if self._enabled and self.command is not None:
            self.command()

    def state(self, spec=None):
        """ttk-style: state(["disabled"]) / state(["!disabled"]); returns the current flags."""
        for flag in spec or ():
            if flag == "disabled":
                self._enabled = False
            elif flag == "!disabled":
                self._enabled = True
        self.configure(cursor="hand2" if self._enabled else "arrow")
        self.redraw()
        return () if self._enabled else ("disabled",)

    def instate(self, spec) -> bool:
        flags = set(self.state())
        return all((f[1:] not in flags) if f.startswith("!") else (f in flags) for f in spec)

    def redraw(self):
        self.delete("all")
        w = max(int(self.cget("width")), self.winfo_width() if self.winfo_ismapped() else 0)
        h = max(int(self.cget("height")), self.winfo_height() if self.winfo_ismapped() else 0)
        c = button_colors(self.style, self._enabled, self._hover, self._pressed)
        r = max(2, round(3 * self._s))
        _gradient(self, 1, 1, w - 1, h - 1, c["top"], c["bottom"], radius=r)
        if self._enabled and not self._pressed:  # light line along the top: the game's bevel
            self.create_line(r + 1, 2, w - r - 1, 2, fill=blend(c["top"], "#ffffff", 0.55))
        _outline(self, 0, 0, w - 1, h - 1, c["edge"], radius=r)
        dy = 1 if self._pressed else 0
        shadow = blend(c["edge"], "#000000", 0.2) if self._enabled else c["bottom"]
        _shadow_text(self, w / 2, h / 2 + dy, self.text, self._font, c["text"], shadow)


class GameCheck(tk.Canvas):
    """Shop-row checkbox: square box with a tick, label text; click anywhere on the row toggles."""

    def __init__(self, master, text, variable, command=None, width=236, height=24, scale=1.0, bg=SHOP_ROW_BG):
        self._s = scale
        super().__init__(master, width=round(width * scale), height=round(height * scale), bg=bg,
                         highlightthickness=0, bd=0, cursor="hand2")
        self.text, self.variable, self.command = text, variable, command
        self._hover = False
        self._font = (FONT, 9)
        self.bind("<Enter>", lambda e: self._hov(True))
        self.bind("<Leave>", lambda e: self._hov(False))
        self.bind("<ButtonRelease-1>", lambda e: self.toggle())
        self._trace = variable.trace_add("write", lambda *a: self.redraw())
        self.bind("<Destroy>", self._untrace)
        self.redraw()

    def _untrace(self, event):
        if event.widget is self:
            try:
                self.variable.trace_remove("write", self._trace)
            except tk.TclError:
                pass

    def _hov(self, value):
        self._hover = value
        self.redraw()

    def toggle(self):
        self.variable.set(not bool(self.variable.get()))
        if self.command is not None:
            self.command()

    def redraw(self):
        self.delete("all")
        s = self._s
        h = int(self.cget("height"))
        box = round(13 * s)
        x0, y0 = round(8 * s), (h - box) // 2
        edge = blend(SHOP_LIST_EDGE, "#000000", 0.25) if self._hover else SHOP_LIST_EDGE
        self.create_rectangle(x0, y0, x0 + box, y0 + box, fill=CHECK_BOX, outline=edge)
        if bool(self.variable.get()):
            lw = max(2, round(2 * s))
            self.create_line(x0 + 0.22 * box, y0 + 0.52 * box, x0 + 0.42 * box, y0 + 0.74 * box,
                             x0 + 0.80 * box, y0 + 0.26 * box, fill=CHECK_TICK, width=lw,
                             capstyle="round", joinstyle="round", tags="tick")
        self.create_text(x0 + box + round(8 * s), h / 2, text=self.text, font=self._font, fill=SHOP_TEXT,
                         anchor="w")


class TitleBar(tk.Canvas):
    """Blue-grey title band: icon, bold white title, minimise and close boxes; drags its toplevel."""

    def __init__(self, master, title, on_close, on_minimize=None, icon=None, width=260, height=26, scale=1.0):
        self._s = scale
        super().__init__(master, width=round(width * scale), height=round(height * scale), bg=SHOP_FRAME,
                         highlightthickness=0, bd=0)
        self.title, self.on_close, self.on_minimize, self.icon = title, on_close, on_minimize, icon
        self._drag = None
        self._hot = None
        self.bind("<ButtonPress-1>", self._press)
        self.bind("<B1-Motion>", self._move)
        self.bind("<ButtonRelease-1>", self._release)
        self.bind("<Motion>", self._motion)
        self.bind("<Leave>", lambda e: self._set_hot(None))
        self.redraw()

    def _boxes(self):
        s = self._s
        w, h = int(self.cget("width")), int(self.cget("height"))
        size, gap = round(16 * s), round(4 * s)
        y0 = (h - size) // 2
        close = (w - size - round(2 * s), y0, w - round(2 * s), y0 + size)
        mini = (close[0] - gap - size, y0, close[0] - gap, y0 + size)
        boxes = {"close": close}
        if self.on_minimize is not None:
            boxes["min"] = mini
        return boxes

    def _hit(self, x, y):
        for name, (x0, y0, x1, y1) in self._boxes().items():
            if x0 <= x <= x1 and y0 <= y <= y1:
                return name
        return None

    def _set_hot(self, name):
        if name != self._hot:
            self._hot = name
            self.redraw()

    def _motion(self, e):
        self._set_hot(self._hit(e.x, e.y))

    def _press(self, e):
        if self._hit(e.x, e.y) is None:
            top = self.winfo_toplevel()
            self._drag = (e.x_root - top.winfo_x(), e.y_root - top.winfo_y())

    def _move(self, e):
        if self._drag is not None:
            top = self.winfo_toplevel()
            top.geometry(f"+{e.x_root - self._drag[0]}+{e.y_root - self._drag[1]}")

    def _release(self, e):
        if self._drag is not None:
            self._drag = None
            return
        hit = self._hit(e.x, e.y)
        if hit == "close":
            self.on_close()
        elif hit == "min" and self.on_minimize is not None:
            self.on_minimize()

    def redraw(self):
        self.delete("all")
        s = self._s
        h = int(self.cget("height"))
        x = round(4 * s)
        if self.icon is not None:
            self.create_image(x, h / 2, image=self.icon, anchor="w")
            x += self.icon.width() + round(6 * s)
        _shadow_text(self, x, h / 2, self.title, (FONT, 9, "bold"), TITLE_TEXT, TEXT_SHADOW, anchor="w")
        for name, (x0, y0, x1, y1) in self._boxes().items():
            hot = name == self._hot
            top = "#ffffff" if hot else SHOP_FRAME_HI
            bottom = blend(SHOP_FRAME_HI, SHOP_FRAME, 0.6 if hot else 0.9)
            _gradient(self, x0 + 1, y0 + 1, x1, y1, top, bottom, radius=2)
            _outline(self, x0, y0, x1, y1, SHOP_EDGE, radius=2)
            cx, cy, k = (x0 + x1) / 2, (y0 + y1) / 2, (x1 - x0) * 0.22
            lw = max(2, round(2 * s))
            color = WARN_TEXT if (hot and name == "close") else SHOP_EDGE
            if name == "close":
                self.create_line(cx - k, cy - k, cx + k, cy + k, fill=color, width=lw)
                self.create_line(cx - k, cy + k, cx + k, cy - k, fill=color, width=lw)
            else:
                self.create_line(cx - k, cy + k * 0.6, cx + k, cy + k * 0.6, fill=color, width=lw)


def framed(root, scale: float = 1.0) -> tk.Frame:
    """Game window frame in `root`: dark outline around a blue-grey band. Returns the band: pack the
    title bar at its top, then `content_area(band)`."""
    outline = tk.Frame(root, bg=SHOP_EDGE, padx=1, pady=1)
    outline.pack(fill="both", expand=True)
    band = tk.Frame(outline, bg=SHOP_FRAME, padx=round(5 * scale), pady=0)
    band.pack(fill="both", expand=True)
    return band


def content_area(band, scale: float = 1.0) -> tk.Frame:
    """Light content panel inside the band (under the title bar), edged like the shop's inner panels."""
    s = scale
    hi = tk.Frame(band, bg=SHOP_FRAME_HI, padx=1, pady=1)
    hi.pack(fill="both", expand=True, pady=(0, round(5 * s)))
    inner = tk.Frame(hi, bg=WINDOW_BG, padx=round(10 * s), pady=round(10 * s))
    inner.pack(fill="both", expand=True)
    return inner


def status_strip(master, textvariable, scale: float = 1.0, width=236, height=26):
    """White box like the shop's title strip, with a status dot and text. Returns (canvas, set_dot)."""
    s = scale
    w, h = round(width * s), round(height * s)
    c = tk.Canvas(master, width=w, height=h, bg=WINDOW_BG, highlightthickness=0, bd=0)
    c.create_rectangle(0, 0, w - 1, h - 1, fill=SHOP_TITLE_BG, outline=SHOP_TITLE_EDGE)
    d = round(10 * s)
    dot = c.create_oval(round(9 * s), (h - d) / 2, round(9 * s) + d, (h + d) / 2, fill="#9aa3ad",
                        outline=blend("#9aa3ad", "#000000", 0.3))
    text = c.create_text(round(9 * s) + d + round(8 * s), h / 2, text=textvariable.get(),
                         font=(FONT, 10, "bold"), fill=SHOP_TEXT, anchor="w")
    textvariable.trace_add("write", lambda *a: c.itemconfigure(text, text=textvariable.get()))

    def set_dot(color: str) -> None:
        c.itemconfigure(dot, fill=color, outline=blend(color, "#000000", 0.3))

    return c, set_dot


def option_list(master) -> tuple[tk.Frame, tk.Frame]:
    """Box like the shop's item list: blue border, rows separated by thin lines.
    Returns (border, body): pack `border`; create rows in `body` and pack them with `pack_row`."""
    border = tk.Frame(master, bg=SHOP_LIST_EDGE, padx=1, pady=1)
    body = tk.Frame(border, bg=SHOP_ROW_LINE)
    body.pack(fill="both", expand=True)
    return border, body


def pack_row(widget, first: bool) -> None:
    """Pack a row into an option_list body, leaving a 1 px separator line above all but the first."""
    widget.pack(fill="x", pady=(0 if first else 1, 0))


def icon_image(ico_path: Path, size: int):
    """tk.PhotoImage of the .ico's PNG entry closest to `size` (the icon file holds PNG entries)."""
    data = Path(ico_path).read_bytes()
    _, _, count = struct.unpack_from("<HHH", data, 0)
    best = None
    for i in range(count):
        w, h, _, _, _, _, length, offset = struct.unpack_from("<BBBBHHII", data, 6 + 16 * i)
        w = w or 256
        blob = data[offset:offset + length]
        if not blob.startswith(b"\x89PNG"):
            continue
        key = (w < size, abs(w - size))  # prefer the smallest entry at least `size` wide
        if best is None or key < best[0]:
            best = (key, w, blob)
    if best is None:
        return None
    img = tk.PhotoImage(data=base64.b64encode(best[2]).decode("ascii"))
    factor = best[1] // size
    return img.subsample(factor) if factor > 1 else img


GWL_EXSTYLE = -20
WS_EX_APPWINDOW = 0x00040000
WS_EX_TOOLWINDOW = 0x00000080
SW_MINIMIZE = 6


def _hwnd(root) -> int:
    return ctypes.windll.user32.GetParent(root.winfo_id()) or root.winfo_id()


def set_app_window(root) -> None:
    """Keep a taskbar button and Alt+Tab entry for a borderless (override-redirect) window."""
    try:
        u = ctypes.windll.user32
        u.GetWindowLongW.restype = ctypes.c_long
        hwnd = _hwnd(root)
        style = u.GetWindowLongW(hwnd, GWL_EXSTYLE)
        u.SetWindowLongW(hwnd, GWL_EXSTYLE, (style & ~WS_EX_TOOLWINDOW) | WS_EX_APPWINDOW)
    except Exception:  # noqa: BLE001
        log.warning("could not give the window a taskbar button", exc_info=True)


def minimize(root) -> None:
    """Minimise a borderless window (Tk's iconify refuses override-redirect windows)."""
    try:
        ctypes.windll.user32.ShowWindow(_hwnd(root), SW_MINIMIZE)
    except Exception:  # noqa: BLE001
        log.warning("could not minimise", exc_info=True)
