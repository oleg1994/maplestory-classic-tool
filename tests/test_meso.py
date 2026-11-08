from pathlib import Path

import cv2
import numpy as np
import pytest

from mapletool.meso import MESO_GLYPHS, MesoReader, MesoTracker, classify, read_digits

ROOT = Path(__file__).resolve().parents[1]
SHOPS = ROOT / "tests" / "fixtures" / "shops"
ASSETS = ROOT / "mapletool" / "assets"
BASES = {"windowed1600_shop": (1228, 768), "windowed1600_stars": (1228, 768)}
# captures with the item inventory open
TRUTH = {"shop9": 611_814, "shop10": 611_814, "shop18": 599_144, "noshop_grind": 602_357,
         "noshop_partialinv": 616_186}  # the last: the box hangs off the left edge, the number is whole
ALL = sorted(p.stem for p in SHOPS.glob("*.png"))


def frame(name):
    g = cv2.imread(str(SHOPS / f"{name}.png"), cv2.IMREAD_GRAYSCALE)
    return cv2.resize(g, BASES.get(name, (1366, 768)), interpolation=cv2.INTER_AREA)


def reader():
    return MesoReader(cv2.imread(str(ASSETS / "meso_label.png"), cv2.IMREAD_GRAYSCALE))


@pytest.mark.parametrize("name", ALL)
def test_meso_box_read_or_reported_closed(name):
    r = reader().read(frame(name))
    if name in TRUTH:
        assert r.visible and r.mesos == TRUTH[name]
    else:
        assert not r.visible and r.mesos is None


@pytest.mark.parametrize("ch", sorted(MESO_GLYPHS))
def test_every_pattern_is_unambiguous(ch):
    """A glyph drawn with only its sure pixels, or with all its maybe pixels too, reads as itself."""
    a = np.array([list(r) for r in MESO_GLYPHS[ch]])
    assert classify(a == "#") == ch
    assert classify((a == "#") | (a == "+")) == ch


def test_unknown_glyph_gives_no_reading():
    box = np.full((11, 40), 255, np.uint8)
    box[2:9, 5:10] = 0  # a solid block is no digit
    assert read_digits(box) is None


def _draw(text):
    cols = []
    for ch in text:
        a = np.array([list(r) for r in MESO_GLYPHS[ch]])
        cols += [np.where(a != ".", 0, 255).astype(np.uint8), np.full((11, 1), 255, np.uint8)]
    return np.concatenate([np.full((11, 3), 255, np.uint8)] + cols, axis=1)


def test_every_digit_reads_from_its_pattern():
    assert read_digits(_draw("2,370,104")) == 2_370_104
    assert read_digits(_draw("5,689")) == 5_689


def test_bad_grouping_rejected():
    assert read_digits(_draw("59,9144")) is None


def test_position_reused_cheaply(monkeypatch):
    import mapletool.meso as meso
    r = reader()
    g = frame("shop18")
    r.read(g)
    calls = []
    real = meso.cv2.matchTemplate
    monkeypatch.setattr(meso.cv2, "matchTemplate", lambda *a: calls.append(1) or real(*a))
    assert r.read(g).mesos == 599_144 and calls == []


# --- tracker -------------------------------------------------------------------------------------

def feed(tr, samples):
    for t, m in samples:
        tr.add(m, t)


def test_value_counts_after_two_equal_reads():
    tr = MesoTracker()
    assert not tr.add(1000, 0)
    assert tr.add(1000, 1) and tr.mesos == 1000
    assert not tr.add(1500, 2)  # one read: not yet
    assert tr.add(1500, 3) and tr.gained == 500


def test_single_misread_never_counts():
    tr = MesoTracker()
    feed(tr, [(0, 1000), (1, 1000), (2, 91000), (3, 1000), (4, 1000)])
    assert tr.gained == 0 and tr.mesos == 1000


def test_spending_is_not_subtracted():
    tr = MesoTracker()
    feed(tr, [(0, 10_000), (1, 10_000), (2, 4_000), (3, 4_000), (4, 6_000), (5, 6_000)])
    assert tr.gained == 2_000 and tr.mesos == 6_000


def test_rate_per_hour():
    tr = MesoTracker()
    feed(tr, [(0, 0), (1, 0)] + [(t + d, 100 * (t // 30)) for t in range(30, 301, 30) for d in (0, 1)])
    assert tr.gained == 1000
    assert tr.rate(301) == pytest.approx(1000 / 300 * 3600, rel=0.01)


def test_reset_starts_over():
    tr = MesoTracker()
    feed(tr, [(0, 0), (1, 0), (2, 50), (3, 50)])
    tr.reset()
    assert tr.mesos is None and tr.gained == 0 and tr.rate(100) is None


@pytest.mark.parametrize("cut, expected", [(120, 599_144), (122, 599_144), (124, None), (129, None)])
def test_box_cut_at_the_left_edge(cut, expected):
    """shop18 shifted left by `cut` px: the number is read while blank box precedes it at the edge; once a
    digit touches the edge the number may continue beyond it, so there is no reading."""
    g = frame("shop18")[:, cut:]
    r = reader().read(g)
    assert r.visible and r.mesos == expected
