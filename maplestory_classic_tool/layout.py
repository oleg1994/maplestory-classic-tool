"""Shop-window geometry, in base-resolution pixels (Amendment A), and base-resolution choice."""
import json
import re
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

# Game resolutions the client can render at; the frame is resized to one of these ("base").
RESOLUTIONS = [
    (1366, 768), (1280, 720), (1600, 900), (1920, 1080),  # 16:9
    (1280, 800), (1440, 900), (1680, 1050), (1920, 1200),  # 16:10
    (1024, 768), (800, 600),  # 4:3
]
DEFAULT_BASE = (1366, 768)
BASE_H = 768
ASPECT_TOL = 0.01
PROBE_MIN = 0.92  # ShopLocator threshold: a probe "hits" and triggers a full family evaluation
CONFIRM_MIN = 0.95  # the best-scoring base must reach this to be confirmed
HEAL_MARGIN = 0.02  # a confirmed base is replaced when another scores this much higher
_KEY_RE = re.compile(r"^(\d+)x(\d+)$")


@dataclass(frozen=True)
class Layout:
    base_size: tuple  # (width, height) the game renders at
    anchor_to_grid: tuple  # anchor top-left -> row 0 origin
    rows: int
    row_pitch: int
    name_box: tuple  # (x, y, w, h) relative to row origin
    price_box: tuple
    ink_max_gray: int
    label_dx: int  # label x relative to row origin
    price_format: str
    window_box: tuple = (-141, -58, 523, 406)  # shop window (x, y, w, h) relative to the anchor
    shop_id_boxes: tuple = ()  # ((x, y, w, h), ...) relative to the anchor: title bar, seller name
    icon_box: tuple | None = None  # item cell left of the name (image + stock number), relative to the row origin

    def row_origin(self, anchor_xy: tuple, row: int) -> tuple:
        ax, ay = anchor_xy
        dx, dy = self.anchor_to_grid
        return (ax + dx, ay + dy + row * self.row_pitch)

    def icon_rect(self, anchor_xy: tuple, row: int):
        """Item icon cell (x, y, w, h) of a row in base pixels, or None without icon_box."""
        if self.icon_box is None:
            return None
        ox, oy = self.row_origin(anchor_xy, row)
        x, y, w, h = self.icon_box
        return (ox + x, oy + y, w, h)

    def window_rect(self, anchor_xy: tuple) -> tuple:
        """Shop window (x, y, w, h) in base pixels."""
        x, y, w, h = self.window_box
        return (anchor_xy[0] + x, anchor_xy[1] + y, w, h)


def load_layout(path: Path) -> Layout:
    d = json.loads(Path(path).read_text(encoding="utf-8"))
    extra = {}
    if "window_box" in d:
        extra["window_box"] = tuple(int(v) for v in d["window_box"])
    if "shop_id_boxes" in d:
        extra["shop_id_boxes"] = tuple(tuple(int(v) for v in b) for b in d["shop_id_boxes"])
    if "icon_box" in d:
        extra["icon_box"] = tuple(int(v) for v in d["icon_box"])
    return Layout(
        base_size=tuple(d["base_size"]),
        anchor_to_grid=tuple(d["anchor_to_grid"]),
        rows=int(d["rows"]),
        row_pitch=int(d["row_pitch"]),
        name_box=tuple(d["name_box"]),
        price_box=tuple(d["price_box"]),
        ink_max_gray=int(d["ink_max_gray"]),
        label_dx=int(d["label_dx"]),
        price_format=d["price_format"],
        **extra,
    )


def to_base(gray: np.ndarray, base_size: tuple) -> np.ndarray:
    w, h = base_size
    if gray.shape[1] == w and gray.shape[0] == h:
        return gray
    return cv2.resize(gray, (w, h), interpolation=cv2.INTER_AREA)


def crop_to_base(full: np.ndarray, base_size: tuple, box: tuple):
    """BGR crop of a base-pixel box (x, y, w, h) cut from a full-resolution colour frame and resized to
    (w, h): the same pixels as to_base(full)[box] up to resampling, without resizing the whole frame.
    BGRA input gives BGR. None when the box is not fully inside the frame."""
    fh, fw = full.shape[:2]
    bw, bh = base_size
    x, y, w, h = box
    sx, sy = fw / bw, fh / bh
    x0, y0, x1, y1 = round(x * sx), round(y * sy), round((x + w) * sx), round((y + h) * sy)
    if x < 0 or y < 0 or x1 > fw or y1 > fh or x1 <= x0 or y1 <= y0 or w <= 0 or h <= 0:
        return None
    region = full[y0:y1, x0:x1]
    if region.ndim == 3 and region.shape[2] == 4:
        region = cv2.cvtColor(region, cv2.COLOR_BGRA2BGR)
    if region.shape[:2] == (h, w):
        return np.ascontiguousarray(region).copy()
    return cv2.resize(region, (w, h), interpolation=cv2.INTER_AREA)


def size_key(client) -> str:
    return f"{int(client[0])}x{int(client[1])}"


def base_family(client_w: int, client_h: int) -> list:
    """The game keeps a 768 px base height and a width of 768*W/H rounded to the nearest even integer
    (measured: 3840x2160 -> 1366x768, 2560x1600 -> 1228x768). Primary first, then widths +-2, +-1."""
    if client_w <= 0 or client_h <= 0:
        return []
    w = 2 * round(BASE_H * client_w / client_h / 2)
    return [(w + d, BASE_H) for d in (0, -2, 2, -1, 1) if w + d > 0]


def base_candidates(client_w: int, client_h: int, prev_scale: float | None = None) -> list:
    """Bases worth probing for a client size, most likely first.

    1. The 768-height family (base_family): the rule the game was measured to follow.
    2. Last-resort tail: the standard resolutions within 1% of the client aspect and no larger than the
       client, ordered exact size, integer scales, then by closeness to `prev_scale` (the last confirmed
       scale). Kept in case a client renders at a fixed standard size; costs probe slots only while
       nothing in the family matched.
    """
    out = base_family(client_w, client_h)
    if not out:
        return [DEFAULT_BASE]
    aspect = client_w / client_h
    keep = [(w, h) for w, h in RESOLUTIONS
            if w <= client_w and h <= client_h and abs((w / h) / aspect - 1) <= ASPECT_TOL and (w, h) not in out]
    tail = []
    if (client_w, client_h) in keep:
        tail.append((client_w, client_h))
    rest = [b for b in keep if b not in tail]
    integer = sorted((b for b in rest if client_w % b[0] == 0 and client_h % b[1] == 0),
                     key=lambda b: client_w / b[0])
    tail += integer
    rest = [b for b in rest if b not in integer]
    if prev_scale:
        rest.sort(key=lambda b: (abs(client_w / b[0] - prev_scale), client_w / b[0]))
    else:
        rest.sort(key=lambda b: client_w / b[0])
    return out + tail + rest


def best_score(scores: dict):
    """(base, score) with the highest score, or (None, 0.0)."""
    if not scores:
        return None, 0.0
    base = max(scores, key=lambda b: scores[b])
    return base, scores[base]


def _parse_entry(key, value):
    """("WxH", (bw, bh)) or None for a malformed cache entry."""
    if not isinstance(key, str) or not _KEY_RE.match(key):
        return None
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        return None
    try:
        bw, bh = (int(v) for v in value)
    except (TypeError, ValueError):
        return None
    if bw <= 0 or bh <= 0:
        return None
    return key, (bw, bh)


class BaseResolver:
    """Chooses the base resolution per client size: cached confirmed base, else round-robin probing.

    current(client) -> (base, confirmed). The caller scans the frame resized to `base`. When the anchor
    matches there, it scores the whole 768-family on that frame and calls choose() (best >= CONFIRM_MIN
    wins); otherwise miss() advances probing by one candidate. A confirmed base is never dropped (the
    shop may just be closed), but heal() replaces it when another base scores HEAL_MARGIN higher.
    """

    def __init__(self, confirmed: dict | None = None, on_confirm=None):
        self.confirmed: dict = {}
        for k, v in (confirmed or {}).items():
            e = _parse_entry(k, v)
            if e is not None:
                self.confirmed[e[0]] = e[1]
        self.on_confirm = on_confirm
        self._probe: dict = {}  # size key -> probe index
        self.prev_scale = None
        for k, (bw, _) in self.confirmed.items():
            self.prev_scale = int(_KEY_RE.match(k).group(1)) / bw

    def candidates(self, client) -> list:
        return base_candidates(int(client[0]), int(client[1]), self.prev_scale)

    def family(self, client) -> list:
        return base_family(int(client[0]), int(client[1]))

    def choose(self, client, scores: dict):
        """Confirm the best-scoring base (not the first that passed); None if it is below CONFIRM_MIN."""
        base, score = best_score(scores)
        if base is None or score < CONFIRM_MIN:
            return None
        self.confirm(client, base)
        return base

    def heal(self, client, scores: dict):
        """Re-confirm when another base beats the confirmed one by HEAL_MARGIN; returns the new base or None."""
        current = self.confirmed.get(size_key(client))
        base, score = best_score(scores)
        if base is None or base == current or score < CONFIRM_MIN:
            return None
        if score < scores.get(current, 0.0) + HEAL_MARGIN:
            return None
        self.confirm(client, base)
        return base

    def current(self, client) -> tuple:
        key = size_key(client)
        if key in self.confirmed:
            return self.confirmed[key], True
        cands = self.candidates(client)
        return cands[self._probe.get(key, 0) % len(cands)], False

    def miss(self, client) -> None:
        key = size_key(client)
        if key not in self.confirmed:
            self._probe[key] = self._probe.get(key, 0) + 1

    def confirm(self, client, base) -> bool:
        """Record `base` for this client size; True when newly confirmed."""
        key = size_key(client)
        base = (int(base[0]), int(base[1]))
        if self.confirmed.get(key) == base:
            return False
        self.confirmed[key] = base
        self._probe.pop(key, None)
        self.prev_scale = int(client[0]) / base[0]
        if self.on_confirm is not None:
            self.on_confirm(key, base)
        return True
