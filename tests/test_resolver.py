from mapletool.layout import CONFIRM_MIN, HEAL_MARGIN, BaseResolver, base_candidates, base_family


def test_family_768_even_width():
    # the game keeps 768 px height; width = 768*W/H rounded to the nearest even integer
    assert base_family(3840, 2160)[0] == (1366, 768)  # 1365.33 -> 1366
    assert base_family(2560, 1600)[0] == (1228, 768)  # 1228.8 -> 1228 (even), not 1229
    assert base_family(2560, 1440)[0] == (1366, 768)
    assert base_family(1024, 768)[0] == (1024, 768)
    assert base_family(2560, 1600) == [(1228, 768), (1226, 768), (1230, 768), (1227, 768), (1229, 768)]


def test_candidates_primary_first():
    assert base_candidates(2560, 1600)[0] == (1228, 768)
    assert base_candidates(3840, 2160)[0] == (1366, 768)
    assert base_candidates(1920, 1080)[0] == (1366, 768)
    assert base_candidates(1366, 768)[0] == (1366, 768)


def test_candidates_family_then_standard_tail():
    c = base_candidates(2560, 1600)
    assert c[:5] == base_family(2560, 1600)
    assert (1280, 800) in c[5:] and (1440, 900) in c[5:]
    assert len(c) == len(set(c))
    c = base_candidates(3840, 2160)
    assert c[:5] == [(1366, 768), (1364, 768), (1368, 768), (1365, 768), (1367, 768)]
    assert set(c[5:]) == {(1280, 720), (1600, 900), (1920, 1080)}  # 1366x768 not repeated


def test_tail_ordered_by_prev_scale():
    c = base_candidates(2560, 1600, prev_scale=1.5)
    assert c[5:7] == [(1280, 800), (1680, 1050)]  # integer scale, then closest to 1.5
    c = base_candidates(2560, 1600, prev_scale=1.3)
    assert c[5:7] == [(1280, 800), (1920, 1200)]


def test_candidates_odd_aspect_still_has_family():
    c = base_candidates(2000, 700)
    assert c[0] == (2194, 768)
    assert base_candidates(0, 0) == [(1366, 768)]


def test_resolver_confirmed_is_used():
    r = BaseResolver({"3840x2160": [1366, 768]})
    assert r.current((3840, 2160)) == ((1366, 768), True)


def test_resolver_probes_round_robin_one_per_miss():
    r = BaseResolver({})
    cands = base_candidates(2560, 1600)
    seen = []
    for _ in range(len(cands) + 2):
        base, confirmed = r.current((2560, 1600))
        assert not confirmed
        seen.append(base)
        r.miss((2560, 1600))
    assert seen[:len(cands)] == cands
    assert seen[len(cands):] == cands[:2]  # wraps around


def test_resolver_current_is_stable_without_miss():
    r = BaseResolver({})
    a = r.current((2560, 1600))
    assert r.current((2560, 1600)) == a


def test_resolver_confirm_caches_and_reports():
    saved = []
    r = BaseResolver({}, on_confirm=lambda key, base: saved.append((key, base)))
    assert r.confirm((2560, 1600), (1228, 768)) is True
    assert saved == [("2560x1600", (1228, 768))]
    assert r.current((2560, 1600)) == ((1228, 768), True)
    assert r.confirm((2560, 1600), (1228, 768)) is False  # already confirmed: no second save
    assert saved == [("2560x1600", (1228, 768))]


def test_resolver_confirmed_is_sticky_on_misses():
    r = BaseResolver({"3840x2160": [1366, 768]})
    for _ in range(1000):  # e.g. shop closed for a long time while frames change
        r.miss((3840, 2160))
    assert r.current((3840, 2160)) == ((1366, 768), True)


def test_choose_picks_best_not_first():
    saved = []
    r = BaseResolver({}, on_confirm=lambda k, b: saved.append(b))
    # the probed neighbour passes the 0.92 locator threshold, but the true width scores higher
    scores = {(1228, 768): 0.93, (1226, 768): 0.90, (1230, 768): 0.999, (1227, 768): 0.97, (1229, 768): 0.97}
    assert r.choose((2560, 1600), scores) == (1230, 768)
    assert r.current((2560, 1600)) == ((1230, 768), True) and saved == [(1230, 768)]


def test_choose_requires_confirm_min():
    r = BaseResolver({})
    assert CONFIRM_MIN == 0.95
    assert r.choose((2560, 1600), {(1228, 768): 0.949, (1230, 768): 0.93}) is None
    assert r.current((2560, 1600))[1] is False


def test_heal_switches_on_margin_only():
    saved = []
    r = BaseResolver({"2560x1600": [1226, 768]}, on_confirm=lambda k, b: saved.append(b))
    assert HEAL_MARGIN == 0.02
    # 0.015 better: not enough
    assert r.heal((2560, 1600), {(1226, 768): 0.98, (1228, 768): 0.995}) is None
    assert r.current((2560, 1600)) == ((1226, 768), True) and saved == []
    # 0.039 better: re-confirm
    assert r.heal((2560, 1600), {(1226, 768): 0.96, (1228, 768): 0.999}) == (1228, 768)
    assert r.current((2560, 1600)) == ((1228, 768), True) and saved == [(1228, 768)]


def test_heal_from_never_matching_cache():
    r = BaseResolver({"2560x1600": [1280, 800]})
    assert r.heal((2560, 1600), {(1228, 768): 0.999}) == (1228, 768)  # cached base absent -> scores 0
    r2 = BaseResolver({"2560x1600": [1280, 800]})
    assert r2.heal((2560, 1600), {(1228, 768): 0.94}) is None  # below CONFIRM_MIN


def test_resolver_prev_scale_from_confirmed():
    r = BaseResolver({"3840x2160": [1366, 768]})
    assert abs(r.prev_scale - 3840 / 1366) < 1e-9
    r.confirm((2560, 1600), (1280, 800))
    assert r.prev_scale == 2.0


def test_resolver_ignores_bad_cache_entries():
    r = BaseResolver({"3840x2160": [1366, 768], "junk": [1, 2], "100x100": "x"})
    assert r.confirmed == {"3840x2160": (1366, 768)}
