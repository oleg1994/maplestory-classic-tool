# FM NPC-Flip Overlay — Design

Date: 2026-11-06. Inspired by cozy-markets-dev/fm-overlay (MaplePrice), narrower scope.

## Goal

Windows overlay for **MapleStory Classic**. While a player shop (Free Market) window is open,
mark every listing whose asking price is **below what an NPC pays** for the same item and quantity,
and show the profit in mesos.

## Hard rules

- Screen reading only. No memory reading, no packet capture, no input injection, no file edits in the game folder.
- Speed is the primary requirement: a changed shop must be read and labelled in under 5 ms of CPU
  (excluding screen capture); an unchanged frame in under 1 ms.

## Decisions (from user)

| Topic | Decision |
|---|---|
| OCR | Custom glyph-template matcher on the game's fixed font. Calibrated once from user screenshots. Windows built-in OCR (Windows.Media.Ocr) is fallback for unknown glyphs only. |
| Trigger | Auto: poll ~10 Hz, rescan when shop region pixels change. F8 = force rescan, F9 = clear labels. |
| Game | MapleStory Classic, user's resolution/UI scale; user supplies 3–5 shop screenshots. |
| Flag rule | Flag when `npc_price × qty − asking_price ≥ min_profit` (default 1). Optional "show all" mode labels every row with NPC value. |

## Data

- NPC sell prices from MeowDB public API, base `https://meowdb.com/msclassic/api`.
  - Bulk: `GET /items?limit=500&page=N`, response `{items, page, hasMore, ...}`; loop until `hasMore` is false.
    (`total`/`totalPages` are unreliable; ignore them.)
  - Each item: `id`, `name`, `stats` (dict **or JSON-encoded string**), `stats.price` = NPC sell price per unit.
- Cached locally in `%LOCALAPPDATA%\mapletool\catalog.json`; refreshed when older than 7 days.
- MeowDB warns some values may still be closed-test values. Overlay is advisory.

## Shop reading

- Shop window located by template-matching an anchor crop (window header). Position cached; re-verified per frame cheaply.
- Listing grid: fixed rows (reference implementation: 6 visible), each with an item-name line and a
  price line of the form `<qty> for <price> mesos`; price is the total for that bundle.
- Sold-out rows are drawn grey and are skipped.
- Item name matched to catalog: exact normalized → truncated-prefix → fuzzy (cutoff 90). Ambiguous → unknown, never flagged.

## Out of scope

Free Market price history, alerts, Discord, sharing, equipment stat reading, multi-language, auto-update, installer.
