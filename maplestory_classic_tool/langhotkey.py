"""Turn off Windows' Alt+Shift input-language shortcut while MapleStory Classic Tool is open (MapleStory players press
Alt+Shift a lot). It never looks at the game: it changes the user's own hotkey setting
(HKCU\\Keyboard Layout\\Toggle, the "Input language hot keys" dialog) and asks Windows to reload it.
Win+Space still switches languages.

The original values are written to a backup file before anything changes. restore() puts them back
(on Off of the checkbox and on exit); recover() at startup restores a backup left by a crash.

Toggle values: "1" Alt+Shift (Windows' default when absent), "2" Ctrl+Shift, "3" none, "4" grave accent.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

log = logging.getLogger("maplestory_classic_tool")

KEY = r"Keyboard Layout\Toggle"
LANGUAGE = ("Language Hotkey", "Hotkey")  # "Hotkey" is the legacy copy of "Language Hotkey"
LAYOUT = "Layout Hotkey"
ALT_SHIFT, NONE = "1", "3"
SPI_SETLANGTOGGLE = 0x005B


class Registry:
    """HKCU\\Keyboard Layout\\Toggle string values, and the reload Windows needs after a change."""

    def get(self, name: str):
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, KEY) as k:
                return str(winreg.QueryValueEx(k, name)[0])
        except FileNotFoundError:
            return None

    def set(self, name: str, value: str) -> None:
        import winreg
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, KEY, 0, winreg.KEY_SET_VALUE) as k:
            winreg.SetValueEx(k, name, 0, winreg.REG_SZ, value)

    def delete(self, name: str) -> None:
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, KEY, 0, winreg.KEY_SET_VALUE) as k:
                winreg.DeleteValue(k, name)
        except FileNotFoundError:
            pass

    def apply(self) -> None:
        import ctypes
        ctypes.windll.user32.SystemParametersInfoW(SPI_SETLANGTOGGLE, 0, None, 0)


class LangHotkeyBlocker:
    def __init__(self, backup: Path, reg=None):
        self.backup = Path(backup)
        self.reg = reg if reg is not None else Registry()

    @property
    def blocked(self) -> bool:
        return self.backup.exists()

    def _changes(self) -> dict:
        """{value name: new value} turning Alt+Shift off; empty when Alt+Shift is not in use."""
        out = {}
        lang = self.reg.get(LANGUAGE[0])
        if lang is None:
            lang = self.reg.get(LANGUAGE[1])
        if lang in (None, ALT_SHIFT):  # absent: Windows' default, Alt+Shift
            out.update({name: NONE for name in LANGUAGE})
        if self.reg.get(LAYOUT) == ALT_SHIFT:
            out[LAYOUT] = NONE
        return out

    def block(self) -> bool:
        """Turn the Alt+Shift language/layout shortcut off; True when something was changed."""
        if self.blocked:
            return False
        changes = self._changes()
        if not changes:
            return False
        original = {name: self.reg.get(name) for name in changes}
        self.backup.parent.mkdir(parents=True, exist_ok=True)
        self.backup.write_text(json.dumps(original), encoding="utf-8")  # first: a crash can always undo
        for name, value in changes.items():
            self.reg.set(name, value)
        self.reg.apply()
        log.info("Alt+Shift language shortcut turned off while MapleStory Classic Tool is open")
        return True

    def restore(self) -> bool:
        """Put the original values back; True when there was something to restore."""
        if not self.blocked:
            return False
        try:
            original = json.loads(self.backup.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            log.warning("unreadable language hotkey backup %s; Windows defaults restored", self.backup)
            original = {name: None for name in (*LANGUAGE, LAYOUT)}
        for name, value in original.items():
            if value is None:
                self.reg.delete(name)
            else:
                self.reg.set(name, str(value))
        self.reg.apply()
        self.backup.unlink(missing_ok=True)
        log.info("Alt+Shift language shortcut restored")
        return True

    def recover(self) -> bool:
        """At startup: undo a block left behind by a crash or a killed process."""
        if self.blocked:
            log.warning("restoring the language shortcut left off by a previous run")
            return self.restore()
        return False
