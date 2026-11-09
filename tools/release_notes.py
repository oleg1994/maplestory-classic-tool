"""Print one version's section of CHANGELOG.md, for `gh release create --notes-file`.

    python tools/release_notes.py 0.2.0 > notes.md
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

CHANGELOG = Path(__file__).resolve().parents[1] / "CHANGELOG.md"


def section(text: str, version: str) -> str:
    """Body under `## [version]` up to the next `## ` heading; KeyError when the version has no entry."""
    m = re.search(rf"^## \[{re.escape(version)}\][^\n]*\n(.*?)(?=^## |\Z)", text, re.M | re.S)
    if m is None:
        raise KeyError(f"CHANGELOG.md has no entry for {version}")
    return m.group(1).strip() + "\n"


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print(__doc__.strip(), file=sys.stderr)
        return 2
    try:
        sys.stdout.write(section(CHANGELOG.read_text(encoding="utf-8"), argv[0]))
    except KeyError as e:
        print(e.args[0], file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
