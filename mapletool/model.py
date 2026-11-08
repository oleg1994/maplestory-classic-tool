from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class Item:
    id: int
    name: str
    npc_price: int
    rechargeable: bool = False  # throwing stars/bullets: one listing is one set, whatever its count
    slot_max: int = 1  # full-set size of a rechargeable item


@dataclass(frozen=True)
class Match:
    item: Item
    score: float
    how: Literal["exact", "prefix", "fuzzy"]
    candidates: int = 1  # distinct catalog names that fit (a truncated name can fit several same-value tiers)
    display: str = ""  # name to show when candidates > 1 (the cleaned OCR text); "" -> item.name


@dataclass(frozen=True)
class Listing:
    row: int
    name: str
    qty: int | None
    price: int | None
    uncertain: bool
    name_unknown: bool = False  # the glyph read of the name had unknown glyphs (before any OCR fill)
    price_filled: bool = False  # the price had unknown glyphs that the OCR fallback filled in


@dataclass(frozen=True)
class Verdict:
    listing: Listing
    item: Item | None
    npc_total: int | None
    profit: int | None
    status: Literal["profit", "no_profit", "unknown"]
    display: str = ""  # Match.display: shown (and keyed) instead of item.name for an ambiguous truncated name


@dataclass(frozen=True)
class Annotation:
    row: int
    x: int  # game-client pixel coords
    y: int
    text: str
    status: str
