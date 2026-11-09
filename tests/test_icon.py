import struct

from maplestory_classic_tool.panel import icon_path


def test_app_icon_has_small_and_large_png_entries():
    data = icon_path().read_bytes()
    reserved, kind, count = struct.unpack_from("<HHH", data, 0)
    assert (reserved, kind) == (0, 1) and count >= 4
    sizes = set()
    for i in range(count):
        w, h, _, _, planes, bpp, size, offset = struct.unpack_from("<BBBBHHII", data, 6 + 16 * i)
        assert w == h and bpp == 32 and offset + size <= len(data)
        assert data[offset:offset + 8] == b"\x89PNG\r\n\x1a\n"
        sizes.add(w or 256)
    assert {16, 32, 48, 256} <= sizes


def test_spec_builds_maple_tool_with_the_icon():
    from pathlib import Path
    spec = (Path(__file__).resolve().parents[1] / "packaging" / "MapleStoryClassicTool.spec").read_text(encoding="utf-8")
    assert 'name="MapleStoryClassicTool"' in spec and "app.ico" in spec
