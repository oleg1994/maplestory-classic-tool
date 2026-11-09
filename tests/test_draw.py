import numpy as np

from maplestory_classic_tool import draw


def test_round_rect_points_inside_bounds_and_corners_cut():
    pts = draw.round_rect_points(10, 20, 110, 70, 6)
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    assert min(xs) == 10 and max(xs) == 110 and min(ys) == 20 and max(ys) == 70
    assert (10, 20) not in pts and (110, 70) not in pts


def test_round_rect_points_without_radius_is_the_rectangle():
    assert draw.round_rect_points(0, 0, 5, 5, 0) == [(0, 0), (5, 0), (5, 5), (0, 5)]


def test_gradient_spans_cover_rows_with_corner_insets():
    spans = draw.gradient_spans(0, 0, 40, 20, "#000000", "#ffffff", 5)
    assert len(spans) == 20
    assert spans[0][1] > 0 and spans[10][1] == 0  # first row inset at the corner, middle row full width
    assert spans[0][3] == "#000000" and spans[-1][3] == "#ffffff"


def test_lerp_color():
    assert draw.lerp("#000000", "#ffffff", 0.5) == "#808080"


def test_cv2_renderer_fills_and_text():
    img = np.zeros((60, 200, 3), np.uint8)
    r = draw.CvRenderer(img)
    r.render([draw.Rect(10, 10, 190, 50, "#204060", "#ff0000", 1, 4),
              draw.Text(20, 30, "Hello 1,234", "#ffffff", 14, True, "w")])
    assert tuple(img[30, 100]) != (0, 0, 0)
    assert tuple(img[11, 100]) == (0x60, 0x40, 0x20) or tuple(img[10, 100]) == (0, 0, 255)
    assert r.measure("Hello", 14, True) > r.measure("Hi", 14, True) > 0
