import os

import pytest
import requests

# CLI subprocess tests decode the child's stdout as UTF-8 ("·" in panel titles); make the child write
# UTF-8 whatever the calling shell sets (PowerShell exports PYTHONIOENCODING=utf-8, Git Bash nothing).
os.environ["PYTHONIOENCODING"] = "utf-8"


class _Resp:
    def __init__(self, body):
        self._body = body

    def json(self):
        return self._body

    def raise_for_status(self):
        pass


class FakeSession:
    def __init__(self, pages):
        self.pages = pages
        self.calls = []

    def get(self, url, params=None, timeout=None, headers=None):
        self.calls.append((url, params, headers))
        return _Resp(self.pages[params["page"] - 1])


class FailingSession:
    def get(self, *a, **k):
        raise requests.ConnectionError("offline")


@pytest.fixture
def fake_session():
    return FakeSession([
        {"items": [{"id": 1, "name": "A", "stats": '{"price": 1}'},
                   {"id": 270, "name": "Red Potion", "stats": {"price": 5}}],
         "hasMore": True},
        {"items": [{"id": 9, "name": "No Price", "stats": {}}], "hasMore": False},
    ])


@pytest.fixture
def failing_session():
    return FailingSession()


@pytest.fixture(scope="session")
def tk_session_root():
    """One Tk interpreter for the whole run: creating a fresh tk.Tk() per test intermittently fails on
    Windows ("Can't find a usable tk.tcl"). Tests build their windows as Toplevels of this root."""
    tk = pytest.importorskip("tkinter")
    try:
        r = tk.Tk()
    except tk.TclError as e:
        pytest.skip(f"no Tk display: {e}")
    r.withdraw()
    yield r
    r.destroy()


@pytest.fixture
def tk_window(tk_session_root):
    """A hidden Toplevel for one test, destroyed afterwards."""
    import tkinter as tk
    w = tk.Toplevel(tk_session_root)
    w.withdraw()
    yield w
    w.destroy()
