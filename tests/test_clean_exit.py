"""Regression: start -> stop -> destroy must exit cleanly (it used to segfault, exit code 139)."""
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

SCRIPT = r'''
import faulthandler, sys, time
faulthandler.enable()
from types import SimpleNamespace as NS
import numpy as np
import tkinter as tk
from maplestory_classic_tool.live import build_scanner
from maplestory_classic_tool.overlay import Overlay
from maplestory_classic_tool.session import LiveSession
from maplestory_classic_tool.sidelist import Panel, PanelRow
from maplestory_classic_tool.exp import ExpBox

Overlay.deiconify = lambda self: None  # tests never show the overlay window


class FakeHotkeys:  # never registers F8/F9
    def __init__(self, bindings):
        pass

    def start(self):
        pass

    def stop(self):
        pass


def no_sync(path, http):
    raise RuntimeError("offline in tests")


root = tk.Tk()
root.withdraw()
overlay = Overlay(tk.Toplevel(root))
session = LiveSession(root, overlay, build=build_scanner, find_rect=lambda title: (0, 0, 1366, 768),
                      grabber_factory=lambda size: NS(grab=lambda rect, base: np.zeros((base[1], base[0]), np.uint8)),
                      hotkeys_factory=FakeHotkeys, sync_fn=no_sync)
session.start()
print("ocr", session._built[0].fallback is not None, flush=True)
end = time.time() + 0.5
while time.time() < end:
    session.pump()
    root.update()
    time.sleep(0.02)
def row(icon=None):
    return PanelRow("A", "1 for 10 mesos", "+5", True, "+5  A  1 for 10", icon)


overlay.show_panel(Panel(100, 100, 200, 60, "2 items · 1 deals", (row(),), scale=1.0, row_h=34))
texts = sorted(overlay.canvas.itemcget(i, "text") for i in overlay.canvas.find_withtag("lbl")
               if overlay.canvas.type(i) == "text")
print("panel texts", texts, flush=True)
from maplestory_classic_tool.sidelist import icon_png_b64
png = icon_png_b64(np.full((35, 35, 3), 200, np.uint8), 2.0)
overlay.show_panel(Panel(100, 100, 300, 100, "1 items · 1 deals", (row(png),), scale=2.0, row_h=40, icon_w=35))
root.update()
images = [i for i in overlay.canvas.find_withtag("lbl") if overlay.canvas.type(i) == "image"]
print("icon panel images", len(images), overlay._images[0].width(), flush=True)
images_first = overlay._images[0]
overlay.show_panel(Panel(100, 100, 300, 100, "1 items · 1 deals", (row(png),), scale=2.0, row_h=40, icon_w=35))
print("photo cache", len(overlay._photos), "reused", overlay._images[0] is images_first, flush=True)
for i in range(140):
    overlay.photo(icon_png_b64(np.full((4, 4, 3), i, np.uint8), 1.0))
print("photo cache bounded", len(overlay._photos), flush=True)
overlay.clear()
long = (PanelRow("A Rather Long Item Name", "1 for 3,000 mesos", "+12,345", True, "x"),)


def frame_x():
    xs = overlay.canvas.coords(overlay.canvas.find_withtag("lbl")[0])[0::2]
    return min(xs), max(xs)


overlay.show_panel(Panel(1000, 100, 40, 60, "t", long, grow_left=True))
x0, x1 = frame_x()
print("grow left", x0 < 1000 - overlay.vx, x1 == 1040 - overlay.vx, flush=True)
overlay.show_panel(Panel(1000, 100, 40, 60, "t", long, grow_left=False))
x0, x1 = frame_x()
print("grow right", x0 == 1000 - overlay.vx, x1 > 1040 - overlay.vx, flush=True)
overlay.show_exp(ExpBox(500, 600, 200, 56, ((("EXP/h ", "#8cb83a"), ("1.2M", "#ffffff")),
                                             (("+312,450", "#ffffff"), (" this session", "#b2b5b7"))), 2.0))
exp_texts = [overlay.canvas.itemcget(i, "text") for i in overlay.canvas.find_withtag("exp")
             if overlay.canvas.type(i) == "text"]
overlay.clear()
print("exp survives panel clear", exp_texts, len(overlay.canvas.find_withtag("exp")) > 0, flush=True)
overlay.show_exp(None)
print("exp hidden", len(overlay.canvas.find_withtag("exp")) == 0, flush=True)
overlay.deiconify()
print("excluded from capture", overlay.excluded, hex(overlay.capture_affinity()), flush=True)
overlay.withdraw()
overlay.clear()
session.stop()
root.destroy()
print("destroyed", flush=True)
'''


def test_start_stop_destroy_exits_zero(tmp_path):
    script = tmp_path / "clean_exit.py"
    script.write_text(SCRIPT, encoding="utf-8")
    env = dict(os.environ, LOCALAPPDATA=str(tmp_path / "appdata"), PYTHONPATH=str(ROOT))
    r = subprocess.run([sys.executable, str(script)], capture_output=True, encoding="utf-8", timeout=120,
                       cwd=str(tmp_path), env=env)
    out = r.stdout + r.stderr
    assert "destroyed" in r.stdout, out
    assert "panel texts ['+5', '1 for 10 mesos', '2 items · 1 deals', 'A']" in r.stdout, out
    assert "icon panel images 1 70" in r.stdout, out  # the row's item icon (PhotoImage kept)
    assert "photo cache 1 reused True" in r.stdout, out  # a redraw does not decode the icon again
    assert "photo cache bounded 128" in r.stdout, out
    assert "grow left True True" in r.stdout, out  # left of the shop: the right edge stays put
    assert "grow right True True" in r.stdout, out
    assert "exp survives panel clear ['LV', 'EXP/h ', '1.2M', '+312,450', ' this session'] True" in r.stdout, out
    assert "exp hidden True" in r.stdout, out
    assert "excluded from capture True 0x11" in r.stdout, out  # its own capture never sees the overlay
    assert r.returncode == 0, f"exit code {r.returncode}\n{out}"
