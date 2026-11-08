"""Frame -> verdicts pipeline (base-resolution gray frames)."""
import logging

import numpy as np

from mapletool.evaluate import ScanResult, evaluate
from mapletool.glyphs import glyph_key
from mapletool.shop import agrees_with_glyphs, name_agrees, parse_price, row_inks, to_listing

CACHE_MAX = 4096
ID_INK_GRAY = 128  # binarize identity boxes (dark text on the white title bar, light text on the dark name box)
# The locator matches the shop's button block, which changes when the mouse hovers a button. While the
# title strip (layout.shop_id_boxes[0]) at the last position is unchanged the shop is still open: hold it.
HOLD_PIXEL_DIFF = 48  # a title-strip pixel counts as changed above this gray difference
HOLD_MAX_CHANGED = 0.03  # at most this share of the strip may change (the game's cursor over it)
log = logging.getLogger(__name__)


class Scanner:
    def __init__(self, locator, layout, glyphs, catalog, settings, fallback):
        self.locator = locator
        self.layout = layout
        self.glyphs = glyphs
        self.catalog = catalog
        self.settings = settings
        self.fallback = fallback
        if hasattr(glyphs, "chars"):
            known = glyphs.chars()
            self._unknown_chars = "".join(c for c in "0123456789," if c not in known)
        else:  # fail closed: without the template list no "?" may stand for a digit
            log.warning("glyph set has no chars(); OCR cannot fill unknown price digits")
            self._unknown_chars = ""
        self._texts: dict = {}
        self._last_key = None
        self._last = ScanResult(None, [], True)
        self._title_ref = None  # title strip (gray) at the last anchored frame
        self._session_id = None  # identity of the shop for as long as it stays open

    def reset(self) -> None:
        self._texts.clear()
        self._last_key = None
        self._last = ScanResult(None, [], True)
        self._title_ref = None
        self._session_id = None
        self.locator.reset()

    def _title_crop(self, gray, anchor):
        """The title strip at `anchor`, clipped to the frame; None when nothing of it is visible."""
        boxes = getattr(self.layout, "shop_id_boxes", ())
        if not boxes:
            return None
        x, y, w, h = boxes[0]
        x0, y0 = max(anchor[0] + x, 0), max(anchor[1] + y, 0)
        x1, y1 = min(anchor[0] + x + w, gray.shape[1]), min(anchor[1] + y + h, gray.shape[0])
        if x1 <= x0 or y1 <= y0:
            return None
        return gray[y0:y1, x0:x1]

    def _title_still_there(self, gray) -> bool:
        if self._last.anchor is None or self._title_ref is None:
            return False
        crop = self._title_crop(gray, self._last.anchor)
        if crop is None or crop.shape != self._title_ref.shape:
            return False
        changed = np.count_nonzero(np.abs(crop.astype(np.int16) - self._title_ref) > HOLD_PIXEL_DIFF)
        return changed <= HOLD_MAX_CHANGED * crop.size

    def _grid_region(self, gray, anchor):
        lay = self.layout
        ox, oy = lay.row_origin(anchor, 0)
        x0 = ox + min(lay.name_box[0], lay.price_box[0])
        x1 = ox + max(lay.name_box[0] + lay.name_box[2], lay.price_box[0] + lay.price_box[2])
        y0 = oy + min(lay.name_box[1], lay.price_box[1])
        y1 = oy + (lay.rows - 1) * lay.row_pitch + max(
            lay.name_box[1] + lay.name_box[3], lay.price_box[1] + lay.price_box[3])
        return gray[max(y0, 0):max(y1, 0), max(x0, 0):max(x1, 0)]

    def _shop_id(self, gray, anchor):
        """Hash of the binarized title-bar and seller-name boxes (layout.shop_id_boxes).

        Boxes reaching off the frame are clipped (the clip rectangle is part of the hash); None when
        nothing of any box is visible, so such frames are never merged into a shop's list.
        """
        ax, ay = anchor
        parts = []
        visible = False
        for x, y, w, h in getattr(self.layout, "shop_id_boxes", ()):
            x0, y0 = max(ax + x, 0), max(ay + y, 0)
            x1, y1 = min(ax + x + w, gray.shape[1]), min(ay + y + h, gray.shape[0])
            if x1 <= x0 or y1 <= y0:
                parts.append(None)
                continue
            visible = True
            crop = gray[y0:y1, x0:x1] < ID_INK_GRAY
            parts.append((x0 - ax - x, y0 - ay - y, x1 - x0, y1 - y0, np.packbits(crop).tobytes()))
        return hash(tuple(parts)) if visible else None

    def _crop(self, gray, anchor, row, box):
        ox, oy = self.layout.row_origin(anchor, row)
        x, y, w, h = box
        return gray[oy + y:oy + y + h, ox + x:ox + x + w]

    def _read(self, ink, gray, anchor, row, box, is_price):
        """(text, unknown, raw_unknown) with the fallback policy applied; cached by glyph key.
        raw_unknown: unknown glyphs of the glyph read itself, before any OCR fill."""
        key = glyph_key(ink)
        hit = self._texts.get(key)
        if hit is not None:
            return hit
        res = self.glyphs.read(ink)
        text, unknown = res.text, res.unknown
        raw_unknown = unknown
        if unknown > 0 and self.fallback is not None:
            fb = self.fallback.read(self._crop(gray, anchor, row, box))
            if is_price:
                if fb and parse_price(fb)[0] is not None and agrees_with_glyphs(text, fb, self._unknown_chars):
                    text, unknown = fb, 0
            elif fb and name_agrees(text, fb):  # a wrong OCR name would map to a wrong item/NPC price
                text, unknown = fb, 0
        if len(self._texts) > CACHE_MAX:
            self._texts.clear()
        self._texts[key] = (text, unknown, raw_unknown)
        return text, unknown, raw_unknown

    def scan(self, gray: np.ndarray) -> ScanResult:
        anchor = self.locator.locate(gray)
        if anchor is None and self._title_still_there(gray):
            # hover over the shop's buttons: same shop, same rows; no identity, so nothing is merged
            self._last = ScanResult(self._last.anchor, self._last.verdicts, False, None)
            return self._last
        if anchor is None:
            self._last_key = None
            self._title_ref = None
            self._session_id = None
            changed = self._last.anchor is not None or bool(self._last.verdicts)
            self._last = ScanResult(None, [], changed)
            return self._last
        title = self._title_crop(gray, anchor)
        self._title_ref = None if title is None else title.astype(np.int16)
        # The shop cannot change while its window stays open, so its identity is fixed until it closes
        # (the game's cursor over the title or seller name would otherwise look like another shop).
        if self._session_id is None:
            self._session_id = self._shop_id(gray, anchor)
        shop_id = self._session_id
        key = (anchor, hash(self._grid_region(gray, anchor).tobytes()))
        if key == self._last_key:
            self._last = ScanResult(self._last.anchor, self._last.verdicts, False, shop_id)
            return self._last
        lay = self.layout
        verdicts = []
        for ri in row_inks(gray, anchor, lay):
            name, _, n_raw = self._read(ri.name, gray, anchor, ri.row, lay.name_box, False)
            price, p_unknown, p_raw = self._read(ri.price, gray, anchor, ri.row, lay.price_box, True)
            listing = to_listing(ri.row, name, price, p_unknown, name_unknown=n_raw > 0,
                                 price_filled=p_raw > 0 and p_unknown == 0)
            verdicts.append(evaluate(listing, self.catalog, self.settings.min_profit))
        self._last_key = key
        self._last = ScanResult(anchor, verdicts, True, shop_id)
        return self._last
