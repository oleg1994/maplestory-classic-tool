"""Glyph-template OCR for fixed pixel fonts."""
import base64
import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np


def binarize(gray: np.ndarray, ink_max_gray: int) -> np.ndarray:
    return gray < ink_max_gray


def column_runs(ink: np.ndarray) -> list:
    cols = ink.any(axis=0).view(np.int8)
    edges = np.flatnonzero(np.diff(cols, prepend=0, append=0))
    return list(zip(edges[::2].tolist(), edges[1::2].tolist()))


def glyph_key(ink: np.ndarray) -> bytes:
    h, w = ink.shape
    assert h < 256 and w < 256
    return bytes((h, w)) + np.packbits(ink).tobytes()


@dataclass
class ReadResult:
    text: str
    unknown: int


class GlyphSet:
    def __init__(self, glyphs: dict, space_gap: int, tolerance: float = 0.06):
        self.space_gap = space_gap
        self.tolerance = tolerance
        self.exact = {}
        self.by_width = {}
        self._widths_desc = []
        for char, arrays in glyphs.items():
            for arr in arrays:
                self.add(char, arr)

    def add(self, char: str, ink: np.ndarray) -> None:
        ink = np.ascontiguousarray(ink, dtype=bool)
        if ink.ndim != 2 or ink.size == 0:
            raise ValueError("glyph template must be non-empty 2-D")
        key = glyph_key(ink)
        if self.exact.get(key) == char:
            return
        self.exact[key] = char
        w = ink.shape[1]
        self.by_width.setdefault(w, []).append((ink, char))
        self._widths_desc = sorted(self.by_width, reverse=True)

    def chars(self) -> set:
        """Characters that have at least one template (read-only copy)."""
        return {c for arrs in self.by_width.values() for _, c in arrs}

    def read(self, ink: np.ndarray) -> ReadResult:
        exact = self.exact
        widths = self._widths_desc
        out = []
        unknown = 0
        prev_x1 = None
        for x0, x1 in column_runs(ink):
            if prev_x1 is not None and x0 - prev_x1 >= self.space_gap:
                out.append(" ")
            prev_x1 = x1
            seg = ink[:, x0:x1]
            ch = exact.get(glyph_key(seg))
            if ch is not None:
                out.append(ch)
                continue
            seg_w = x1 - x0
            pos = 0
            while pos < seg_w:
                for w in widths:
                    if w > seg_w - pos:
                        continue
                    ch = exact.get(glyph_key(seg[:, pos:pos + w]))
                    if ch is not None:
                        out.append(ch)
                        pos += w
                        break
                else:
                    rest = seg[:, pos:]
                    ch = self._nearest(rest)
                    if ch is not None:
                        out.append(ch)
                    else:
                        out.append("?")
                        unknown += 1
                    pos = seg_w
                while pos < seg_w and not seg[:, pos].any():
                    pos += 1
        return ReadResult("".join(out), unknown)

    def _nearest(self, rest: np.ndarray):
        cands = self.by_width.get(rest.shape[1])
        if not cands:
            return None
        best_char = None
        best = None
        for arr, char in cands:
            if arr.shape != rest.shape:
                continue
            d = int(np.count_nonzero(arr != rest))
            if best is None or d < best:
                best, best_char = d, char
        if best is not None and best / rest.size <= self.tolerance:
            return best_char
        return None

    def save(self, path: Path) -> None:
        glyphs = []
        for items in self.by_width.values():
            for arr, char in items:
                h, w = arr.shape
                glyphs.append({
                    "char": char, "h": h, "w": w,
                    "bits": base64.b64encode(np.packbits(arr).tobytes()).decode("ascii"),
                })
        data = {"space_gap": self.space_gap, "tolerance": self.tolerance, "glyphs": glyphs}
        Path(path).write_text(json.dumps(data), encoding="utf-8")

    @classmethod
    def load(cls, path: Path) -> "GlyphSet":
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        gs = cls({}, data["space_gap"], data["tolerance"])
        for g in data["glyphs"]:
            h, w = g["h"], g["w"]
            bits = np.frombuffer(base64.b64decode(g["bits"]), np.uint8)
            arr = np.unpackbits(bits)[: h * w].reshape(h, w).astype(bool)
            gs.add(g["char"], arr)
        return gs
