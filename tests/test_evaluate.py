from mapletool.catalog import Catalog
from mapletool.evaluate import evaluate
from mapletool.model import Item, Listing

CAT = Catalog([Item(270, "Red Potion", 5), Item(9, "Event Item", 0)], 0)


def test_profit():
    v = evaluate(Listing(0, "Red Potion", 100, 400, False), CAT, 1)
    assert (v.status, v.profit, v.npc_total) == ("profit", 100, 500)


def test_break_even_below_min_profit():
    v = evaluate(Listing(0, "Red Potion", 100, 500, False), CAT, 1)
    assert (v.status, v.profit) == ("no_profit", 0)


def test_evaluate_uncertain_never_profit():
    assert evaluate(Listing(0, "Red Potion", 100, 1, True), CAT, 1).status == "unknown"


def test_unmatched_name_unknown():
    assert evaluate(Listing(0, "Zzzz Qqqq", 1, 1, False), CAT, 1).status == "unknown"
    assert evaluate(Listing(0, "", 1, 1, False), CAT, 1).status == "unknown"


def test_zero_npc_price_no_profit():
    v = evaluate(Listing(0, "Event Item", 1, 1, False), CAT, 1)
    assert v.status == "no_profit" and v.item.id == 9


KUMBI = Item(297, "Kumbi Throwing Stars", 1500, True, 700)
STAR_CAT = Catalog([KUMBI, Item(417, "Silver Ore", 80)], 0)


def test_rechargeable_full_set_is_one_item():
    v = evaluate(Listing(3, "Kumbi Throwing Stars", 700, 669999, False), STAR_CAT, 1)
    assert (v.npc_total, v.status) == (1500, "no_profit")
    assert v.profit == 1500 - 669999


def test_rechargeable_partial_set_rounds_up():
    v = evaluate(Listing(0, "Kumbi Throwing Stars", 350, 100, False), STAR_CAT, 1)
    assert v.npc_total == 750
    v = evaluate(Listing(0, "Kumbi Throwing Stars", 1, 0 + 1, False), STAR_CAT, 1)
    assert v.npc_total == 3  # ceil(1500 / 700)


def test_normal_item_still_times_qty():
    v = evaluate(Listing(0, "Silver Ore", 3, 100, False), STAR_CAT, 1)
    assert (v.npc_total, v.status, v.profit) == (240, "profit", 140)


# --- cursor robustness: unknown name glyphs + non-exact match need a fully known price ---------

WOLBI = Catalog([Item(295, "Wolbi Throwing Stars", 500, True, 500), Item(270, "Red Potion", 5)], 0)


def test_cursor_junk_fuzzy_name_with_ocr_filled_price_is_unknown():
    li = Listing(0, "Red Potion l", 100, 400, False, name_unknown=True, price_filled=True)
    assert evaluate(li, WOLBI, 1).status == "unknown"


def test_cursor_junk_fuzzy_name_with_known_price_is_evaluated():
    li = Listing(0, "Red Potion l", 100, 400, False, name_unknown=True, price_filled=False)
    v = evaluate(li, WOLBI, 1)
    assert (v.status, v.item.id) == ("profit", 270)


def test_exact_name_with_ocr_filled_price_is_evaluated():
    li = Listing(0, "Red Potion", 100, 400, False, name_unknown=True, price_filled=True)
    assert evaluate(li, WOLBI, 1).status == "profit"


def test_known_name_glyphs_fuzzy_with_ocr_filled_price_is_evaluated():
    li = Listing(0, "Red Pot1on", 100, 400, False, name_unknown=False, price_filled=True)
    assert evaluate(li, WOLBI, 1).status == "profit"



def test_rechargeable_without_slot_size_is_one_set():
    cat = Catalog([Item(10, "Odd Star", 7, True, 1)], 0)
    v = evaluate(Listing(0, "Odd Star", 700, 100, False), cat, 1)
    assert v.npc_total == 7 and v.status == "no_profit"


def test_ambiguous_prefix_verdict_carries_ocr_display():
    cat = Catalog([Item(13, "Two-handed Axe Attack Scroll: Chaos", 200000),
                   Item(14, "Two-handed Axe Attack Scroll: Greater", 200000)], 0)
    v = evaluate(Listing(3, "Two-handed Axe Attack S...", 1, 115000, False), cat, 1)
    assert v.display == "Two-handed Axe Attack S..." and v.status == "profit"
    v = evaluate(Listing(3, "Two-handed Axe Attack Scroll: Ch...", 1, 115000, False), cat, 1)
    assert v.display == "" and v.item.id == 13
