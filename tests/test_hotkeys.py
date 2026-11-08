import threading

from mapletool import hotkeys


def test_immediate_start_stop_leaves_no_thread(monkeypatch):
    # F23/F24 only: never register the real F8/F9 in tests.
    monkeypatch.setattr(hotkeys, "vk", lambda name: {"F23": 0x86, "F24": 0x87}[name])
    for _ in range(20):
        t = hotkeys.HotkeyThread({"F23": lambda: None, "F24": lambda: None})
        t.start()
        t.stop()
        assert not t.is_alive()
    assert [x for x in threading.enumerate() if x.name == "hotkeys"] == []
