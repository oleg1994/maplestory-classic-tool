# Changelog

All notable changes to MapleStory Classic Tool. Versions follow
[Semantic Versioning](https://semver.org); the release steps are in
[docs/RELEASING.md](docs/RELEASING.md).

Between releases, add each change under `## [Unreleased]` (create the heading if
it is missing). A release renames that heading to the new version and date.

## [0.1.0] - 2026-11-10

First release.

### Added
- **Price checker:** a list beside an open player shop with every item an NPC
  buys for more than its price, most profitable first, with item icons and a
  profit badge. Scroll once and the whole shop stays listed.
- **Show all rows** (on by default) also lists the other items with what the NPC
  pays. Items with no NPC price on MeowDB are left out.
- **EXP and meso tracker** over the status bar's LV panel: EXP per hour, time to
  the next level and mesos per hour.
- **Alt+Shift blocker:** turns off Windows' language-switch shortcut while the
  tool is open and puts it back on exit.
- Control window styled like a MapleStory window, with ON / OFF and the options.
- F8 / F9 hotkeys to rescan and clear.
- Reads the screen only: it never touches the game's memory, files or input.
