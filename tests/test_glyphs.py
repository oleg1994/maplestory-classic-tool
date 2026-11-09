import statistics
import time

import numpy as np
import pytest

from maplestory_classic_tool.glyphs import GlyphSet, ReadResult, binarize, column_runs, glyph_key

_SHAPES = {
    "a": ["111", "101", "111", "101", "101"],
    "b": ["110", "101", "110", "101", "110"],
    "c": ["111", "100", "100", "100", "111"],
    "d": ["110", "101", "101", "101", "110"],
    "e": ["111", "100", "111", "100", "111"],
    "X": ["111", "111", "111", "111", "111"],
}


def _glyph(ch):
    return np.array([[c == "1" for c in row] for row in _SHAPES[ch]], bool)


def render(chars, gap=1, space=4):
    cols = []
    pending = gap
    for ch in chars:
        if ch == " ":
            pending = space
            continue
        if cols:
            cols.extend([np.zeros(5, bool)] * pending)
        cols.extend(list(_glyph(ch).T))
        pending = gap
    return np.stack(cols, axis=1)


def make_set(chars, tolerance=0.06):
    return GlyphSet({c: [_glyph(c)] for c in chars}, space_gap=3, tolerance=tolerance)


def test_binarize_threshold():
    out = binarize(np.array([[0, 99, 100, 255]], np.uint8), 100)
    assert out.tolist() == [[True, True, False, False]]


def test_column_runs():
    ink = np.array([[1, 1, 0, 0, 1, 0, 1]], bool)
    assert column_runs(ink) == [(0, 2), (4, 5), (6, 7)]


def test_column_runs_empty():
    assert column_runs(np.zeros((3, 4), bool)) == []


def test_glyph_key_includes_shape():
    k = glyph_key(_glyph("a"))
    assert k[:2] == bytes([5, 3])
    assert k[2:] == np.packbits(_glyph("a")).tobytes()


def test_read_exact_word_with_space():
    assert make_set("abc").read(render("ab c")) == ReadResult("ab c", 0)


def test_read_touching_glyphs_split():
    assert make_set("abc").read(render("abc", gap=0)).text == "abc"


def test_read_hamming_tolerance_accepts_one_flipped_pixel():
    gs = make_set("abc", tolerance=0.07)
    line = render("abc")
    line[2, 1] = ~line[2, 1]  # middle column of 'a'
    assert gs.read(line) == ReadResult("abc", 0)


def test_read_unknown_glyph_marks_question():
    r = make_set("ab").read(render("aXb"))
    assert r.text == "a?b" and r.unknown == 1


def test_add_keeps_structures_consistent():
    gs = make_set("ab")
    gs.add("c", _glyph("c"))
    gs.add("c", _glyph("c"))  # duplicate is a no-op
    assert len(gs.by_width[3]) == 3
    assert gs.read(render("abc")).text == "abc"


def test_save_load_roundtrip(tmp_path):
    gs = make_set("abc")
    path = tmp_path / "glyphs.json"
    gs.save(path)
    loaded = GlyphSet.load(path)
    line = render("ab c")
    assert loaded.read(line) == gs.read(line) == ReadResult("ab c", 0)
    assert loaded.read(render("abc", gap=0)).text == "abc"
    assert loaded.space_gap == 3


@pytest.mark.perf
def test_read_speed_line_under_200us():
    gs = make_set("abcde")
    line = render("abcde abcde abcde abcde", gap=1)
    assert gs.read(line).text == "abcde abcde abcde abcde"
    times = []
    for _ in range(200):
        t = time.perf_counter()
        gs.read(line)
        times.append(time.perf_counter() - t)
    assert statistics.median(times) < 200e-6


def test_read_height_mismatch_is_unknown_not_misread():
    gs = make_set("abc")
    line = np.vstack([render("a"), np.zeros((1, 3), bool)])  # 6 rows vs 5-row templates
    assert gs.read(line) == ReadResult("?", 1)


def test_add_rejects_empty_glyph():
    with pytest.raises(ValueError):
        make_set("a").add("z", np.zeros((5, 0), bool))
