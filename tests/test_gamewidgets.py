import pytest

from maplestory_classic_tool import gamewidgets as gw


@pytest.fixture
def root(tk_window):
    return tk_window


def test_button_colors_styles_differ_and_disabled_is_grey():
    green = gw.button_colors("green", enabled=True)
    orange = gw.button_colors("orange", enabled=True)
    assert green != orange
    assert gw.button_colors("green", enabled=False) == gw.button_colors("orange", enabled=False)


def test_button_colors_hover_and_pressed_change_the_fill():
    base = gw.button_colors("green", enabled=True)
    hover = gw.button_colors("green", enabled=True, hover=True)
    pressed = gw.button_colors("green", enabled=True, pressed=True)
    assert hover["top"] != base["top"]
    assert (pressed["top"], pressed["bottom"]) == (base["bottom"], base["top"])


def test_button_colors_disabled_ignores_hover_and_press():
    off = gw.button_colors("orange", enabled=False)
    assert gw.button_colors("orange", enabled=False, hover=True, pressed=True) == off


def test_blend_endpoints_and_middle():
    assert gw.blend("#000000", "#ffffff", 0) == "#000000"
    assert gw.blend("#000000", "#ffffff", 1) == "#ffffff"
    assert gw.blend("#000000", "#ffffff", 0.5) == "#808080"


def test_button_state_api_like_ttk(root):
    hits = []
    b = gw.GameButton(root, "ON", command=lambda: hits.append(1))
    b.invoke()
    b.state(["disabled"])
    assert b.instate(["disabled"])
    b.invoke()
    b.state(["!disabled"])
    assert not b.instate(["disabled"])
    b.invoke()
    assert hits == [1, 1]


def test_check_click_toggles_variable_and_calls_command(root):
    import tkinter as tk
    var = tk.BooleanVar(value=False)
    seen = []
    c = gw.GameCheck(root, "EXP tracker", variable=var, command=lambda: seen.append(var.get()))
    c.toggle()
    c.toggle()
    assert seen == [True, False] and var.get() is False


def test_check_redraws_when_variable_set_from_outside(root):
    import tkinter as tk
    var = tk.BooleanVar(value=False)
    c = gw.GameCheck(root, "Show all rows", variable=var)
    assert not c.find_withtag("tick")
    var.set(True)
    assert c.find_withtag("tick")


def test_scale_for_never_below_one(root):
    assert gw.scale_for(root) >= 1.0
