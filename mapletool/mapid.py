"""Which map the player is on, from the minimap's map name (screen pixels only), and the EXP-session
reset rule: a new session once the player has stayed off the session's map for MAP_RESET_S.

The minimap is found by its WORLD button (assets/minimap_world.png), identical in both minimap forms:
- expanded: a "MINI MAP" title row (assets/minimap_label.png) with the map name in white on two lines
  below it ("Hidden Street" / "Free Market <1>"); the window is as wide as the map's minimap;
- collapsed: one light strip "Victoria Road : Ellinia Department Store" ending in the buttons; its
  left end depends on the name's length and is found along the strip's plain background row.

A map identity is the binarized name text (no OCR): equal names give equal bitmaps. The game draws its
own cursor into the frame; two names whose bitmaps differ only inside a cursor-sized box count as the
same map, so a cursor parked over the minimap never looks like a map change.
"""
from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

MIN_SCORE = 0.9  # WORLD button match (fixtures: >= 0.985, next best <= 0.69)
CACHE_MAX_MEAN_DIFF = 4
LABEL_MIN_SCORE = 0.85  # "MINI MAP" label at its place: expanded form
# The expanded minimap's width follows the map (Free Market: WORLD button 102 px right of the label;
# East Rocky Mountain IV: 145 px), so the label is searched along the title row left of the button.
EXPANDED_LABEL_ROWS = (-2, 16)  # title-row band searched for the label, relative to the WORLD button's top
EXPANDED_NAME_DX = 44  # the name starts this far right of the label's left edge (after the map icon)
EXPANDED_NAME_RIGHT = 38  # ... and ends this far right of the WORLD button's left edge
EXPANDED_NAME_ROWS = (23, 51)  # name lines relative to the WORLD button's top
EXPANDED_INK_MIN = 210  # white name text on the blue-grey panel (~180)
COLLAPSED_GAP = 28  # the name ends this far left of the WORLD button (the - and + buttons)
COLLAPSED_ROWS = (1, 13)  # name text rows relative to the WORLD button's top
COLLAPSED_BG = (205, 250)  # the strip's plain background (row 0 of the button template, left of it)
COLLAPSED_INK_MAX = 128  # dark name text on the light strip (~221)
CURSOR_BOX = (26, 30)  # the game's cursor sprite fits in this (w, h) at base resolution

MAP_RESET_S = 30.0  # off the session's map this long -> new EXP session


@dataclass(frozen=True)
class MapId:
    form: str  # "expanded" | "collapsed"
    ink: np.ndarray  # bool bitmap of the name text

    def same(self, other: "MapId | None") -> bool:
        """Same map name (differences confined to a cursor-sized box are ignored)."""
        if other is None or other.form != self.form or other.ink.shape != self.ink.shape:
            return False
        diff = self.ink != other.ink
        if not diff.any():
            return True
        ys, xs = np.nonzero(diff)
        return xs.max() - xs.min() < CURSOR_BOX[0] and ys.max() - ys.min() < CURSOR_BOX[1]


class MinimapReader:
    def __init__(self, world: np.ndarray, label: np.ndarray, min_score: float = MIN_SCORE):
        self.world = world
        self.label = label
        self.min_score = min_score
        self._pos = None
        self._ref = None  # the button's pixels where it was last found (the cheap re-check compares to these)

    def reset(self) -> None:
        self._pos = None
        self._ref = None

    def locate(self, gray: np.ndarray):
        """Top-left of the WORLD button, or None (minimap closed or off-frame)."""
        h, w = self.world.shape
        if self._pos is not None:
            x, y = self._pos
            region = gray[y:y + h, x:x + w]
            if region.shape == self.world.shape and \
                    np.abs(region.astype(np.int16) - self._ref).mean() <= CACHE_MAX_MEAN_DIFF:
                return self._pos
        self._pos = None
        self._ref = None
        if gray.shape[0] < h or gray.shape[1] < w:
            return None
        _, score, _, loc = cv2.minMaxLoc(cv2.matchTemplate(gray, self.world, cv2.TM_CCOEFF_NORMED))
        if score < self.min_score:
            return None
        self._pos = (int(loc[0]), int(loc[1]))
        self._ref = gray[self._pos[1]:self._pos[1] + h, self._pos[0]:self._pos[0] + w].astype(np.int16)
        return self._pos

    def _label_x(self, gray, pos):
        """x of the "MINI MAP" label on the title row left of the WORLD button (expanded form), or None."""
        y0, y1 = pos[1] + EXPANDED_LABEL_ROWS[0], pos[1] + EXPANDED_LABEL_ROWS[1]
        strip = gray[max(0, y0):y1, :pos[0]]
        h, w = self.label.shape
        if strip.shape[0] < h or strip.shape[1] < w:
            return None
        _, score, _, loc = cv2.minMaxLoc(cv2.matchTemplate(strip, self.label, cv2.TM_CCOEFF_NORMED))
        return int(loc[0]) if score >= LABEL_MIN_SCORE else None

    def read(self, gray: np.ndarray) -> MapId | None:
        pos = self.locate(gray)
        if pos is None:
            return None
        label_x = self._label_x(gray, pos)
        if label_x is not None:
            x0, x1 = label_x + EXPANDED_NAME_DX, pos[0] + EXPANDED_NAME_RIGHT
            y0, y1 = pos[1] + EXPANDED_NAME_ROWS[0], pos[1] + EXPANDED_NAME_ROWS[1]
            if x1 - x0 < 8 or y1 > gray.shape[0]:
                return None
            ink = gray[y0:y1, x0:x1] > EXPANDED_INK_MIN
            return MapId("expanded", ink) if ink.any() else None
        right = pos[0] - COLLAPSED_GAP
        row = gray[pos[1], :max(0, right)]
        lo, hi = COLLAPSED_BG
        left = right
        while left > 0 and lo <= row[left - 1] <= hi:
            left -= 1
        if right - left < 8:
            return None
        ink = gray[pos[1] + COLLAPSED_ROWS[0]:pos[1] + COLLAPSED_ROWS[1], left + 2:right] < COLLAPSED_INK_MAX
        return MapId("collapsed", ink) if ink.any() else None


class MapWatch:
    """The EXP session's map and the reset rule.

    observe(map_id, now) once per read; True when the session should restart: the player has been off
    the session's map for MAP_RESET_S (any other maps, a closed minimap or loading screens in between).
    Returning to the session's map before that cancels it. With no readable minimap ever, nothing resets."""

    def __init__(self, reset_after: float = MAP_RESET_S):
        self.reset_after = reset_after
        self.reset()

    def reset(self) -> None:
        self.home: MapId | None = None
        self.away_since: float | None = None
        self.latest: MapId | None = None

    def observe(self, map_id: MapId | None, now: float) -> bool:
        if map_id is not None:
            self.latest = map_id
            if self.home is None:
                self.home = map_id
                return False
            if map_id.same(self.home):
                self.away_since = None
                return False
            if self.away_since is None:
                self.away_since = now
        if self.away_since is not None and now - self.away_since >= self.reset_after:
            self.home = self.latest
            self.away_since = None
            return True
        return False
