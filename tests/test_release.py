"""Release bookkeeping: one version number, a changelog entry for it, release notes taken from the changelog."""
import re
import subprocess
import sys
from pathlib import Path

import pytest

import maplestory_classic_tool
from maplestory_classic_tool.catalog import USER_AGENT

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import release_notes  # noqa: E402

SAMPLE = """# Changelog

Intro text.

## [Unreleased]

## [0.2.0] - 2026-11-10

### Changed
- Thing two.

## [0.1.0] - 2026-11-09

- Thing one.
"""


def test_version_is_semver():
    assert re.fullmatch(r"\d+\.\d+\.\d+", maplestory_classic_tool.__version__)


def test_pyproject_takes_the_version_from_the_package():
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert 'dynamic = ["version"]' in text
    assert 'version = {attr = "maplestory_classic_tool.__version__"}' in text
    assert not re.search(r'^version = "', text, re.M)


def test_user_agent_carries_the_version():
    assert USER_AGENT == f"maplestory-classic-tool/{maplestory_classic_tool.__version__}"


def test_changelog_has_an_entry_for_the_current_version():
    text = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    assert release_notes.section(text, maplestory_classic_tool.__version__).strip()


def test_section_returns_only_that_versions_body():
    assert release_notes.section(SAMPLE, "0.2.0").strip() == "### Changed\n- Thing two."
    assert release_notes.section(SAMPLE, "0.1.0").strip() == "- Thing one."


def test_section_missing_version_raises():
    with pytest.raises(KeyError):
        release_notes.section(SAMPLE, "9.9.9")


def test_version_flag_prints_the_version():
    out = subprocess.run([sys.executable, "-m", "maplestory_classic_tool", "--version"], capture_output=True,
                         text=True, cwd=ROOT, timeout=60)
    assert out.returncode == 0
    assert out.stdout.strip() == f"MapleStory Classic Tool {maplestory_classic_tool.__version__}"
