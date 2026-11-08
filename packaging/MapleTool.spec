# PyInstaller spec: single-file windowed build. Run via build.bat from the repo root.
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

root = Path(SPECPATH).parent

hidden = [
    "winrt.windows.media.ocr",
    "winrt.windows.graphics.imaging",
    "winrt.windows.storage.streams",
    "winrt.windows.globalization",
    "winrt.windows.foundation",
    "winrt.windows.foundation.collections",
    "dxcam",
    "comtypes",
    "mss",
    "mss.windows",
]
for pkg in ("dxcam", "comtypes", "winrt"):
    hidden += collect_submodules(pkg)

datas = [(str(p), "mapletool/assets") for p in (root / "mapletool" / "assets").glob("*")]
datas += collect_data_files("dxcam")
datas += collect_data_files("comtypes")

a = Analysis(
    [str(root / "packaging" / "mapletool_app.py")],
    pathex=[str(root)],
    datas=datas,
    hiddenimports=sorted(set(hidden)),
    excludes=["pytest", "matplotlib", "scipy", "pandas", "IPython"],
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="MapleTool",
    icon=str(root / "mapletool" / "assets" / "app.ico"),
    console=False,
    upx=False,
    runtime_tmpdir=None,
)
