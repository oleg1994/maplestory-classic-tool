"""Draw the app icon (mapletool/assets/app.ico) with OpenCV: no image editor or Pillow needed.

A slate tile in the status bar's colours, an orange maple leaf, and a green "profit" arrow badge.
Drawn once at 1024 px and downscaled per icon size (antialiased); the .ico holds PNG entries.

    python packaging/make_icon.py [--preview out.png]
"""
from __future__ import annotations

import argparse
import struct
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "mapletool" / "assets" / "app.ico"
SIZES = (256, 128, 64, 48, 40, 32, 24, 20, 16)
N = 1024  # drawing canvas


def _bgr(hex_color: str) -> tuple:
    h = hex_color.lstrip("#")
    return int(h[4:6], 16), int(h[2:4], 16), int(h[0:2], 16)


# right half of the leaf on a 0..100 grid (x >= 50), from the top tip clockwise to the stem
_LEAF_RIGHT = [(50, 5), (57, 19), (64, 15), (62, 37), (72, 29), (76, 34), (89, 25), (85, 42), (96, 46),
               (81, 57), (85, 63), (67, 61), (71, 74), (54, 67)]


def leaf_points(cx: float, cy: float, size: float) -> np.ndarray:
    right = [(x, y) for x, y in _LEAF_RIGHT]
    left = [(100 - x, y) for x, y in reversed(right[1:])]
    pts = right + left
    return np.array([[cx + (x - 50) / 100 * size, cy + (y - 45) / 100 * size] for x, y in pts], np.int32)


def _rounded_mask(n: int, x0, y0, x1, y1, r) -> np.ndarray:
    m = np.zeros((n, n), np.uint8)
    cv2.rectangle(m, (x0 + r, y0), (x1 - r, y1), 255, -1)
    cv2.rectangle(m, (x0, y0 + r), (x1, y1 - r), 255, -1)
    for cx, cy in ((x0 + r, y0 + r), (x1 - r, y0 + r), (x0 + r, y1 - r), (x1 - r, y1 - r)):
        cv2.circle(m, (cx, cy), r, 255, -1, cv2.LINE_AA)
    return m


def _vgrad(n: int, y0: int, y1: int, top: str, bottom: str) -> np.ndarray:
    t = np.clip((np.arange(n) - y0) / max(1, y1 - y0), 0, 1)[:, None, None]
    a, b = np.array(_bgr(top), float), np.array(_bgr(bottom), float)
    return np.broadcast_to(a + (b - a) * t, (n, n, 3)).astype(np.float32)


def draw_icon() -> np.ndarray:
    """1024x1024 BGRA."""
    img = np.zeros((N, N, 3), np.float32)
    alpha = np.zeros((N, N), np.float32)

    def paint(mask, color_img, a=1.0):
        k = (mask.astype(np.float32) / 255.0 * a)[..., None]
        img[:] = img * (1 - k) + color_img * k
        alpha[:] = np.maximum(alpha, k[..., 0])

    def solid(c):
        return np.broadcast_to(np.array(_bgr(c), np.float32), (N, N, 3))

    # tile: dark border, slate gradient, bright top line and blue-grey bevel (the status bar look)
    m, r = 40, 190
    paint(_rounded_mask(N, m, m, N - m, N - m, r), solid("#1c2226"))
    inner = _rounded_mask(N, m + 22, m + 22, N - m - 22, N - m - 22, r - 22)
    paint(inner, _vgrad(N, m, N - m, "#6f777c", "#262e33"))
    shine = np.zeros((N, N), np.uint8)
    cv2.ellipse(shine, (N // 2, m + 22), (N // 2 - m - 120, 40), 0, 180, 360, 255, 14, cv2.LINE_AA)
    paint(cv2.bitwise_and(shine, inner), solid("#d7dee0"), 0.55)

    # leaf: soft shadow, dark outline, orange gradient, stem
    cx, cy, size = N * 0.47, N * 0.47, N * 0.70
    pts = leaf_points(cx, cy, size)
    stem = np.array([[cx - 14, cy + 0.20 * size], [cx + 14, cy + 0.20 * size],
                     [cx + 30, cy + 0.47 * size], [cx + 4, cy + 0.47 * size]], np.int32)
    shadow = np.zeros((N, N), np.uint8)
    cv2.fillPoly(shadow, [pts + 18, stem + 18], 255, cv2.LINE_AA)
    shadow = cv2.GaussianBlur(shadow, (0, 0), 14)
    paint(cv2.bitwise_and(shadow, inner), solid("#000000"), 0.45)
    outline = np.zeros((N, N), np.uint8)
    cv2.fillPoly(outline, [stem], 255, cv2.LINE_AA)
    cv2.polylines(outline, [pts], True, 255, 36, cv2.LINE_AA)
    cv2.fillPoly(outline, [pts], 255, cv2.LINE_AA)
    paint(outline, solid("#6e1f08"))
    fill = np.zeros((N, N), np.uint8)
    cv2.fillPoly(fill, [pts], 255, cv2.LINE_AA)
    fill = cv2.erode(fill, np.ones((7, 7), np.uint8))
    top, bottom = int(cy - 0.42 * size), int(cy + 0.30 * size)
    paint(fill, _vgrad(N, top, bottom, "#ffc23a", "#e2441a"))
    # veins
    veins = np.zeros((N, N), np.uint8)
    base = (int(cx), int(cy + 0.20 * size))
    for x, y in ((50, 12), (88, 30), (12, 30), (78, 62), (22, 62)):
        tip = (int(cx + (x - 50) / 100 * size * 0.86), int(cy + (y - 45) / 100 * size * 0.86))
        cv2.line(veins, base, tip, 255, 12, cv2.LINE_AA)
    paint(cv2.bitwise_and(veins, fill), solid("#b8380f"), 0.75)
    stem_fill = np.zeros((N, N), np.uint8)
    cv2.fillPoly(stem_fill, [stem], 255, cv2.LINE_AA)
    paint(cv2.erode(stem_fill, np.ones((9, 9), np.uint8)), solid("#9a3a12"))

    # profit badge: green disc with a white up arrow, bottom right
    bx, by, br = int(N * 0.745), int(N * 0.745), int(N * 0.175)
    disc = np.zeros((N, N), np.uint8)
    cv2.circle(disc, (bx, by), br + 22, 255, -1, cv2.LINE_AA)
    paint(disc, solid("#1c2226"))
    disc[:] = 0
    cv2.circle(disc, (bx, by), br, 255, -1, cv2.LINE_AA)
    paint(disc, _vgrad(N, by - br, by + br, "#a9d64e", "#5f8a22"))
    arrow = np.zeros((N, N), np.uint8)
    a = br * 0.62
    head = np.array([[bx, by - a], [bx + a * 0.85, by + a * 0.05], [bx - a * 0.85, by + a * 0.05]], np.int32)
    cv2.fillPoly(arrow, [head], 255, cv2.LINE_AA)
    cv2.rectangle(arrow, (int(bx - a * 0.32), int(by)), (int(bx + a * 0.32), int(by + a * 0.85)), 255, -1)
    paint(arrow, solid("#ffffff"))

    out = np.dstack([img, alpha * 255]).clip(0, 255).astype(np.uint8)
    out[..., :3] = np.where(out[..., 3:4] == 0, 0, out[..., :3])
    return out


def ico_bytes(big: np.ndarray, sizes=SIZES) -> bytes:
    """A .ico with one PNG entry per size (Windows Vista+ reads PNG entries at every size)."""
    pngs = []
    for s in sizes:
        small = cv2.resize(big, (s, s), interpolation=cv2.INTER_AREA)
        ok, buf = cv2.imencode(".png", small)
        assert ok
        pngs.append((s, buf.tobytes()))
    header = struct.pack("<HHH", 0, 1, len(pngs))
    offset = 6 + 16 * len(pngs)
    entries, data = b"", b""
    for s, png in pngs:
        d = 0 if s >= 256 else s
        entries += struct.pack("<BBBBHHII", d, d, 0, 0, 1, 32, len(png), offset + len(data))
        data += png
    return header + entries + data


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--out", type=Path, default=OUT)
    p.add_argument("--preview", type=Path, help="also write a PNG sheet of every size on light and dark")
    args = p.parse_args()
    big = draw_icon()
    args.out.write_bytes(ico_bytes(big))
    print(f"wrote {args.out}")
    if args.preview:
        sheet = np.full((600, 40 + sum(s + 20 for s in SIZES), 3), 240, np.uint8)
        sheet[300:] = 32
        x = 20
        for s in SIZES:
            small = cv2.resize(big, (s, s), interpolation=cv2.INTER_AREA).astype(np.float32)
            for y0 in (20, 320):
                bg = sheet[y0:y0 + s, x:x + s].astype(np.float32)
                k = small[..., 3:4] / 255.0
                sheet[y0:y0 + s, x:x + s] = (small[..., :3] * k + bg * (1 - k)).astype(np.uint8)
            x += s + 20
        cv2.imwrite(str(args.preview), sheet)
        print(f"wrote {args.preview}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
