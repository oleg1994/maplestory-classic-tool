from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

log = logging.getLogger("mapletool")


DATA_DIR_NAME = "MapleTool"
OLD_DATA_DIR_NAME = "fmoverlay"  # before the project was renamed


def data_dir() -> Path:
    base = Path(os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local"))
    d = base / DATA_DIR_NAME
    old = base / OLD_DATA_DIR_NAME
    if not d.exists() and old.is_dir():
        try:
            old.rename(d)  # keep settings, the catalog cache and a pending language-hotkey backup
        except OSError:
            return old  # in use (an old copy still running): keep using it this time
    d.mkdir(parents=True, exist_ok=True)
    return d


@dataclass
class Settings:
    window_title: str = "MapleStory"
    poll_hz: int = 10
    min_profit: int = 1
    show_all: bool = False
    exp_tracker: bool = True  # EXP/hour + time-to-level box above the status bar
    block_alt_shift: bool = True  # Windows' Alt+Shift language shortcut off while MapleTool is open
    catalog_max_age_days: int = 7
    hotkey_scan: str = "F8"
    hotkey_clear: str = "F9"
    # client size "WxH" -> confirmed base [w, h] (the game's render resolution for that size)
    base_sizes: dict = field(default_factory=lambda: {"3840x2160": [1366, 768], "2560x1600": [1228, 768]})


def save_settings(s: Settings, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(asdict(s), indent=2), encoding="utf-8")


_TRUE = {"true", "1", "yes"}
_FALSE = {"false", "0", "no"}


_SIZE_KEY = re.compile(r"^\d+x\d+$")


def _coerce_base_sizes(value) -> dict:
    """{"WxH": [w, h]}; malformed entries are dropped (with a warning), a non-object raises."""
    if not isinstance(value, dict):
        raise ValueError(f"not an object: {value!r}")
    out = {}
    for k, v in value.items():
        try:
            if not (isinstance(k, str) and _SIZE_KEY.match(k)):
                raise ValueError
            if not isinstance(v, (list, tuple)) or len(v) != 2:
                raise ValueError
            wh = [_coerce(x, 0) for x in v]
            if min(wh) <= 0:
                raise ValueError
        except (TypeError, ValueError):
            log.warning("settings.json: dropping bad base_sizes entry %r: %r", k, v)
            continue
        out[k] = wh
    return out


def _coerce(value, default):
    """Coerce a JSON value to the type of the dataclass default; raise ValueError if it can't."""
    if isinstance(default, dict):
        return _coerce_base_sizes(value)
    if isinstance(default, bool):
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)) and value in (0, 1):
            return bool(value)
        if isinstance(value, str):
            v = value.strip().lower()
            if v in _TRUE:
                return True
            if v in _FALSE:
                return False
        raise ValueError(f"not a bool: {value!r}")
    if isinstance(default, int):
        if isinstance(value, bool):
            raise ValueError("bool is not an int")
        if isinstance(value, float):
            if not value.is_integer():
                raise ValueError(f"not an integer: {value!r}")
            return int(value)
        if isinstance(value, (int, str)):
            return int(value)
        raise ValueError(f"not an int: {value!r}")
    if isinstance(default, str):
        if value is None or isinstance(value, (dict, list)):
            raise ValueError(f"not a string: {value!r}")
        return str(value)
    return value


def load_settings(path: Path) -> Settings:
    if not path.exists():
        s = Settings()
        save_settings(s, path)
        return s
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError("settings must be an object")
    except (OSError, ValueError):
        return Settings()
    defaults = Settings()
    out = {}
    for f in fields(Settings):
        if f.name not in raw:
            continue
        default = getattr(defaults, f.name)
        try:
            out[f.name] = _coerce(raw[f.name], default)
        except (TypeError, ValueError):
            log.warning("settings.json: bad value %r for %r; using default %r",
                        raw[f.name], f.name, default)
    return Settings(**out)
