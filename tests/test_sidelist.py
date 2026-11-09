from maplestory_classic_tool.layout import Layout
from maplestory_classic_tool.model import Item, Listing, Verdict
from maplestory_classic_tool.overlay import OTHER, PROFIT
from maplestory_classic_tool.sidelist import (ABSENT_CLEAR_S, CHAR_W, MAX_CHARS, MAX_ROWS, PANEL_GAP, ListedRow,
                                ShopListing, build_panel, panel_to_screen, row_text)

LAYOUT = Layout(base_size=(1366, 768), anchor_to_grid=(-99, 95), rows=6, row_pitch=40,
                name_box=(3, 6, 158, 14), price_box=(3, 23, 158, 14), ink_max_gray=120,
                label_dx=186, price_format="", window_box=(-141, -58, 523, 406))
ICON_LAYOUT = Layout(base_size=(1366, 768), anchor_to_grid=(-99, 95), rows=6, row_pitch=40,
                     name_box=(3, 6, 158, 14), price_box=(3, 23, 158, 14), ink_max_gray=120,
                     label_dx=186, price_format="", window_box=(-141, -58, 523, 406), icon_box=(-37, 3, 35, 35))
ANCHOR = (563, 210)


_IDS: dict = {}


def item(name):
    """One catalog item per distinct name (stable id)."""
    return Item(_IDS.setdefault(name, 1000 + len(_IDS)), name, 1)


def v(row, name, qty, price, status="no_profit", npc=None, profit=None, as_item=None):
    """`name` is the OCR text; `as_item` the catalog name it matched (default: the same)."""
    it = item(as_item or name) if status != "unknown" else None
    return Verdict(Listing(row, name, qty, price, status == "unknown"), it, npc, profit, status)


def deal(row, name, price, profit, as_item=None):
    return v(row, name, 1, price, "profit", price + profit, profit, as_item)


def plain(row, name, price, npc=1, as_item=None):
    """A listed non-deal row (npc=None: an item with no NPC price, which is never listed)."""
    return v(row, name, 1, price, "no_profit", npc, None if npc is None else npc - price, as_item)


SHOP = [plain(0, "A", 10, 5), deal(1, "B", 20, 7), plain(2, "C", 30, 1),
        plain(3, "D", 40, 2), deal(4, "E", 50, 900), plain(5, "F", 60, 3)]


def scrolled(rows, by, new):
    """Rows shift up by `by`; `new` verdicts fill the bottom (rows renumbered)."""
    kept = rows[by:]
    out = [Verdict(Listing(i, x.listing.name, x.listing.qty, x.listing.price, x.listing.uncertain),
                   x.item, x.npc_total, x.profit, x.status) for i, x in enumerate(kept)]
    for j, x in enumerate(new):
        out.append(Verdict(Listing(len(kept) + j, x.listing.name, x.listing.qty, x.listing.price,
                                   x.listing.uncertain), x.item, x.npc_total, x.profit, x.status))
    return out


# --- ShopListing -------------------------------------------------------------

def test_scroll_frames_union_in_shop_order():
    sl = ShopListing(min_seen=1)
    assert sl.observe("s1", SHOP, 0.0)
    frame2 = scrolled(SHOP, 2, [deal(0, "G", 70, 50), plain(0, "H", 80)])
    assert sl.observe("s1", frame2, 0.1)
    assert [r.name for r in sl.rows] == list("ABCDEFGH")
    assert sl.item_count == 8 and sl.deal_count == 3


def test_same_frame_again_is_not_a_change():
    sl = ShopListing(min_seen=1)
    sl.observe("s1", SHOP, 0.0)
    ver = sl.version
    assert not sl.observe("s1", SHOP, 0.1)
    assert not sl.observe("s1", scrolled(SHOP, 1, []), 0.2)  # subset of known rows
    assert sl.version == ver


def test_count_is_max_identical_rows_in_one_frame():
    rising = [plain(0, "The Rising", 160000), plain(1, "The Rising", 160000), plain(2, "X", 5)]
    sl = ShopListing(min_seen=1)
    sl.observe("s", rising, 0.0)
    assert [(r.name, r.count) for r in sl.rows] == [("The Rising", 2), ("X", 1)]
    sl.observe("s", [plain(0, "The Rising", 160000)], 0.1)  # scrolled: only one visible now
    assert sl.rows[0].count == 2
    assert sl.item_count == 3


def test_key_is_catalog_item_qty_price_and_name_is_catalog_name():
    sl = ShopListing(min_seen=1)
    sl.observe("s", [plain(0, "Red  Potion", 10, as_item="Red Potion"),
                     plain(1, "red potion", 10, as_item="Red Potion"), plain(2, "Red Potion", 11)], 0)
    assert [(r.name, r.price, r.count) for r in sl.rows] == [("Red Potion", 10, 2), ("Red Potion", 11, 1)]


STAR = "Kumbi Throwing Stars"


def _stars(*names):
    return [v(3 + i, n, 700, 669999, "no_profit", 1500, 1500 - 669999, as_item=STAR) for i, n in enumerate(names)]


def test_cursor_junk_variants_of_one_listing_collapse():
    """Live: the cursor sprite added junk glyphs; one listing showed up under five names."""
    sl = ShopListing()
    sl.observe("s", _stars("Kumbi Throwing Stars II", STAR, STAR), 0.0)
    sl.observe("s", _stars(STAR, "Kumbi Throwing Stars lb", STAR), 0.1)
    sl.observe("s", _stars(STAR, STAR, "Kumbi Throwing Stars 1B"), 0.2)
    assert [(r.name, r.qty, r.price, r.count) for r in sl.rows] == [(STAR, 700, 669999, 3)]
    assert sl.item_count == 3 and sl.deal_count == 0
    assert row_text(sl.rows[0]) == "NPC 1,500  Kumbi Throwing Stars  700 for 669,999  (x3)"


def test_unmatched_rows_are_never_listed():
    sl = ShopListing(min_seen=1)
    sl.observe("s", [v(0, "Iron Ore ??", 1, 50, "unknown"), plain(1, "Silver Ore", 88, 80)], 0.0)
    assert [r.name for r in sl.rows] == ["Silver Ore"]
    assert sl.item_count == 1
    p = build_panel(sl, ANCHOR, LAYOUT, show_all=True)
    assert p.title == "1 items · 0 deals"
    assert [t for t, _ in p.lines] == ["NPC 80  Silver Ore  1 for 88"]


def test_matched_but_unknown_rows_are_never_listed():
    unk = Verdict(Listing(0, "Emerald Ore ?", None, None, True), item("Emerald Ore"), None, None, "unknown")
    sl = ShopListing(min_seen=1)
    sl.observe("s", [unk, deal(1, "B", 20, 7)], 0.0)
    assert [r.name for r in sl.rows] == ["B"]
    assert (sl.item_count, sl.deal_count) == (1, 1)
    assert [t for t, _ in build_panel(sl, ANCHOR, LAYOUT, show_all=True).lines] == ["+7  B  1 for 20"]


def test_new_shop_identity_clears():
    sl = ShopListing(min_seen=1)
    sl.observe("s1", SHOP, 0.0)
    assert sl.observe("s2", [plain(0, "Z", 1)], 0.1)
    assert [r.name for r in sl.rows] == ["Z"]


def test_returning_to_identity_within_presence_restores():
    """A cursor over the title bar changes the hash briefly; the original list comes back."""
    sl = ShopListing(min_seen=1)
    sl.observe("s1", SHOP, 0.0)
    sl.observe("s1", scrolled(SHOP, 2, [plain(0, "G", 70)]), 0.1)
    sl.observe("cursor", SHOP[:3], 0.2)
    assert sl.item_count == 3
    assert sl.observe("s1", SHOP, 0.3)
    assert [r.name for r in sl.rows] == list("ABCDEFG")


def test_absence_over_one_second_clears():
    sl = ShopListing(min_seen=1)
    sl.observe("s1", SHOP, 0.0)
    assert not sl.absent(0.5)
    assert sl.item_count == 6
    assert not sl.absent(ABSENT_CLEAR_S)  # exactly 1.0 s: kept
    assert sl.absent(1.01)
    assert sl.rows == [] and sl.shop_id is None
    # the old identity is forgotten too
    sl.observe("s1", SHOP[:1], 1.2)
    assert sl.item_count == 1


def test_brief_absence_keeps_list():
    sl = ShopListing(min_seen=1)
    sl.observe("s1", SHOP, 0.0)
    sl.absent(0.4)
    assert not sl.observe("s1", SHOP, 0.6)
    assert sl.absent(1.2) is False  # last seen at 0.6
    assert sl.item_count == 6


def test_clear():
    sl = ShopListing(min_seen=1)
    sl.observe("s1", SHOP, 0.0)
    sl.clear()
    assert sl.rows == [] and sl.deal_count == 0


def test_unknown_rows_hidden():
    sl = ShopListing(min_seen=1)
    sl.observe("s", [v(0, "Mystery", None, None, "unknown")], 0)
    assert sl.rows == [] and sl.deal_count == 0 and sl.item_count == 0


# --- panel layout ------------------------------------------------------------

def _listing(rows):
    sl = ShopListing(min_seen=1)
    sl.observe("s", rows, 0.0)
    return sl


def test_items_without_an_npc_price_are_not_listed():
    """Catalog items with no price on meowdb (npc_total None) never become rows; deals still do."""
    sl = _listing([plain(0, "NoPrice", 99, None), plain(1, "Priced", 50, 10), deal(2, "Deal", 20, 7)])
    assert [r.name for r in sl.rows] == ["Priced", "Deal"]
    p = build_panel(sl, ANCHOR, LAYOUT, show_all=True)
    assert len(p.lines) == 2


def test_row_text_formats():
    assert row_text(_listing([deal(0, "Name", 3000, 12345)]).rows[0]) == "+12,345  Name  1 for 3,000"
    assert row_text(_listing([plain(0, "Name", 99, 1234)]).rows[0]) == "NPC 1,234  Name  1 for 99"
    assert row_text(ListedRow("Name", 1, 99, "no_profit", None, None, 1, 0)) == "NPC -  Name  1 for 99"
    assert row_text(ListedRow("Name", None, None, "unknown", None, None, 1, 0)) == "?  Name"
    assert row_text(ListedRow("Name", 2, 500, "unknown", None, None, 1, 0)) == "?  Name  2 for 500"
    two = _listing([plain(0, "N", 5, 9), plain(1, "N", 5, 9)]).rows[0]
    assert row_text(two) == "NPC 9  N  1 for 5  (x2)"


def test_panel_left_of_window():
    p = build_panel(_listing(SHOP), ANCHOR, LAYOUT, show_all=False)
    wx, wy, ww, _ = LAYOUT.window_rect(ANCHOR)
    assert (wx, wy) == (422, 152)
    assert p.x == wx - PANEL_GAP - p.w and p.y == wy
    assert p.x >= 0


def test_panel_flips_right_when_no_room():
    anchor = (150, 210)
    p = build_panel(_listing(SHOP), anchor, LAYOUT, show_all=False)
    wx, _, ww, _ = LAYOUT.window_rect(anchor)
    assert p.x == wx + ww + PANEL_GAP


def test_panel_sort_and_show_all_filter():
    sl = _listing(SHOP)
    few = build_panel(sl, ANCHOR, LAYOUT, show_all=False)
    assert few.title == "6 items · 2 deals"
    assert [t for t, _ in few.lines] == ["+900  E  1 for 50", "+7  B  1 for 20"]
    assert all(c == PROFIT for _, c in few.lines)
    many = build_panel(sl, ANCHOR, LAYOUT, show_all=True)
    assert [t.split("  ")[1] for t, _ in many.lines] == ["E", "B", "A", "C", "D", "F"]
    assert [c for _, c in many.lines] == [PROFIT, PROFIT] + [OTHER] * 4


def test_panel_no_deals_title_only():
    p = build_panel(_listing([plain(0, "A", 10, 5)]), ANCHOR, LAYOUT, show_all=False)
    assert p.title == "1 items · 0 deals" and p.lines == ()


def test_panel_truncates_to_max_rows():
    rows = [deal(i % 6, f"Item{i}", 10, 100 + i) for i in range(25)]
    sl = ShopListing(min_seen=1)
    for i in range(0, 25, 5):
        sl.observe("s", rows[i:i + 5], i)
    p = build_panel(sl, ANCHOR, LAYOUT, show_all=True)
    assert len(p.lines) == MAX_ROWS + 1
    assert p.lines[0][0].startswith("+124  Item24")
    assert p.lines[-1][0] == "… and 5 more"


def test_panel_height_grows_with_lines():
    a = build_panel(_listing(SHOP), ANCHOR, LAYOUT, show_all=False)
    b = build_panel(_listing(SHOP), ANCHOR, LAYOUT, show_all=True)
    assert b.h > a.h and b.w >= a.w


def test_panel_to_screen_scales_and_offsets():
    p = build_panel(_listing(SHOP), ANCHOR, LAYOUT, show_all=False)
    d = panel_to_screen(p, (100, 200), 2.0)
    assert (d.x, d.y, d.w, d.h) == (100 + 2 * p.x, 200 + 2 * p.y, 2 * p.w, 2 * p.h)
    assert d.scale == 2.0 and d.font_px == 2 * p.font_px
    assert d.title == p.title and d.lines == p.lines and d.rows == p.rows


# --- stability gate, slot supersession, distinct counts (review round 2) ------

def test_transient_misread_seen_once_never_shows_as_deal():
    sl = ShopListing()
    good = [plain(0, "A", 10, 5)]
    sl.observe("s", good, 0.0)
    sl.observe("s", good, 0.1)
    sl.observe("s", [deal(0, "A", 1, 9999)], 0.2)  # one glitched frame reads a price of 1
    sl.observe("s", good, 0.3)
    sl.observe("s", good, 0.4)
    assert sl.deal_count == 0
    assert [r.price for r in sl.rows] == [10]
    assert build_panel(sl, ANCHOR, LAYOUT, show_all=True).lines[0][0].startswith("NPC 5  A")


def test_row_seen_twice_shows():
    sl = ShopListing()
    rows = [deal(0, "B", 20, 7)]
    assert sl.observe("s", rows, 0.0) is False  # pending: nothing visible changed
    assert sl.deal_count == 0 and sl.rows == [] and sl.item_count == 0
    assert sl.observe("s", rows, 0.1) is True
    assert sl.deal_count == 1 and [r.name for r in sl.rows] == ["B"]


def test_once_seen_key_dropped_when_its_slot_reads_differently():
    sl = ShopListing()
    sl.observe("s", [plain(0, "A", 10), plain(1, "Bxx", 20)], 0.0)  # row 1 misread once
    sl.observe("s", [plain(0, "A", 10), plain(1, "B", 20)], 0.1)
    sl.observe("s", [plain(0, "A", 10), plain(1, "B", 20)], 0.2)
    assert [r.name for r in sl.rows] == ["A", "B"]
    assert all(r.name != "Bxx" for r in sl._shop.rows.values())  # dropped, not just hidden


def test_scrolling_does_not_drop_rows_that_moved_slots():
    sl = ShopListing()
    sl.observe("s", [plain(0, "A", 10), plain(1, "B", 20)], 0.0)
    sl.observe("s", [plain(0, "B", 20), plain(1, "C", 30)], 0.1)  # scrolled after one frame
    sl.observe("s", [plain(0, "B", 20), plain(1, "C", 30)], 0.2)
    # A was seen once and its slot now shows B: dropped; B moved slots and stays
    assert [r.name for r in sl.rows] == ["B", "C"]


def test_item_count_distinct_when_unknown_read_is_later_corrected():
    sl = ShopListing()
    unk = [plain(0, "A", 10, 5), v(1, "Fairy's Tentacle", None, None, "unknown")]
    good = [plain(0, "A", 10, 5), plain(1, "Fairy's Tentacle", 2500, 300)]
    for t in (0.0, 0.1, 0.2):
        sl.observe("s", unk, t)
    assert sl.item_count == 1  # the unreadable row is neither listed nor counted
    sl.observe("s", good, 0.3)
    sl.observe("s", good, 0.4)
    assert sl.item_count == 2
    assert [(r.name, r.status) for r in sl.rows] == [("A", "no_profit"), ("Fairy's Tentacle", "no_profit")]


# --- name-first clipping (review round 2) ---------------------------------------

def test_char_w_estimate():
    assert CHAR_W == 5


def test_long_name_is_clipped_never_the_qty_price_tail():
    long_name = "Gloves Attack Scroll for Intermediate Adventurers Extra Long"
    row = _listing([deal(0, long_name, 1499999, 1234567)]).rows[0]
    text = row_text(row, MAX_CHARS)
    assert len(text) == MAX_CHARS
    assert text.startswith("+1,234,567  Gloves Attack")
    assert text.endswith("…  1 for 1,499,999")
    p = build_panel(_listing([deal(0, long_name, 1499999, 1234567)]), ANCHOR, LAYOUT, show_all=False)
    assert p.lines[0][0].endswith("1 for 1,499,999")


def test_observe_none_shop_id_is_ignored():
    from maplestory_classic_tool.sidelist import ShopListing
    listing = ShopListing()
    assert listing.observe(None, [], now=1.0) is False



# --- item icons -------------------------------------------------------------------

import base64  # noqa: E402

import cv2  # noqa: E402
import numpy as np  # noqa: E402

from maplestory_classic_tool.sidelist import (LIST_GAP, LIST_PAD, PAD, ROW_PITCH, ROW_PITCH_COMPACT,  # noqa: E402
                                ROW_PITCH_TEXT, TITLE_H, panel_height)


def icon(v):
    return np.full((35, 35, 3), v, np.uint8)


def test_most_frequent_icon_wins():
    sl = ShopListing(min_seen=1)
    rows = [plain(0, "A", 10, 5)]
    sl.observe("s", rows, 0.0, icons={0: icon(100)})  # cursor over the icon in the first frame
    sl.observe("s", rows, 0.1, icons={0: icon(200)})
    assert int(sl.rows[0].icon[0, 0, 0]) == 100  # tie: first seen kept
    sl.observe("s", rows, 0.2, icons={0: icon(200)})
    assert int(sl.rows[0].icon[0, 0, 0]) == 200
    sl.observe("s", rows, 0.3)  # no crops this frame: unchanged
    assert int(sl.rows[0].icon[0, 0, 0]) == 200


def test_icon_votes_per_listing_key_and_unknown_rows_ignored():
    sl = ShopListing(min_seen=1)
    rows = [plain(0, "A", 10, 5), v(1, "Iron Ore ??", 1, 50, "unknown"), plain(2, "A", 10, 5)]
    sl.observe("s", rows, 0.0, icons={0: icon(10), 1: icon(99), 2: icon(10)})
    assert len(sl.rows) == 1 and sl.rows[0].count == 2
    assert int(sl.rows[0].icon[0, 0, 0]) == 10


def test_icon_variants_are_capped():
    from maplestory_classic_tool.sidelist import MAX_ICON_VARIANTS
    sl = ShopListing(min_seen=1)
    rows = [plain(0, "A", 10, 5)]
    sl.observe("s", rows, 0.0, icons={0: icon(7)})
    sl.observe("s", rows, 0.0, icons={0: icon(7)})
    for i in range(MAX_ICON_VARIANTS * 3):
        sl.observe("s", rows, 0.1, icons={0: icon(20 + i)})
    r = sl._shop.rows[next(iter(sl._shop.rows))]
    assert len(r.icon_votes) <= MAX_ICON_VARIANTS
    assert int(r.icon[0, 0, 0]) == 7


def test_new_best_icon_bumps_version():
    sl = ShopListing(min_seen=1)
    rows = [plain(0, "A", 10, 5)]
    sl.observe("s", rows, 0.0)
    ver = sl.version
    assert sl.observe("s", rows, 0.1, icons={0: icon(50)})
    assert sl.version > ver


def _icon_listing(n):
    sl = ShopListing(min_seen=1)
    for i in range(0, n, 6):
        chunk = [deal(j, f"Item{i + j}", 10, 100 + i + j) for j in range(min(6, n - i))]
        sl.observe("s", chunk, i, icons={j: icon(30 + j) for j in range(len(chunk))})
    return sl


def test_panel_rows_use_the_shop_row_pitch_with_icons():
    sl = _icon_listing(3)
    p = build_panel(sl, ANCHOR, ICON_LAYOUT, show_all=True, frame_h=768)
    assert p.row_h == ROW_PITCH == 40 and p.icon_w == 35
    assert len(p.icons) == len(p.lines) == 3 and all(i is not None for i in p.icons)
    assert p.h == panel_height(3, ROW_PITCH, False)
    assert p.h == 2 * PAD + TITLE_H + LIST_GAP + 2 * LIST_PAD + 3 * ROW_PITCH


def test_panel_without_icon_box_keeps_text_rows():
    p = build_panel(_icon_listing(3), ANCHOR, LAYOUT, show_all=True)
    assert p.row_h == ROW_PITCH_TEXT and p.icon_w == 0
    assert p.h == panel_height(3, ROW_PITCH_TEXT, False)


def test_panel_compacts_rows_before_capping():
    sl = _icon_listing(16)
    fits_compact = build_panel(sl, ANCHOR, ICON_LAYOUT, show_all=True, frame_h=panel_height(16, ROW_PITCH_COMPACT, False))
    assert fits_compact.row_h == ROW_PITCH_COMPACT and len(fits_compact.icons) == 16 and not fits_compact.more


def test_panel_capped_to_game_window_height():
    sl = _icon_listing(30)
    p = build_panel(sl, ANCHOR, ICON_LAYOUT, show_all=True, frame_h=400)
    assert p.row_h == ROW_PITCH_COMPACT
    fit = len(p.icons)
    assert panel_height(fit, ROW_PITCH_COMPACT, True) <= 400 < panel_height(fit + 1, ROW_PITCH_COMPACT, True)
    assert len(p.lines) == fit + 1
    assert p.lines[-1][0] == f"… and {30 - fit} more" == p.more
    assert p.h <= 400 and p.y + p.h <= 400 and p.y >= 0


def test_panel_to_screen_encodes_scaled_png_icons():
    sl = _icon_listing(2)
    p = build_panel(sl, ANCHOR, ICON_LAYOUT, show_all=True, frame_h=768)
    d = panel_to_screen(p, (100, 200), 2.0)
    assert d.row_h == p.row_h and d.icon_w == p.icon_w and d.scale == 2.0  # base metrics x scale
    assert len(d.icons) == 2
    img = cv2.imdecode(np.frombuffer(base64.b64decode(d.icons[0]), np.uint8), cv2.IMREAD_COLOR)
    assert img.shape == (70, 70, 3)



# --- ambiguous truncated names (fix round 1) -------------------------------------------

def _axe_verdicts():
    from maplestory_classic_tool.catalog import Catalog
    from maplestory_classic_tool.evaluate import evaluate
    cat = Catalog([Item(13, "Two-handed Axe Attack Scroll: Chaos", 1),
                   Item(14, "Two-handed Axe Attack Scroll: Greater", 1)], 0)
    texts = ["Two-handed Axe Attack S...", "Two-handed Axe Attack Scroll: Ch..."]
    return [evaluate(Listing(i, t, 1, 115000, False), cat, 1) for i, t in enumerate(texts)]


def test_ambiguous_prefix_row_shows_ocr_text_and_does_not_collapse_with_a_tier():
    vs = _axe_verdicts()
    assert vs[0].item.id == vs[1].item.id == 13  # the lowest id stands in for the value only
    sl = ShopListing(min_seen=1)
    sl.observe("s", vs, 0.0)
    assert sorted((r.name, r.count) for r in sl.rows) == [
        ("Two-handed Axe Attack S...", 1), ("Two-handed Axe Attack Scroll: Chaos", 1)]
    assert sl.item_count == 2


def test_ambiguous_prefix_key_is_normalized_ocr_text():
    vs = _axe_verdicts()
    again = Verdict(Listing(2, "Two-handed  Axe Attack S…", 1, 115000, False), vs[0].item, vs[0].npc_total,
                    vs[0].profit, vs[0].status, display="Two-handed  Axe Attack S…")
    sl = ShopListing(min_seen=1)
    sl.observe("s", [vs[0], again], 0.0)
    assert [(r.name, r.count) for r in sl.rows] == [("Two-handed Axe Attack S...", 2)]



def test_panel_grows_away_from_the_shop():
    left = build_panel(_listing(SHOP), ANCHOR, LAYOUT, show_all=True)
    assert left.grow_left is True  # placed left of the shop: extra width must grow leftwards
    right = build_panel(_listing(SHOP), (150, 210), LAYOUT, show_all=True)
    assert right.grow_left is False
    assert panel_to_screen(left, (0, 0), 2.0).grow_left is True


# --- shop-window style (EXP/side-list restyle) ------------------------------------------------

from maplestory_classic_tool import draw  # noqa: E402
from maplestory_classic_tool.sidelist import (DEAL_BADGE, NPC_BADGE, SHOP_EDGE, SHOP_FRAME, SHOP_ICON_TILE,  # noqa: E402
                                SHOP_LIST_BG, SHOP_ROW_BG, SHOP_ROW_LINE, SHOP_TEXT, SHOP_TITLE_BG,
                                panel_prims)


def fake_measure(text, px, bold):
    return len(text) * px * 0.5


def _styled(show_all=True, scale=1.0):
    rows = [deal(0, "Wolbi Throwing Stars", 3000, 12345), plain(1, "Fairy's Tentacle", 2500, 1234)]
    sl = ShopListing(min_seen=1)
    sl.observe("s", rows, 0.0, icons={0: icon(40), 1: icon(80)})
    p = build_panel(sl, ANCHOR, ICON_LAYOUT, show_all=show_all, frame_h=768)
    return p if scale == 1.0 else panel_to_screen(p, (0, 0), scale)


def _of(prims, kind):
    return [q for q in prims if isinstance(q, kind)]


def test_panel_rows_are_name_detail_and_badge():
    p = _styled()
    assert [(r.name, r.detail, r.badge, r.deal) for r in p.rows] == [
        ("Wolbi Throwing Stars", "1 for 3,000 mesos", "+12,345", True),
        ("Fairy's Tentacle", "1 for 2,500 mesos", "NPC 1,234", False)]
    assert p.title == "2 items · 1 deals"


def test_panel_prims_use_the_shop_colours():
    p = _styled()
    prims, x, w = panel_prims(p, fake_measure)
    fills = {q.fill for q in _of(prims, draw.Rect) if q.fill}
    assert {SHOP_FRAME, SHOP_TITLE_BG, SHOP_LIST_BG, SHOP_ROW_BG} <= fills
    frame = _of(prims, draw.Rect)[0]
    assert frame.fill == SHOP_FRAME and frame.outline == SHOP_EDGE and frame.radius > 0
    assert (frame.x0, frame.x1) == (x, x + w)
    assert any(isinstance(q, draw.Line) and q.color == SHOP_ROW_LINE for q in prims)  # row separator
    texts = {q.text: q for q in _of(prims, draw.Text)}
    assert texts["Wolbi Throwing Stars"].color == SHOP_TEXT
    assert texts["1 for 3,000 mesos"].color == SHOP_TEXT
    assert texts["2 items · 1 deals"].color != SHOP_TITLE_BG


def test_panel_badges_right_aligned_green_deal_grey_npc():
    p = _styled()
    prims, x, w = panel_prims(p, fake_measure)
    texts = {q.text: q for q in _of(prims, draw.Text)}
    deal_t, npc_t = texts["+12,345"], texts["NPC 1,234"]
    assert deal_t.anchor == npc_t.anchor == "e" and deal_t.x == npc_t.x  # same right edge
    badges = [q for q in _of(prims, draw.Rect) if q.fill in (DEAL_BADGE, NPC_BADGE)]
    assert [b.fill for b in badges] == [DEAL_BADGE, NPC_BADGE]
    assert all(b.radius > 0 for b in badges)
    assert badges[0].x1 == badges[1].x1 <= x + w - PAD
    assert badges[0].x0 < deal_t.x - fake_measure("+12,345", 10, True)  # text inside its badge


def test_panel_icons_sit_left_of_the_text_and_missing_icons_get_a_cream_tile():
    p = _styled()
    prims, x, w = panel_prims(p, fake_measure)
    imgs = _of(prims, draw.Image)
    assert len(imgs) == 2
    assert imgs[1].y - imgs[0].y == ROW_PITCH
    texts = {q.text: q for q in _of(prims, draw.Text)}
    assert texts["Wolbi Throwing Stars"].x > imgs[0].x + 35
    from dataclasses import replace
    bare = replace(p, rows=tuple(replace(r, icon=None) for r in p.rows))
    tiles = [q for q in _of(panel_prims(bare, fake_measure)[0], draw.Rect) if q.fill == SHOP_ICON_TILE]
    assert [(t.x0, t.y0) for t in tiles] == [(i.x, i.y) for i in imgs]
    assert texts["Wolbi Throwing Stars"].y < texts["1 for 3,000 mesos"].y  # name above the price line


def test_panel_prims_scale_with_the_screen_panel():
    p1 = _styled()
    p2 = _styled(scale=2.0)
    a, xa, wa = panel_prims(p1, fake_measure)
    b, xb, wb = panel_prims(p2, fake_measure)
    ta = {q.text: q for q in _of(a, draw.Text)}
    tb = {q.text: q for q in _of(b, draw.Text)}
    assert tb["+12,345"].px == 2 * ta["+12,345"].px
    assert abs(wb - 2 * wa) <= 2


def test_panel_width_follows_measured_text_and_grows_away_from_the_shop():
    p = _styled()
    wide = lambda t, px, b: len(t) * px * 0.9  # noqa: E731
    _, x1, w1 = panel_prims(p, fake_measure)
    _, x2, w2 = panel_prims(p, wide)
    assert w2 > w1
    assert p.grow_left and x1 + w1 == x2 + w2 == p.x + p.w  # edge next to the shop stays put
    right = build_panel(_listing(SHOP), (150, 210), LAYOUT, show_all=True)
    _, xr, _ = panel_prims(right, wide)
    assert xr == right.x


def test_long_name_clipped_in_the_name_line_never_the_price_line():
    long_name = "Gloves Attack Scroll for Intermediate Adventurers Extra Long"
    p = build_panel(_listing([deal(0, long_name, 1499999, 1234567)]), ANCHOR, LAYOUT, show_all=False)
    r = p.rows[0]
    assert r.name.endswith("…") and len(r.name) <= 32 and long_name.startswith(r.name[:-1])
    assert r.detail == "1 for 1,499,999 mesos" and r.badge == "+1,234,567"


def test_duplicate_listing_count_on_the_price_line():
    p = build_panel(_listing([plain(0, "N", 5, 9), plain(1, "N", 5, 9)]), ANCHOR, LAYOUT, show_all=True)
    assert p.rows[0].detail == "1 for 5 mesos  (x2)" and p.rows[0].badge == "NPC 9"


def test_empty_panel_is_title_strip_only():
    p = build_panel(_listing([plain(0, "A", 10, 5)]), ANCHOR, LAYOUT, show_all=False)
    prims, _, _ = panel_prims(p, fake_measure)
    assert p.h == 2 * PAD + TITLE_H
    assert not [q for q in _of(prims, draw.Rect) if q.fill == SHOP_LIST_BG]



def test_icon_rows_skip_the_tile_under_an_icon():
    """The icon crop is the shop's own cream cell: no tile is drawn under it (fewer canvas items)."""
    p = _styled()
    prims, _, _ = panel_prims(p, fake_measure)
    assert not [q for q in _of(prims, draw.Rect) if q.fill == SHOP_ICON_TILE]
    assert len(_of(prims, draw.Image)) == 2


def test_cached_measure_calls_the_font_once_per_text():
    from maplestory_classic_tool.draw import cached_measure
    calls = []

    def m(t, px, b):
        calls.append(t)
        return len(t)
    cm = cached_measure(m)
    assert cm("abc", 10.2, True) == 3 and cm("abc", 10.4, True) == 3  # same rounded size
    assert cm("abc", 10, False) == 3
    assert calls == ["abc", "abc"]
