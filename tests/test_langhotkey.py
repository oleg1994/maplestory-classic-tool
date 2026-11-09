import json

from maplestory_classic_tool.langhotkey import LangHotkeyBlocker


class FakeReg:
    def __init__(self, values=None):
        self.values = dict(values or {})
        self.applied = 0

    def get(self, name):
        return self.values.get(name)

    def set(self, name, value):
        self.values[name] = value

    def delete(self, name):
        self.values.pop(name, None)

    def apply(self):
        self.applied += 1


def test_default_alt_shift_blocked_then_restored_exactly(tmp_path):
    reg = FakeReg()  # nothing set: Windows' default Alt+Shift
    b = LangHotkeyBlocker(tmp_path / "bk.json", reg)
    assert b.block() and b.blocked
    assert reg.values == {"Language Hotkey": "3", "Hotkey": "3"} and reg.applied == 1
    assert json.loads((tmp_path / "bk.json").read_text()) == {"Language Hotkey": None, "Hotkey": None}
    assert b.restore() and not b.blocked
    assert reg.values == {} and reg.applied == 2  # absent again, as before


def test_explicit_values_restored(tmp_path):
    reg = FakeReg({"Language Hotkey": "1", "Hotkey": "1", "Layout Hotkey": "2"})
    b = LangHotkeyBlocker(tmp_path / "bk.json", reg)
    b.block()
    assert reg.values == {"Language Hotkey": "3", "Hotkey": "3", "Layout Hotkey": "2"}  # Ctrl+Shift kept
    b.restore()
    assert reg.values == {"Language Hotkey": "1", "Hotkey": "1", "Layout Hotkey": "2"}


def test_layout_alt_shift_also_blocked(tmp_path):
    reg = FakeReg({"Language Hotkey": "2", "Hotkey": "2", "Layout Hotkey": "1"})
    b = LangHotkeyBlocker(tmp_path / "bk.json", reg)
    assert b.block()
    assert reg.values == {"Language Hotkey": "2", "Hotkey": "2", "Layout Hotkey": "3"}
    b.restore()
    assert reg.values["Layout Hotkey"] == "1"


def test_nothing_to_do_without_alt_shift(tmp_path):
    reg = FakeReg({"Language Hotkey": "2", "Hotkey": "2"})
    b = LangHotkeyBlocker(tmp_path / "bk.json", reg)
    assert not b.block() and not b.blocked and reg.applied == 0


def test_block_twice_keeps_the_first_backup(tmp_path):
    reg = FakeReg()
    b = LangHotkeyBlocker(tmp_path / "bk.json", reg)
    b.block()
    assert not b.block()  # already off: the backup still holds the real original
    b.restore()
    assert reg.values == {}


def test_recover_after_a_crash(tmp_path):
    reg = FakeReg()
    LangHotkeyBlocker(tmp_path / "bk.json", reg).block()  # the process dies here
    fresh = LangHotkeyBlocker(tmp_path / "bk.json", reg)
    assert fresh.recover() and reg.values == {} and not fresh.blocked
    assert not fresh.recover()


def test_unreadable_backup_restores_windows_defaults(tmp_path):
    reg = FakeReg({"Language Hotkey": "3", "Hotkey": "3"})
    (tmp_path / "bk.json").write_text("{broken")
    b = LangHotkeyBlocker(tmp_path / "bk.json", reg)
    assert b.restore() and reg.values == {}


def test_restore_without_block_does_nothing(tmp_path):
    reg = FakeReg({"Language Hotkey": "1"})
    assert not LangHotkeyBlocker(tmp_path / "bk.json", reg).restore() and reg.applied == 0
