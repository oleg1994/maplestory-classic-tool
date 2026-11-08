from mapletool.catalog import Catalog, fetch_all, fold_il, normalize, sync
from mapletool.model import Item, Match


def test_normalize():
    assert normalize("  Red  Potion! ") == "red potion"
    assert normalize("One-Handed Axe Attack Scroll: Chaos") == "one handed axe attack scroll chaos"


def test_fold_il():
    assert fold_il("Iron Ingot") == "lron lngot"
    assert fold_il("Skull") == "Skull"


def test_lookup_il_folded_exact():
    it = Item(4000, "Iron Hog's Metal Hoof", 30)
    c = Catalog([it, Item(4001, "Fire Boar's Tooth", 20)], 0)
    assert c.lookup("lron Hog's Metal Hoof") == Match(it, 100.0, "exact")
    assert c.lookup("Iron Hog's Metal Hoof") == Match(it, 100.0, "exact")


def test_lookup_il_folded_prefix():
    c = Catalog([Item(1, "Claw Attack Scroll: Intermediate", 50), Item(2, "Red Potion", 5)], 0)
    m = c.lookup("CIaw Attack ScroII: lnterm...")
    assert m.item.id == 1 and m.how == "prefix"


def test_lookup_exact():
    c = Catalog([Item(270, "Red Potion", 5)], 0)
    assert c.lookup("Red Potion") == Match(Item(270, "Red Potion", 5), 100.0, "exact")


def test_lookup_fuzzy_one_char_off():
    c = Catalog([Item(270, "Red Potion", 5), Item(271, "Orange Potion", 10)], 0)
    m = c.lookup("Red Pot1on")
    assert m.item.id == 270 and m.how == "fuzzy"


def test_lookup_below_cutoff_none():
    c = Catalog([Item(270, "Red Potion", 5)], 0)
    assert c.lookup("Blue Ribbon") is None


def test_lookup_truncated_prefix_unique():
    c = Catalog([Item(1, "Dark Scroll for Gloves for ATT 30%", 50), Item(2, "Red Potion", 5)], 0)
    m = c.lookup("Dark Scroll for Glo...")
    assert m.item.id == 1 and m.how == "prefix"


def test_lookup_truncated_prefix_ambiguous_none():
    c = Catalog([Item(1, "Scroll for Gloves for ATT 10%", 50), Item(2, "Scroll for Gloves for ATT 60%", 70)], 0)
    assert c.lookup("Scroll for Gloves for..") is None


def test_duplicate_names_use_min_price():
    c = Catalog([Item(1, "Snail Shell", 9), Item(2, "Snail Shell", 4)], 0)
    assert c.lookup("Snail Shell").item.npc_price == 4


def test_fetch_all_paginates_and_parses_string_stats(fake_session):
    items = fetch_all(fake_session)
    assert [(i.id, i.npc_price) for i in items] == [(1, 1), (270, 5), (9, 0)]
    assert fake_session.calls[0][2]["User-Agent"] == "mapletool/0.1"


def test_save_load_roundtrip(tmp_path):
    c = Catalog([Item(270, "Red Potion", 5), Item(1, "Snail Shell", 4)], 123.5)
    c.save(tmp_path / "c.json")
    d = Catalog.load(tmp_path / "c.json")
    assert d.items == c.items and d.fetched_at == 123.5


def test_load_missing_returns_empty(tmp_path):
    c = Catalog.load(tmp_path / "nope.json")
    assert len(c) == 0 and c.fetched_at == 0


def test_load_corrupt_returns_empty(tmp_path):
    p = tmp_path / "c.json"
    p.write_text("{not json")
    assert len(Catalog.load(p)) == 0


def test_sync_failure_keeps_cache(tmp_path, failing_session):
    Catalog([Item(270, "Red Potion", 5)], 123.0).save(tmp_path / "c.json")
    assert sync(tmp_path / "c.json", failing_session).lookup("Red Potion").item.id == 270


def test_sync_success_saves(tmp_path, fake_session):
    c = sync(tmp_path / "c.json", fake_session)
    assert len(c) == 3 and len(Catalog.load(tmp_path / "c.json")) == 3


def test_is_stale():
    assert Catalog([], 0).is_stale(7, now=8 * 86400)
    assert not Catalog([], 0).is_stale(7, now=6 * 86400)


def test_sync_garbage_response_keeps_cache(tmp_path):
    from conftest import FakeSession
    path = tmp_path / "c.json"
    Catalog([Item(270, "Red Potion", 5)], 123.0).save(path)
    before = path.read_text()
    for body in ({"foo": 1}, {"items": [], "hasMore": False}, []):
        c = sync(path, FakeSession([body]))
        assert c.lookup("Red Potion").item.id == 270
        assert path.read_text() == before


def test_fetch_all_page_cap_raises():
    import pytest
    from conftest import FakeSession

    class Endless(FakeSession):
        def get(self, url, params=None, timeout=None, headers=None):
            self.calls.append(params)
            from conftest import _Resp
            return _Resp({"items": [{"id": params["page"], "name": "x", "stats": {}}],
                          "hasMore": True})

    s = Endless([])
    with pytest.raises(ValueError):
        fetch_all(s)
    assert len(s.calls) == 200


def test_fuzzy_near_tie_different_prices_none():
    c = Catalog([Item(1, "Tobi Throwing Stars", 2500), Item(2, "Wolbi Throwing Stars", 500)], 0)
    assert c.lookup("Wobi Throwing Stars") is None


def test_fuzzy_near_tie_same_price_matches():
    c = Catalog([Item(1, "Tobi Throwing Stars", 500), Item(2, "Wolbi Throwing Stars", 500)], 0)
    m = c.lookup("Wobi Throwing Stars")
    assert m is not None and m.how == "fuzzy" and m.item.npc_price == 500


def test_truncated_ellipsis_char_unique_prefix():
    c = Catalog([Item(1, "Silver Master Sergeant Medal", 2000), Item(2, "Red Potion", 5)], 0)
    m = c.lookup("Silver Master Serg\u2026")
    assert m is not None and m.item.id == 1 and m.how == "prefix"


def test_truncated_never_falls_to_fuzzy():
    c = Catalog([Item(1, "Silver Master Sergeant Medal", 2000)], 0)
    assert c.lookup("Silve? Master Sergeant ...") is None
    assert c.lookup("Silve? Master Sergeant \u2026") is None
    # fuzzy-close to a full name, but truncated and no prefix candidate
    c2 = Catalog([Item(1, "Red Potion", 5)], 0)
    assert c2.lookup("Red Pot1on..") is None


def test_prefix_same_price_min_id():
    c = Catalog([Item(5, "Claw Scroll A", 50), Item(3, "Claw Scroll B", 50)], 0)
    m = c.lookup("Claw Scr...")
    assert m.item.id == 3 and m.how == "prefix"


def test_prefix_different_prices_none():
    c = Catalog([Item(5, "Claw Scroll A", 50), Item(3, "Claw Scroll B", 60)], 0)
    assert c.lookup("Claw Scr...") is None


# --- rechargeable items, cache v2 ---------------------------------------------

KUMBI = {"id": 297, "name": "Kumbi Throwing Stars", "type": "Use", "subtype": "Throwing Star",
         "stats": '{"watk":21,"price":1500,"slotMax":700,"unitPrice":0.6}'}
SILVER = {"id": 417, "name": "Silver Ore", "type": "Etc", "subtype": "Ore & Mineral", "stats": {"price": 80}}


def test_item_defaults_not_rechargeable():
    it = Item(1, "Red Potion", 5)
    assert it.rechargeable is False and it.slot_max == 1


def test_fetch_all_parses_rechargeable_and_slot_max():
    from conftest import FakeSession
    bullet = {"id": 9, "name": "Bullet", "subtype": "Bullet", "stats": {"price": 10, "slotMax": 1000}}
    unit_only = {"id": 10, "name": "Odd Star", "subtype": None, "stats": '{"price": 7, "unitPrice": 1}'}
    items = {i.id: i for i in fetch_all(FakeSession([{"items": [KUMBI, SILVER, bullet, unit_only],
                                                      "hasMore": False}]))}
    assert items[297] == Item(297, "Kumbi Throwing Stars", 1500, True, 700)
    assert items[417] == Item(417, "Silver Ore", 80, False, 1)
    assert items[9].rechargeable and items[9].slot_max == 1000
    assert items[10].rechargeable and items[10].slot_max == 1
    from mapletool.evaluate import npc_value
    assert npc_value(items[10], 700) == 7  # set size unknown: the whole listing is one set, never x qty


def test_bad_slot_max_falls_back_to_one():
    from conftest import FakeSession
    rec = dict(KUMBI, stats={"price": 1500, "slotMax": "x", "unitPrice": 0.6})
    zero = dict(KUMBI, id=298, stats={"price": 1500, "slotMax": 0, "unitPrice": 0.6})
    items = fetch_all(FakeSession([{"items": [rec, zero], "hasMore": False}]))
    assert [i.slot_max for i in items] == [1, 1]


def test_save_writes_version_2_and_roundtrips_rechargeable(tmp_path):
    import json
    c = Catalog([Item(297, "Kumbi Throwing Stars", 1500, True, 700), Item(417, "Silver Ore", 80)], 5.0)
    c.save(tmp_path / "c.json")
    assert json.loads((tmp_path / "c.json").read_text())["version"] == 2
    assert Catalog.load(tmp_path / "c.json").items == c.items


def test_v1_cache_is_not_trusted(tmp_path):
    import json
    p = tmp_path / "c.json"
    p.write_text(json.dumps({"fetched_at": 123.0, "items": [[297, "Kumbi Throwing Stars", 1500]]}))
    c = Catalog.load(p)
    assert len(c) == 0 and c.fetched_at == 0  # empty -> the app syncs; "No price data" if that fails


def test_sync_failure_with_v1_cache_is_empty(tmp_path, failing_session):
    import json
    p = tmp_path / "c.json"
    p.write_text(json.dumps({"fetched_at": 123.0, "items": [[270, "Red Potion", 5]]}))
    assert len(sync(p, failing_session)) == 0



# --- truncated names with several same-value candidates (fix round 1) ---------------

AXE = [Item(13, "Two-handed Axe Attack Scroll: Chaos", 1), Item(14, "Two-handed Axe Attack Scroll: Greater", 1),
       Item(15, "Two-handed Axe Attack Scroll: Intermediate", 1), Item(16, "Two-handed Axe Attack Scroll: Lesser", 1)]


def test_ambiguous_prefix_reports_candidates_and_displays_ocr_text():
    m = Catalog(AXE, 0).lookup("Two-handed Axe Attack S...")
    assert m.how == "prefix" and m.candidates == 4
    assert m.display == "Two-handed Axe Attack S..."  # never a confident sibling name ("...: Chaos")
    assert m.item.npc_price == 1


def test_unique_prefix_has_one_candidate_and_catalog_display():
    m = Catalog(AXE, 0).lookup("Two-handed Axe Attack Scroll: Ch...")
    assert (m.item.id, m.candidates, m.display) == (13, 1, "")


def test_ambiguous_prefix_display_folds_only_il_glitches():
    c = Catalog([Item(1, "Earring INT Scroll: Intermediate", 9), Item(2, "Earring INT Scroll: Interesting", 9)], 0)
    m = c.lookup("Earring  lNT Scroll: lnter…")
    assert m.candidates == 2 and m.display == "Earring INT Scroll: Inter…"


def test_exact_and_fuzzy_have_one_candidate():
    c = Catalog([Item(270, "Red Potion", 5)], 0)
    assert c.lookup("Red Potion").candidates == 1 and c.lookup("Red Potion").display == ""
    assert c.lookup("Red Pot1on").candidates == 1


def test_prefix_gate_compares_rechargeable_and_slot_max():
    c = Catalog([Item(1, "Star A", 500, True, 500), Item(2, "Star B", 500, True, 800)], 0)
    assert c.lookup("Star ...") is None  # same npc_price, different set size -> different value
    c = Catalog([Item(1, "Star A", 500, True, 500), Item(2, "Star B", 500, False, 1)], 0)
    assert c.lookup("Star ...") is None


def test_fuzzy_gate_compares_rechargeable_and_slot_max():
    c = Catalog([Item(1, "Tobi Throwing Stars", 500, True, 1000), Item(2, "Wolbi Throwing Stars", 500, True, 500)], 0)
    assert c.lookup("Wobi Throwing Stars") is None
