import json

from maplestory_classic_tool.config import Settings, load_settings, save_settings


def test_defaults_written_when_missing(tmp_path):
    p = tmp_path / "s.json"
    s = load_settings(p)
    assert s == Settings() and p.exists()


def test_unknown_keys_ignored_and_roundtrip(tmp_path):
    p = tmp_path / "s.json"
    p.write_text(json.dumps({"poll_hz": 5, "bogus": 1}))
    assert load_settings(p).poll_hz == 5
    save_settings(Settings(min_profit=9), p)
    assert load_settings(p).min_profit == 9


def _load(tmp_path, data):
    p = tmp_path / "s.json"
    p.write_text(json.dumps(data))
    return load_settings(p)


def test_numeric_string_coerced_to_int(tmp_path):
    s = _load(tmp_path, {"min_profit": "500", "poll_hz": 5.0})
    assert s.min_profit == 500 and s.poll_hz == 5


def test_bool_strings_coerced(tmp_path):
    assert _load(tmp_path, {"show_all": "false"}).show_all is False
    assert _load(tmp_path, {"show_all": "TRUE"}).show_all is True
    assert _load(tmp_path, {"show_all": 1}).show_all is True
    assert _load(tmp_path, {"show_all": "no"}).show_all is False
    assert _load(tmp_path, {"show_all": "maybe"}).show_all is True  # default


def test_show_all_on_by_default(tmp_path):
    assert Settings().show_all is True
    assert _load(tmp_path, {}).show_all is True


def test_garbage_int_uses_default_and_warns(tmp_path, caplog):
    with caplog.at_level("WARNING"):
        s = _load(tmp_path, {"min_profit": "lots", "poll_hz": None, "hotkey_scan": 5})
    assert s.min_profit == Settings().min_profit and s.poll_hz == Settings().poll_hz
    assert s.hotkey_scan == "5"
    assert "min_profit" in caplog.text and "poll_hz" in caplog.text


def test_base_sizes_default_and_roundtrip(tmp_path):
    assert Settings().base_sizes == {"3840x2160": [1366, 768], "2560x1600": [1228, 768]}
    p = tmp_path / "s.json"
    s = Settings()
    s.base_sizes = {**s.base_sizes, "1920x1200": [1228, 768]}
    save_settings(s, p)
    assert load_settings(p).base_sizes == {"3840x2160": [1366, 768], "2560x1600": [1228, 768],
                                           "1920x1200": [1228, 768]}


def test_base_sizes_coercion(tmp_path, caplog):
    s = _load(tmp_path, {"base_sizes": {"2560x1600": ["1280", 800.0], "bad": [1, 2],
                                        "1920x1080": [0, 5], "1366x768": "x"}})
    assert s.base_sizes == {"2560x1600": [1280, 800]}
    with caplog.at_level("WARNING"):
        s = _load(tmp_path, {"base_sizes": [1, 2]})
    assert s.base_sizes == Settings().base_sizes and "base_sizes" in caplog.text


def test_unknown_keys_still_ignored(tmp_path):
    assert _load(tmp_path, {"bogus": "x", "min_profit": 7}).min_profit == 7


def test_exp_tracker_default_on_and_roundtrip(tmp_path):
    assert Settings().exp_tracker is True
    p = tmp_path / "s.json"
    save_settings(Settings(exp_tracker=False), p)
    assert json.loads(p.read_text())["exp_tracker"] is False
    assert load_settings(p).exp_tracker is False
    assert _load(tmp_path, {"exp_tracker": "no"}).exp_tracker is False
    assert _load(tmp_path, {"exp_tracker": "garbage"}).exp_tracker is True


def test_block_alt_shift_default_on_and_roundtrip(tmp_path):
    assert Settings().block_alt_shift is True
    p = tmp_path / "s.json"
    save_settings(Settings(block_alt_shift=False), p)
    assert load_settings(p).block_alt_shift is False


def test_data_dir_created(tmp_path, monkeypatch):
    from maplestory_classic_tool.config import data_dir
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    assert data_dir() == tmp_path / "MapleStoryClassicTool" and (tmp_path / "MapleStoryClassicTool").is_dir()


def test_data_dir_moves_the_old_fmoverlay_folder(tmp_path, monkeypatch):
    from maplestory_classic_tool.config import data_dir
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    (tmp_path / "fmoverlay").mkdir()
    (tmp_path / "fmoverlay" / "settings.json").write_text("{}")
    d = data_dir()
    assert d == tmp_path / "MapleStoryClassicTool" and (d / "settings.json").read_text() == "{}"
    assert not (tmp_path / "fmoverlay").exists()


def test_data_dir_moves_the_maple_tool_folder_first(tmp_path, monkeypatch):
    from maplestory_classic_tool.config import data_dir
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    (tmp_path / "fmoverlay").mkdir()
    (tmp_path / "MapleTool").mkdir()
    (tmp_path / "MapleTool" / "settings.json").write_text('{"show_all": true}')
    d = data_dir()
    assert d == tmp_path / "MapleStoryClassicTool" and (d / "settings.json").read_text() == '{"show_all": true}'
    assert not (tmp_path / "MapleTool").exists() and (tmp_path / "fmoverlay").exists()


def test_data_dir_keeps_existing_new_folder(tmp_path, monkeypatch):
    from maplestory_classic_tool.config import data_dir
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    (tmp_path / "fmoverlay").mkdir()
    (tmp_path / "MapleStoryClassicTool").mkdir()
    assert data_dir() == tmp_path / "MapleStoryClassicTool" and (tmp_path / "fmoverlay").exists()
