"""Verdicts and overlay annotations."""
from dataclasses import dataclass

from maplestory_classic_tool.catalog import Catalog
from maplestory_classic_tool.layout import Layout
from maplestory_classic_tool.model import Annotation, Listing, Verdict


@dataclass
class ScanResult:
    anchor: tuple | None
    verdicts: list
    changed: bool
    shop_id: object = None  # identity of the open shop (title bar + seller name), None without anchor


def npc_value(item, qty: int) -> int:
    """What the NPC pays for `qty`: per piece, except a rechargeable listing is ONE set of up to
    slot_max (a full set of 700 stars sells for npc_price, never x 700). Values assume the base set size;
    buyers with a larger set (Claw Mastery) get less per star. Unknown set size (slot_max <= 1): the
    whole listing is valued as one set."""
    if item.rechargeable:
        if item.slot_max <= 1:
            return item.npc_price
        return -(-item.npc_price * qty // item.slot_max)
    return item.npc_price * qty


def evaluate(listing: Listing, catalog: Catalog, min_profit: int) -> Verdict:
    if listing.uncertain or not listing.name or listing.qty is None or listing.price is None:
        return Verdict(listing, None, None, None, "unknown")
    match = catalog.lookup(listing.name)
    if match is None:
        return Verdict(listing, None, None, None, "unknown")
    if listing.name_unknown and match.how != "exact" and listing.price_filled:
        # Likely the game's cursor over the row: junk glyphs in the name AND an OCR-filled price.
        # Limited reach: since every digit has a glyph template the price is rarely OCR-filled, and junk
        # that the glyph reader reads as known characters (e.g. "ll") never sets name_unknown. Such variants
        # are mostly absorbed by the side list keying on the catalog item instead.
        return Verdict(listing, None, None, None, "unknown")
    item = match.item
    display = match.display if match.candidates > 1 else ""
    if item.npc_price <= 0:
        return Verdict(listing, item, None, None, "no_profit", display)
    npc_total = npc_value(item, listing.qty)
    profit = npc_total - listing.price
    status = "profit" if profit >= min_profit else "no_profit"
    return Verdict(listing, item, npc_total, profit, status, display)


def annotations(result: ScanResult, layout: Layout, show_all: bool) -> list:
    if result.anchor is None:
        return []
    out = []
    for v in result.verdicts:
        if v.status == "profit":
            text = f"NPC +{v.profit:,}"
        elif not show_all:
            continue
        elif v.status == "no_profit":
            text = f"NPC {v.npc_total:,}" if v.npc_total is not None else "NPC -"
        else:
            text = "NPC ?"
        x, y = layout.row_origin(result.anchor, v.listing.row)
        out.append(Annotation(v.listing.row, x + layout.label_dx, y, text, v.status))
    return out
