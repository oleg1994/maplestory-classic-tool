from pathlib import Path

import cv2
import numpy as np
import pytest

from maplestory_classic_tool.mapid import MAP_RESET_S, MapId, MapWatch, MinimapReader

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures" / "shops"
ASSETS = ROOT / "maplestory_classic_tool" / "assets"
BASES = {"windowed1600_shop": (1228, 768), "windowed1600_stars": (1228, 768)}


def frame(name):
    img = cv2.imread(str(FIXTURES / f"{name}.png"), cv2.IMREAD_GRAYSCALE)
    return cv2.resize(img, BASES.get(name, (1366, 768)), interpolation=cv2.INTER_AREA)


def reader():
    return MinimapReader(cv2.imread(str(ASSETS / "minimap_world.png"), cv2.IMREAD_GRAYSCALE),
                         cv2.imread(str(ASSETS / "minimap_label.png"), cv2.IMREAD_GRAYSCALE))


FM = ["shop1", "shop2", "shop3", "shop4", "noshop_fm", "windowed1600_shop", "windowed1600_stars"]


@pytest.mark.parametrize("name", FM)
def test_expanded_free_market_minimap(name):
    m = reader().read(frame(name))
    assert m is not None and m.form == "expanded"
    assert m.same(reader().read(frame("shop2")))  # same map on every capture, both bases


def test_collapsed_minimap_reads_the_strip():
    r = reader()
    g = frame("noshop_npc")
    assert r.locate(g) == (267, 22)
    m = r.read(g)
    assert m is not None and m.form == "collapsed"
    assert m.ink.shape[1] > 200  # "Victoria Road : Ellinia Department Store", strip found to its left end
    assert not m.same(reader().read(frame("shop2")))


def test_no_minimap_no_id():
    g = frame("shop2")
    g[0:200, 0:400] = 0
    assert reader().read(g) is None


def test_different_name_is_another_map():
    g = frame("shop2")
    a = reader().read(g)
    g2 = g.copy()
    g2[24 + 23:24 + 51, 107 - 58:107 - 10] = 180  # first half of both name lines replaced by the panel colour
    b = reader().read(g2)
    assert b is not None and not a.same(b)


def test_cursor_over_the_name_is_the_same_map():
    g = frame("shop2")
    a = reader().read(g)
    g2 = g.copy()
    g2[24 + 28:24 + 50, 107 - 20:107 - 2] = 255  # the cursor sprite over the name
    b = reader().read(g2)
    assert not np.array_equal(a.ink, b.ink) and a.same(b)


def _id(k):
    ink = np.zeros((28, 96), bool)
    ink[:, k * 30:k * 30 + 30] = True  # wide difference between maps
    return MapId("expanded", ink)


A, B, C = _id(0), _id(1), _id(2)


def test_watch_resets_after_30s_on_another_map():
    w = MapWatch()
    assert not w.observe(A, 0)
    assert not w.observe(B, 10)
    assert not w.observe(B, 10 + MAP_RESET_S - 1)
    assert w.observe(B, 10 + MAP_RESET_S)
    assert w.home is B
    assert not w.observe(B, 100)  # B is the new session's map


def test_watch_return_within_30s_keeps_session():
    w = MapWatch()
    w.observe(A, 0)
    w.observe(B, 5)
    assert not w.observe(A, 30)
    assert not w.observe(A, 60) and w.away_since is None


def test_watch_counts_from_leaving_through_several_maps_and_loading():
    w = MapWatch()
    w.observe(A, 0)
    assert not w.observe(B, 10)
    assert not w.observe(None, 20)  # loading screen / minimap closed
    assert not w.observe(C, 25)
    assert w.observe(C, 40) and w.home is C


def test_watch_never_resets_without_a_minimap():
    w = MapWatch()
    assert not any(w.observe(None, t) for t in range(0, 300, 10))


def test_found_position_is_reused_cheaply(monkeypatch):
    import maplestory_classic_tool.mapid as mapid
    r = reader()
    g = frame("shop2")
    r.read(g)
    shapes = []
    real = mapid.cv2.matchTemplate
    monkeypatch.setattr(mapid.cv2, "matchTemplate", lambda img, tpl, m: shapes.append(img.shape) or real(img, tpl, m))
    assert r.read(g) is not None
    assert len(shapes) == 1 and shapes[0][0] <= 18 and shapes[0][1] < 200  # only the title-row label search


def test_grinding_map_differs_from_the_free_market():
    """noshop_grind: expanded minimap on "Victoria Road / East Rocky Mountain IV"."""
    m = reader().read(frame("noshop_grind"))
    assert m is not None and m.form == "expanded"
    assert not m.same(reader().read(frame("shop2")))
