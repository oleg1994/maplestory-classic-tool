"""PyInstaller entry point: opens the MapleStory Classic Tool control panel (or runs --selftest)."""
import sys

from maplestory_classic_tool.__main__ import main

if __name__ == "__main__":
    # Double-click starts the GUI; `--selftest` etc. pass through.
    raise SystemExit(main(sys.argv[1:] or ["--gui"]))
