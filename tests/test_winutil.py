import os
import tkinter

import pytest

from maplestory_classic_tool import live, panel
from maplestory_classic_tool.winutil import MUTEX_NAME, SingleInstance


def _name():
    return rf"Local\MapleStoryClassicToolTest-{os.getpid()}"


def test_mutex_name():
    assert MUTEX_NAME == r"Local\MapleStoryClassicTool"


def test_second_acquire_fails_until_released():
    a, b = SingleInstance(_name()), SingleInstance(_name())
    try:
        assert a.acquire() and a.held
        assert a.acquire()  # re-acquire by the holder is a no-op
        assert not b.acquire() and not b.held
        a.release()
        assert b.acquire()
    finally:
        a.release()
        b.release()


class _Taken:
    def __init__(self, *a):
        pass

    def acquire(self):
        return False

    def release(self):
        pass


def _no_windows(monkeypatch):
    monkeypatch.setattr(live, "_setup_logging", lambda: None)
    monkeypatch.setattr(live, "set_dpi_aware", lambda: None)
    monkeypatch.setattr(tkinter, "Tk", lambda *a, **k: pytest.fail("a window was created"))


def test_gui_second_launch_shows_message_and_exits(monkeypatch):
    _no_windows(monkeypatch)
    shown = []
    monkeypatch.setattr(panel, "SingleInstance", _Taken)
    monkeypatch.setattr(panel, "notify_already_running", lambda: shown.append(1))
    assert panel.main() == 1
    assert shown == [1]


def test_headless_second_launch_logs_and_exits_1(monkeypatch, caplog):
    _no_windows(monkeypatch)
    monkeypatch.setattr(live, "SingleInstance", _Taken)
    with caplog.at_level("ERROR", logger="maplestory_classic_tool"):
        assert live.run_live() == 1
    assert "already running" in caplog.text


def test_second_process_cannot_acquire():
    import subprocess
    import sys
    a = SingleInstance(_name())
    assert a.acquire()
    code = ("import sys; from maplestory_classic_tool.winutil import SingleInstance; "
            "sys.exit(0 if SingleInstance(sys.argv[1]).acquire() else 3)")
    try:
        r = subprocess.run([sys.executable, "-c", code, _name()], timeout=60)
        assert r.returncode == 3
    finally:
        a.release()
    r = subprocess.run([sys.executable, "-c", code, _name()], timeout=60)
    assert r.returncode == 0
