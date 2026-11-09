"""Find the player-shop window by template-matching its button block (base-resolution gray)."""
import cv2
import numpy as np

CACHE_MAX_MEAN_DIFF = 4


class ShopLocator:
    def __init__(self, anchor: np.ndarray, min_score: float = 0.92):
        self.anchor = anchor
        self.min_score = min_score
        self._anchor16 = anchor.astype(np.int16)
        self._pos = None

    def reset(self) -> None:
        self._pos = None

    def score(self, gray: np.ndarray) -> float:
        """Best anchor match score (TM_CCOEFF_NORMED) anywhere in `gray`; 0.0 if it does not fit."""
        h, w = self.anchor.shape
        if gray.shape[0] < h or gray.shape[1] < w:
            return 0.0
        return float(cv2.minMaxLoc(cv2.matchTemplate(gray, self.anchor, cv2.TM_CCOEFF_NORMED))[1])

    def locate(self, gray: np.ndarray):
        """Anchor top-left (x, y) in `gray`, or None when no shop window is visible."""
        h, w = self.anchor.shape
        if self._pos is not None:
            x, y = self._pos
            region = gray[y:y + h, x:x + w]
            if region.shape == self.anchor.shape and \
                    np.abs(region.astype(np.int16) - self._anchor16).mean() <= CACHE_MAX_MEAN_DIFF:
                return self._pos
        if gray.shape[0] < h or gray.shape[1] < w:
            self._pos = None
            return None
        res = cv2.matchTemplate(gray, self.anchor, cv2.TM_CCOEFF_NORMED)
        _, score, _, loc = cv2.minMaxLoc(res)
        if score < self.min_score:
            self._pos = None
            return None
        self._pos = (int(loc[0]), int(loc[1]))
        return self._pos
