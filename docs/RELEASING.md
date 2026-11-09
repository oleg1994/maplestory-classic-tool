# Releasing

Each release is a git tag `vX.Y.Z` on `main` and a GitHub release with the exe
attached. The version number lives in one place:
`maplestory_classic_tool/__init__.py` (`__version__`). `pyproject.toml`, the
`--version` flag, `--selftest` and the price-list User-Agent all read it.

Commands below are for a Windows command prompt in the repository folder, with the
virtual environment from the README (`.venv`) and the
[GitHub CLI](https://cli.github.com) (`gh`) logged in.

## 1. Pick the version

[Semantic Versioning](https://semver.org), starting from the current
`__version__`:

| Change since the last release | Bump | Example |
|---|---|---|
| Only fixes | patch | 0.1.0 → 0.1.1 |
| New features or visible behaviour changes | minor | 0.1.1 → 0.2.0 |
| Settings or data folder no longer compatible | major | 0.2.0 → 1.0.0 |

Below, `X.Y.Z` is the new version.

## 2. Start from an up-to-date `main`

```bat
git checkout main
git pull
git status
```

`git status` must show no changes to tracked files.

## 3. Run all the tests

```bat
.venv\Scripts\python -m pytest -rs
```

Every test must pass and none may be skipped (`-rs` lists skips with their
reason). The tests marked `live` are left out by default ("deselected"); that's
expected.

If MapleStory is open, also run the tests that need the running game:

```bat
.venv\Scripts\python -m pytest -m live
```

## 4. Write what changed

In `CHANGELOG.md`, rename `## [Unreleased]` to `## [X.Y.Z] - YYYY-MM-DD` (today's
date) and check that it lists every user-visible change since the last release.
Group the entries under `### Added`, `### Changed`, `### Fixed` or `### Removed`,
and write them for players, not developers ("The side list no longer…", not
"Refactor sidelist.py").

To see what went in since the last release:

```bat
git log --oneline vLAST..HEAD
```

This text becomes the release description on GitHub (step 8).

## 5. Increase the version

In `maplestory_classic_tool/__init__.py`:

```python
__version__ = "X.Y.Z"
```

Then reinstall so the package metadata picks it up, and run the release tests
(they check the changelog has an entry for the new version):

```bat
.venv\Scripts\pip install -e .
.venv\Scripts\python -m pytest tests\test_release.py
```

## 6. Build and check the exe

```bat
build.bat
dist\MapleStoryClassicTool.exe --selftest
```

The selftest line must start with `selftest ok: version=X.Y.Z`. Then
double-click the exe and check it by hand with the game open:

- The control window opens; ON starts reading the screen.
- Opening a player shop shows the side list.
- The EXP box appears over the LV panel.
- Closing the window puts Alt+Shift back.

## 7. Commit, tag and push

Check that no screenshot added since the last release shows the author's
character name unblurred (see the screenshot note in the README), then:

```bat
git add maplestory_classic_tool\__init__.py CHANGELOG.md
git commit -m "Release vX.Y.Z"
git tag -a vX.Y.Z -m "vX.Y.Z"
git push origin main
git push origin vX.Y.Z
```

Only ever push `main` and the tag (never the local `private-history` branch).

## 8. Publish the GitHub release with the exe

```bat
.venv\Scripts\python tools\release_notes.py X.Y.Z > dist\notes.md
certutil -hashfile dist\MapleStoryClassicTool.exe SHA256
```

Add the SHA-256 line to the end of `dist\notes.md`
(`SHA-256 of MapleStoryClassicTool.exe: …`), then:

```bat
gh release create vX.Y.Z dist\MapleStoryClassicTool.exe --title "vX.Y.Z" --notes-file dist\notes.md
```

Keep the file name `MapleStoryClassicTool.exe`, without the version in it, so the
README's download link
(`https://github.com/oleg1994/maplestory-classic-tool/releases/latest/download/MapleStoryClassicTool.exe`)
always gets the newest exe.

## 9. Check the release

- Open the release page (`gh release view vX.Y.Z --web`): the description matches
  the changelog and the exe is attached.
- Download the exe from the README link, run it with `--selftest`, and check it
  reports `version=X.Y.Z`.

## If something went wrong

- **Found a problem before step 7:** fix it, commit, and start again from step 3.
- **Tag pushed, release not published yet:** fix, then move the tag
  (`git tag -d vX.Y.Z`, `git push origin :refs/tags/vX.Y.Z`) and redo steps 7–8.
- **Release already published:** don't change it. Fix the problem and release
  the next patch version.
