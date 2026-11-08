"""Slice shop rows into ink bitmaps and turn OCR text into Listings."""
import re
from dataclasses import dataclass

import numpy as np

from mapletool.catalog import fold_il
from mapletool.glyphs import binarize
from mapletool.layout import Layout
from mapletool.model import Listing

_NUM = r"[1-9]\d{0,2}(?:,\d{3})*"  # no leading zero
PRICE_RE = re.compile(rf"^({_NUM}) for ({_NUM}) mesos$")  # matches layout.price_format


def collapse_digit_spaces(text: str) -> str:
    """Drop a space between two digits/commas (tabular digits: the gap after "1" reads as one)."""
    return re.sub(r"(?<=[\d,]) (?=[\d,])", "", text)


def agrees_with_glyphs(glyph_text: str, ocr_text: str, unknown_chars: str) -> bool:
    """True only if OCR text fills in the glyph read without contradicting any known position.

    unknown_chars: the digit/comma characters the glyph set cannot read as a single glyph; only
    these can hide behind a "?" in the qty and price words.

    1. Digit gaps are collapsed on both sides (on the glyph side "?" counts as digit-like, so
       "1 ?,000" -> "1?,000"); whitespace runs become single spaces.
    2. A trailing fringe of "?" and spaces is trimmed from the glyph side.
    3. The trimmed glyph text must end with the known word "mesos" and contain the known word
       "for"; otherwise the read is too uncertain to trust.
    4. Both sides are split into words; the word counts must be equal.
    5. Each glyph word becomes a pattern; the OCR word must fullmatch it. In the qty word
       (first) and the price word (before "mesos"), a run of k "?" matches exactly k characters,
       each one of unknown_chars (no unknown_chars: any "?" there fails). Elsewhere a "?" run
       matches any non-empty non-space run.
    6. The qty word must start with a known digit, and the qty and price words must each contain
       a known digit: a price with no positional evidence stays unknown.
    """
    g = re.sub(r"(?<=[\d,?]) (?=[\d,?])", "", glyph_text)
    g = re.sub(r"\s+", " ", g).strip().rstrip("? ")
    o = re.sub(r"\s+", " ", collapse_digit_spaces(ocr_text)).strip()
    gw, ow = g.split(" "), o.split(" ")
    if len(gw) < 3 or gw[-1] != "mesos" or "for" not in gw:
        return False
    if len(gw) != len(ow):
        return False
    numeric = {0, len(gw) - 2}
    if not gw[0][:1].isdigit():
        return False
    for idx in numeric:
        if not re.search(r"[0-9]", gw[idx]):
            return False
    alphabet = "[" + re.escape("".join(unknown_chars)) + "]" if unknown_chars else None
    for idx, (gword, oword) in enumerate(zip(gw, ow)):
        if idx in numeric:
            if alphabet is None:
                if "?" in gword:
                    return False
                pattern = re.escape(gword)
            else:
                pattern = "".join(
                    f"{alphabet}{{{len(t)}}}" if t[0] == "?" else re.escape(t)
                    for t in re.findall(r"\?+|[^?]+", gword))
        else:
            pattern = r"\S+".join(re.escape(part) for part in re.split(r"\?+", gword))
        if re.fullmatch(pattern, oword) is None:
            return False
    return True


_THIN = "l.,':;i"  # glyphs a stroke fragment of an untemplated letter tends to match (N -> "l?", L -> "l...")
_CLUSTER_RE = re.compile(rf"[{re.escape(_THIN)}]*\?(?:[{re.escape(_THIN)}]*\?)*[{re.escape(_THIN)}]*")
MIN_NAME_EVIDENCE = 3  # known characters outside unknown clusters needed to trust an OCR name


def _name_norm(text: str) -> str:
    return re.sub(r"\s+", "", fold_il(text.replace("…", "...")).casefold())


def name_agrees(glyph_text: str, ocr_text: str) -> bool:
    """True if the OCR name agrees with the glyph name read at every known character.

    Both sides: I folded to l, casefolded, spaces removed. Each run of "?" in the glyph text
    (an untemplated glyph, possibly a touching cluster) matches 1-3 characters per "?"; thin glyphs
    touching such a run ("l", ".", ...) are absorbed into it, because a fragment of an untemplated
    letter often matches one (real reads: "INT" -> "ll?T", "LUK" -> "l.....?l?"). Everything else
    must match literally, and at least MIN_NAME_EVIDENCE known characters must remain.
    """
    g, o = _name_norm(glyph_text), _name_norm(ocr_text)
    if not g or not o:
        return False
    parts, literal, pos = [], 0, 0
    for m in _CLUSTER_RE.finditer(g):
        parts.append(re.escape(g[pos:m.start()]))
        literal += m.start() - pos
        c = m.group()
        q = c.count("?")
        parts.append(f".{{1,{3 * q + len(c) - q}}}")
        pos = m.end()
    parts.append(re.escape(g[pos:]))
    literal += len(g) - pos
    if literal < MIN_NAME_EVIDENCE:
        return False
    return re.fullmatch("".join(parts), o) is not None


@dataclass
class RowInk:
    row: int
    name: np.ndarray  # bool
    price: np.ndarray  # bool


def _box(gray: np.ndarray, origin: tuple, box: tuple, ink_max_gray: int):
    ox, oy = origin
    x, y, w, h = box
    x0, y0 = ox + x, oy + y
    if x0 < 0 or y0 < 0 or x0 + w > gray.shape[1] or y0 + h > gray.shape[0]:
        return None
    return binarize(gray[y0:y0 + h, x0:x0 + w], ink_max_gray)


def row_inks(gray: np.ndarray, anchor_xy: tuple, layout: Layout) -> list:
    """RowInk per row that has ink and lies fully inside the frame."""
    out = []
    for r in range(layout.rows):
        origin = layout.row_origin(anchor_xy, r)
        name = _box(gray, origin, layout.name_box, layout.ink_max_gray)
        price = _box(gray, origin, layout.price_box, layout.ink_max_gray)
        if name is None or price is None or not (name.any() or price.any()):
            continue
        out.append(RowInk(r, name, price))
    return out


def parse_price(text: str) -> tuple:
    """(qty, price) from "N for P mesos"; (None, None) on no match or an unknown glyph."""
    if "?" in text:
        return None, None
    m = PRICE_RE.match(collapse_digit_spaces(text))
    if not m:
        return None, None
    try:
        return int(m.group(1).replace(",", "")), int(m.group(2).replace(",", ""))
    except ValueError:
        return None, None


def to_listing(row: int, name_text: str, price_text: str, price_unknown: int,
               name_unknown: bool = False, price_filled: bool = False) -> Listing:
    """price_unknown: unknown-glyph count of the price box only (name '?' goes to fallback OCR).
    name_unknown / price_filled: the raw glyph reads had unknown glyphs (see Listing)."""
    qty, price = parse_price(price_text)
    uncertain = price_unknown > 0 or qty is None or price is None or not name_text.strip()
    return Listing(row, name_text, qty, price, uncertain, name_unknown, price_filled)
