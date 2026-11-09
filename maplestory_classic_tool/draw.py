"""Drawing primitives shared by the Tk overlay and the --image preview (cv2), in screen pixels.

Panels and the EXP box are laid out once as a list of primitives (sidelist.panel_prims,
exp.exp_prims) given a text `measure(text, px, bold) -> width`; each renderer only draws them.
Text is positioned by its vertical centre (anchor "w": left edge, "e": right edge).
"""
from __future__ import annotations

import base64
import math
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

FONT_FAMILY = "Segoe UI"
_FONTS = Path("C:/Windows/Fonts")
_TTF = {True: "segoeuib.ttf", False: "segoeui.ttf"}


@dataclass(frozen=True)
class Rect:
    """Filled and/or outlined box [x0, x1] x [y0, y1]; radius > 0 rounds the corners."""
    x0: float
    y0: float
    x1: float
    y1: float
    fill: str | None
    outline: str | None = None
    width: float = 1
    radius: float = 0


@dataclass(frozen=True)
class Gradient:
    """Vertical gradient fill from `top` to `bottom`, rounded like Rect."""
    x0: float
    y0: float
    x1: float
    y1: float
    top: str
    bottom: str
    radius: float = 0


@dataclass(frozen=True)
class Line:
    x0: float
    y0: float
    x1: float
    y1: float
    color: str
    width: float = 1


@dataclass(frozen=True)
class Text:
    x: float
    y: float  # vertical centre
    text: str
    color: str
    px: float
    bold: bool = False
    anchor: str = "w"  # "w": x is the left edge, "e": the right edge


@dataclass(frozen=True)
class Image:
    x: float
    y: float
    data: str  # base64 PNG (screen size)


MEASURE_CACHE_MAX = 4096


def cached_measure(measure):
    """measure(text, px, bold) memoised per (text, rounded px, bold) (bounded; cleared when full)."""
    cache: dict = {}

    def cached(text: str, px: float, bold: bool) -> float:
        key = (text, round(px), bool(bold))
        w = cache.get(key)
        if w is None:
            if len(cache) >= MEASURE_CACHE_MAX:
                cache.clear()
            w = cache[key] = measure(text, px, bold)
        return w
    return cached


def _rgb(hex_color: str) -> tuple:
    h = hex_color.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def bgr(hex_color: str) -> tuple:
    r, g, b = _rgb(hex_color)
    return (b, g, r)


def lerp(a: str, b: str, t: float) -> str:
    ca, cb = _rgb(a), _rgb(b)
    return "#%02x%02x%02x" % tuple(round(x + (y - x) * t) for x, y in zip(ca, cb))


def round_rect_points(x0, y0, x1, y1, r, steps: int = 4) -> list:
    """Polygon of a rounded rectangle (integer points); a plain rectangle when r < 1."""
    x0, y0, x1, y1 = (round(v) for v in (x0, y0, x1, y1))
    r = min(r, (x1 - x0) / 2, (y1 - y0) / 2)
    if r < 1:
        return [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
    pts = []
    for cx, cy, a0 in ((x1 - r, y0 + r, -90), (x1 - r, y1 - r, 0), (x0 + r, y1 - r, 90), (x0 + r, y0 + r, 180)):
        for i in range(steps + 1):
            a = math.radians(a0 + 90 * i / steps)
            pts.append((round(cx + r * math.cos(a)), round(cy + r * math.sin(a))))
    out = []
    for p in pts:
        if not out or out[-1] != p:
            out.append(p)
    return out


def _inset(r: float, dy: float) -> float:
    """Horizontal inset of a rounded corner of radius r at dy rows (pixel centre) from the edge."""
    if r < 1 or dy >= r:
        return 0.0
    d = r - dy - 0.5
    return r - math.sqrt(max(0.0, r * r - d * d))


def gradient_spans(x0, y0, x1, y1, top: str, bottom: str, r: float = 0) -> list:
    """[(y, xa, xb, colour)] one per pixel row; corner rows are inset for the radius."""
    x0, y0, x1, y1 = (round(v) for v in (x0, y0, x1, y1))
    h = max(1, y1 - y0)
    out = []
    for i in range(h):
        k = round(_inset(r, min(i, h - 1 - i)))
        out.append((y0 + i, x0 + k, x1 - k, lerp(top, bottom, i / max(1, h - 1))))
    return out


class CvRenderer:
    """Draws primitives onto a BGR image (the --image preview). Uses Segoe UI through OpenCV's
    TrueType text when available, else a Hershey font."""

    def __init__(self, img: np.ndarray):
        self.img = img
        self._faces: dict = {}

    def _face(self, bold: bool):
        if bold not in self._faces:
            face = None
            path = _FONTS / _TTF[bold]
            if hasattr(cv2, "FontFace") and path.exists():
                try:
                    face = cv2.FontFace(str(path))
                except Exception:  # noqa: BLE001
                    face = None
            self._faces[bold] = face
        return self._faces[bold]

    def measure(self, text: str, px: float, bold: bool) -> float:
        face = self._face(bold)
        if face is None:
            scale = px / 22.0 * 0.88
            return cv2.getTextSize(_ascii(text), cv2.FONT_HERSHEY_SIMPLEX, scale, max(1, round(px / 12)))[0][0]
        r = cv2.getTextSize((0, 0), text, (0, 0), face, max(1, round(px)))
        return float(r[0] + r[2])

    def render(self, prims) -> None:
        for p in prims:
            getattr(self, "_" + type(p).__name__.lower())(p)

    def _rect(self, p: Rect) -> None:
        pts = np.array(round_rect_points(p.x0, p.y0, p.x1, p.y1, p.radius), np.int32)
        if p.fill:
            cv2.fillPoly(self.img, [pts], bgr(p.fill))
        if p.outline:
            cv2.polylines(self.img, [pts], True, bgr(p.outline), max(1, round(p.width)))

    def _gradient(self, p: Gradient) -> None:
        for y, xa, xb, color in gradient_spans(p.x0, p.y0, p.x1, p.y1, p.top, p.bottom, p.radius):
            if xb > xa:
                cv2.line(self.img, (xa, y), (xb - 1, y), bgr(color), 1)

    def _line(self, p: Line) -> None:
        w = max(1, round(p.width))
        x0, y0, x1, y1 = (round(v) for v in (p.x0, p.y0, p.x1, p.y1))
        if y0 == y1:  # horizontal: w rows starting at y0
            cv2.rectangle(self.img, (x0, y0), (x1 - 1, y0 + w - 1), bgr(p.color), -1)
        elif x0 == x1:  # vertical: w columns starting at x0
            cv2.rectangle(self.img, (x0, y0), (x0 + w - 1, y1 - 1), bgr(p.color), -1)
        else:
            cv2.line(self.img, (x0, y0), (x1, y1), bgr(p.color), w)

    def _text(self, p: Text) -> None:
        face = self._face(p.bold)
        w = self.measure(p.text, p.px, p.bold)
        x = p.x - w if p.anchor == "e" else p.x
        if face is None:
            scale = p.px / 22.0 * 0.88
            th = max(1, round(p.px / 12))
            (_, h), _ = cv2.getTextSize("Hg", cv2.FONT_HERSHEY_SIMPLEX, scale, th)
            cv2.putText(self.img, _ascii(p.text), (round(x), round(p.y + h / 2)), cv2.FONT_HERSHEY_SIMPLEX, scale,
                        bgr(p.color), th, cv2.LINE_AA)
            return
        size = max(1, round(p.px))
        r = cv2.getTextSize((0, 0), "Hg", (0, 0), face, size)
        base_y = p.y - (r[1] + r[1] + r[3]) / 2
        cv2.putText(self.img, p.text, (round(x), round(base_y)), bgr(p.color), face, size)

    def _image(self, p: Image) -> None:
        icon = cv2.imdecode(np.frombuffer(base64.b64decode(p.data), np.uint8), cv2.IMREAD_COLOR)
        if icon is None:
            return
        x, y = round(p.x), round(p.y)
        h, w = icon.shape[:2]
        x0, y0, x1, y1 = max(x, 0), max(y, 0), min(x + w, self.img.shape[1]), min(y + h, self.img.shape[0])
        if x1 > x0 and y1 > y0:
            self.img[y0:y1, x0:x1] = icon[y0 - y:y1 - y, x0 - x:x1 - x]


def _ascii(text: str) -> str:
    return text.replace("·", "-").replace("…", "...").replace("—", "-")
