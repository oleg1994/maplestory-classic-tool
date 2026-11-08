import subprocess
import sys

import pytest

from types import SimpleNamespace as NS

from mapletool import panel
from mapletool.panel import button_states, status_text


@pytest.mark.parametrize("state,text", [
    (("off",), "Off"),
    (("starting",), "Starting…"),
    (("waiting",), "On — waiting for game"),
    (("no_shop",), "On — no shop open"),
    (("shop", 3), "On — shop found (3 deals)"),
    (("shop", 1), "On — shop found (1 deals)"),
    (("no_data",), "No price data"),
])
def test_status_text(state, text):
    assert status_text(state) == text


def test_button_states():
    assert button_states(("off",)) == {"on": True, "off": False}
    for st in [("starting",), ("waiting",), ("no_shop",), ("shop", 0), ("no_data",)]:
        assert button_states(st) == {"on": False, "off": True}


def test_selftest_subprocess_exit_zero():
    r = subprocess.run([sys.executable, "-m", "mapletool", "--selftest"], capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stdout + r.stderr


def test_relaunch_args(monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    assert panel.relaunch_args() == (sys.executable, "")
    monkeypatch.setattr(sys, "frozen", False, raising=False)
    assert panel.relaunch_args() == (sys.executable, "-m mapletool --gui")


def _fake_shell(monkeypatch, rc):
    calls = []

    def shell(*a):
        calls.append(a)
        return rc
    monkeypatch.setattr(panel, "ctypes", NS(windll=NS(shell32=NS(ShellExecuteW=shell))))
    return calls


class FakeInstance:
    def __init__(self, order):
        self.order = order

    def release(self):
        self.order.append("release")

    def acquire(self):
        self.order.append("acquire")
        return True


def test_restart_declined_leaves_session_and_window(monkeypatch):
    calls = _fake_shell(monkeypatch, 5)  # <= 32: UAC declined / failure
    order = []
    fake = NS(session=NS(stop=lambda: pytest.fail("stopped"), close=lambda: pytest.fail("closed")),
              root=NS(destroy=lambda: pytest.fail("destroyed")), instance=FakeInstance(order))
    panel.ControlPanel._restart(fake)
    assert calls and calls[0][1] == "runas"
    assert order == ["release", "acquire"]  # the single-instance lock is taken back


def test_restart_success_releases_lock_first_then_closes_and_quits(monkeypatch):
    order = []

    def shell(*a):
        order.append("shell")
        return 42
    monkeypatch.setattr(panel, "ctypes", NS(windll=NS(shell32=NS(ShellExecuteW=shell))))
    fake = NS(session=NS(close=lambda: order.append("close")), root=NS(destroy=lambda: order.append("destroy")),
              instance=FakeInstance(order))
    panel.ControlPanel._restart(fake)
    # the elevated copy must not find this copy's mutex
    assert order == ["release", "shell", "close", "destroy"]


def test_close_releases_session_before_destroy():
    order = []
    fake = NS(session=NS(close=lambda: order.append("close")), root=NS(destroy=lambda: order.append("destroy")))
    panel.ControlPanel.close(fake)
    assert order == ["close", "destroy"]


def test_exp_tracker_checkbox_reaches_session():
    got = []
    fake = NS(session=NS(set_exp_tracker=got.append), exp_var=NS(get=lambda: False))
    panel.ControlPanel._exp_tracker(fake)
    assert got == [False]


def test_initial_checkbox_values_from_settings():
    from mapletool.config import Settings
    assert panel.initial_checks(Settings(show_all=True, exp_tracker=False)) == {
        "show_all": True, "exp_tracker": False, "block_alt_shift": True}


def test_alt_shift_checkbox_blocks_restores_and_saves():
    from types import SimpleNamespace as NS
    saved, calls = [], []
    blocker = NS(block=lambda: calls.append("block"), restore=lambda: calls.append("restore"))
    for value in (True, False):
        fake = NS(session=NS(set_block_alt_shift=saved.append), alt_shift_var=NS(get=lambda v=value: v),
                  blocker=blocker)
        panel.ControlPanel._block_alt_shift(fake)
    assert calls == ["block", "restore"] and saved == [True, False]


def test_close_restores_the_shortcut_first():
    from types import SimpleNamespace as NS
    calls = []
    fake = NS(blocker=NS(restore=lambda: calls.append("restore")), session=NS(close=lambda: calls.append("session")),
              root=NS(destroy=lambda: calls.append("root")))
    panel.ControlPanel.close(fake)
    assert calls == ["restore", "session", "root"]


def test_restore_shortcut_never_raises():
    from types import SimpleNamespace as NS
    def boom():
        raise OSError("registry")
    panel.restore_shortcut(NS(restore=boom))
    panel.restore_shortcut(None)
