# FM NPC-Flip Overlay Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Windows overlay that reads MapleStory Classic player-shop windows from the screen and labels every listing an NPC would buy for more than its asking price.

**Architecture:** Python package `mapletool`. Pipeline per frame: capture game client → locate shop anchor (cached) → slice fixed row grid → read each text box with an exact-match glyph-template OCR (Windows OCR fallback for unknown glyphs) → parse qty/price → match name against a cached MeowDB catalog → compute NPC profit → draw labels on a click-through topmost tkinter overlay. Every stage is a pure function over numpy arrays, so the whole pipeline runs offline on fixture screenshots.

**Tech Stack:** Python ≥3.10, numpy, opencv-python, rapidfuzz, requests, dxcam (mss fallback), winrt Windows.Media.Ocr, tkinter, ctypes (Win32), pytest.

**Spec:** `docs/superpowers/specs/2026-11-06-fm-npc-overlay-design.md`

## Global Constraints

- Screen reading only: no memory reads, packet capture, input injection, or writes into the game folder.
- Python ≥3.10 (user machine has 3.10.5). No syntax newer than 3.10.
- Perf budget: changed shop read+evaluate < 5 ms CPU; unchanged frame < 1 ms (capture excluded).
- MeowDB base URL `https://meowdb.com/msclassic/api`; bulk list `GET /items?limit=500&page=N` until `hasMore` is false; NPC unit price = `int(stats["price"])`, where `stats` may be a dict or a JSON string; missing → 0.
- HTTP User-Agent `mapletool/0.1`. Never send user email or identity anywhere.
- User data dir `%LOCALAPPDATA%\mapletool` (settings.json, catalog.json, log.txt). Catalog refresh age: 7 days.
- Flag rule: `profit = npc_price * qty - price`; flag when `profit >= min_profit` (default 1). Uncertain reads and unmatched names are never flagged.
- Name match: exact normalized → truncated prefix → `rapidfuzz.fuzz.ratio` cutoff 90.
- Hotkeys: F8 force rescan, F9 clear. Poll rate 10 Hz.
- Process must call `SetProcessDpiAwareness(2)` before any Win32 geometry call.

## Review Focus

1. **Long names truncated by the shop UI** (e.g. ends with `..`/`...`) → must match by prefix only when the prefix is unambiguous, else unknown. Test: `test_lookup_truncated_prefix_*` in Task 1.
2. **Misread or unknown glyph in a price** (`?` in text) → listing uncertain → never shown as profit. Test: `test_evaluate_uncertain_never_profit` in Task 6, `test_parse_price_unknown_glyph` in Task 4.
3. **Shop closed / window moved / partly off-screen** → labels cleared on the next scan, no stale labels at old position. Test: `test_scan_no_shop_clears` in Task 6.
4. **MeowDB unreachable or returns garbage** → keep using cached catalog; with no cache, app runs and shows status "No price data", every row unknown. Test: `test_sync_failure_keeps_cache`, `test_load_missing_returns_empty` in Task 1.
5. **Same name, several catalog ids with different NPC prices** → use the minimum NPC price (conservative; never over-promise profit). Test: `test_duplicate_names_use_min_price` in Task 1.

## Amendment A (2026-11-06, from real captures — binding on Tasks 3–7)

- The game renders at a **base resolution of 1366×768** and the client (3840×2160 on the user's machine) is a non-integer upscale (×2.811) with smoothing. At capture resolution the same glyph differs up to 8% of pixels between rows. After `cv2.resize(gray, (1366, 768), interpolation=cv2.INTER_AREA)` identical text is pixel-identical (verified: two `1 for 129 mesos` rows → 0 px mismatch at thresholds 100–140).
- Therefore `anchor.png`, every `layout.json` coordinate, and all glyph templates are in **base-resolution pixels**. `Layout` gains `base_size: tuple[int,int]` (value `[1366, 768]`). New function in `layout.py`: `to_base(gray: np.ndarray, base_size: tuple[int,int]) -> np.ndarray` — `INTER_AREA` resize; returns the input object unchanged when already that size. Fixture PNGs stay at full capture resolution; code and tests load them as gray and call `to_base` first.
- Task 7: `Grabber.grab` returns base-resolution gray (BGR→gray + resize measured 2.5 ms at 4K; counted as capture, outside the scan budget). `to_screen` takes a `scale: float` (= client width / base width) and maps `(x * scale + client_x, y * scale + client_y)`, rounded.
- Price line verified: `1 for 3,000 mesos`. The number drawn on the item icon (e.g. 200) is **stock left**, not bundle size — qty comes from the text only.
- Base row pitch ≈ 40 px (112.5 px at 4K); rows alternate 112/113 px at capture res, so all row geometry must be computed at base resolution.
- Fixture labels: a row value may also be the string `"occluded"` (tooltip or cursor covers it). Such rows are excluded from ink checks, calibration and expected listings.
- Fixtures present: `shop1.png`–`shop7.png` (Free Market player shops; `shop3.png` has a hover tooltip), `noshop_npc.png` (NPC store window — locator must return None), `noshop_fm.png` (Free Market, no window).
- The game runs elevated: global hotkeys only reach a process that is also elevated. Auto-scan does not depend on hotkeys.

---

## File Structure

```
pyproject.toml                 # deps, pytest config (markers: perf, live)
README.md                      # usage, admin note, calibration how-to
mapletool/
  __init__.py
  __main__.py                  # CLI: live mode, --image offline mode
  model.py                     # shared dataclasses
  config.py                    # Settings, data dir, load/save
  catalog.py                   # MeowDB sync, cache, name lookup
  glyphs.py                    # binarize, column split, GlyphSet read/save/load
  calibrate.py                 # CLI: build glyphs.json from labelled fixtures; draw layout debug
  layout.py                    # Layout dataclass + load
  detect.py                    # ShopLocator (anchor match + cache)
  shop.py                      # row slicing, price parsing, Listing building
  fallback_ocr.py              # Windows.Media.Ocr wrapper
  evaluate.py                  # Verdict from Listing + Catalog
  scanner.py                   # frame → ScanResult, caches, annotations
  capture.py                   # find game client rect, grab gray frame
  overlay.py                   # tkinter click-through overlay
  hotkeys.py                   # RegisterHotKey thread
  assets/anchor.png, assets/layout.json, assets/glyphs.json   # produced in Task 3
tests/
  fixtures/shops/<name>.png + <name>.json                     # user screenshots + labels (Task 3)
  test_catalog.py test_glyphs.py test_calibrate.py test_shop.py
  test_fallback_ocr.py test_evaluate.py test_scanner.py test_live.py
```

---

### Task 1: Scaffold, models, settings, catalog

**Files:**
- Create: `pyproject.toml`, `.gitignore`, `mapletool/__init__.py`, `mapletool/model.py`, `mapletool/config.py`, `mapletool/catalog.py`
- Test: `tests/test_catalog.py`

**Interfaces:**
- Produces (`model.py`, all `@dataclass(frozen=True)`):
  - `Item(id: int, name: str, npc_price: int)`
  - `Match(item: Item, score: float, how: Literal["exact","prefix","fuzzy"])`
  - `Listing(row: int, name: str, qty: int | None, price: int | None, uncertain: bool)`
  - `Verdict(listing: Listing, item: Item | None, npc_total: int | None, profit: int | None, status: Literal["profit","no_profit","unknown"])`
  - `Annotation(row: int, x: int, y: int, text: str, status: str)` — x,y in game-client pixel coords
- Produces (`config.py`): `data_dir() -> Path` (creates `%LOCALAPPDATA%\mapletool`); `Settings` dataclass with `window_title="MapleStory"`, `poll_hz=10`, `min_profit=1`, `show_all=False`, `catalog_max_age_days=7`, `hotkey_scan="F8"`, `hotkey_clear="F9"`; `load_settings(path: Path) -> Settings` (writes defaults if missing, ignores unknown keys); `save_settings(s: Settings, path: Path) -> None`.
- Produces (`catalog.py`): `API = "https://meowdb.com/msclassic/api"`; `normalize(name: str) -> str`; `class Catalog` with `Catalog(items: list[Item], fetched_at: float)`, `Catalog.load(path: Path) -> Catalog` (empty catalog, `fetched_at=0`, if file missing/corrupt), `save(path: Path) -> None`, `is_stale(max_age_days: int, now: float) -> bool`, `lookup(text: str) -> Match | None`, `__len__`; `fetch_all(session: requests.Session) -> list[Item]`; `sync(path: Path, session: requests.Session) -> Catalog` (fetch, save, return; on any exception return `Catalog.load(path)`).

- [ ] **Step 1: Scaffold.** `git init`; `pyproject.toml` (project `mapletool`, `requires-python = ">=3.10"`, deps: `numpy`, `opencv-python`, `rapidfuzz`, `requests`, `dxcam`, `mss`, `winrt-runtime`, `winrt-Windows.Media.Ocr`, `winrt-Windows.Graphics.Imaging`, `winrt-Windows.Storage.Streams`, `winrt-Windows.Globalization`; dev: `pytest`; `[tool.pytest.ini_options] markers = ["perf: timing budget", "live: needs running game"]`, `addopts = "-m 'not live'"`). `.gitignore`: `__pycache__/`, `.venv/`, `*.egg-info/`, `dbg*.png`. Create venv and `pip install -e .[dev]`.

- [ ] **Step 2: Write failing tests in `tests/test_catalog.py`**

```python
def test_normalize():
    assert normalize("  Red  Potion! ") == "red potion"
    assert normalize("One-Handed Axe Attack Scroll: Chaos") == "one handed axe attack scroll chaos"

def test_lookup_exact():
    c = Catalog([Item(270, "Red Potion", 5)], 0)
    assert c.lookup("Red Potion") == Match(Item(270, "Red Potion", 5), 100.0, "exact")

def test_lookup_fuzzy_one_char_off():
    c = Catalog([Item(270, "Red Potion", 5), Item(271, "Orange Potion", 10)], 0)
    m = c.lookup("Red Pot1on")
    assert m.item.id == 270 and m.how == "fuzzy"

def test_lookup_below_cutoff_none():
    c = Catalog([Item(270, "Red Potion", 5)], 0)
    assert c.lookup("Blue Ribbon") is None

def test_lookup_truncated_prefix_unique():
    c = Catalog([Item(1, "Dark Scroll for Gloves for ATT 30%", 50), Item(2, "Red Potion", 5)], 0)
    m = c.lookup("Dark Scroll for Glo...")
    assert m.item.id == 1 and m.how == "prefix"

def test_lookup_truncated_prefix_ambiguous_none():
    c = Catalog([Item(1, "Scroll for Gloves for ATT 10%", 50), Item(2, "Scroll for Gloves for ATT 60%", 70)], 0)
    assert c.lookup("Scroll for Gloves for..") is None

def test_duplicate_names_use_min_price():
    c = Catalog([Item(1, "Snail Shell", 9), Item(2, "Snail Shell", 4)], 0)
    assert c.lookup("Snail Shell").item.npc_price == 4

def test_fetch_all_paginates_and_parses_string_stats(fake_session):
    # page1: hasMore True, stats as JSON string; page2: hasMore False, stats as dict; one item without price
    items = fetch_all(fake_session)
    assert [(i.id, i.npc_price) for i in items] == [(1, 1), (270, 5), (9, 0)]

def test_save_load_roundtrip(tmp_path): ...  # same items, same fetched_at
def test_load_missing_returns_empty(tmp_path):
    assert len(Catalog.load(tmp_path / "nope.json")) == 0
def test_sync_failure_keeps_cache(tmp_path, failing_session):
    Catalog([Item(270, "Red Potion", 5)], 123.0).save(tmp_path / "c.json")
    assert sync(tmp_path / "c.json", failing_session).lookup("Red Potion").item.id == 270
def test_is_stale():
    assert Catalog([], 0).is_stale(7, now=8 * 86400)
    assert not Catalog([], 0).is_stale(7, now=6 * 86400)
```

`fake_session` / `failing_session`: tiny fakes in `tests/conftest.py` exposing `.get(url, params=..., timeout=...)` returning objects with `.json()` / `.raise_for_status()`; `failing_session.get` raises `requests.ConnectionError`. No network in tests.

- [ ] **Step 3: Run** `pytest tests/test_catalog.py -v` → FAIL (import errors).

- [ ] **Step 4: Implement** `model.py`, `config.py`, `catalog.py` per Interfaces. `normalize`: lowercase, `re.sub(r"[^a-z0-9]+", " ", ...)`, strip. Lookup keys: dict `normalized → Item` built once, duplicates keep min `npc_price`. Truncation detected by text ending in `..` (strip trailing dots, normalize, `startswith` over keys; unique item or all candidates share one npc_price → match). Fuzzy: `rapidfuzz.process.extractOne(norm, keys, scorer=fuzz.ratio, score_cutoff=90)`. `fetch_all`: `GET {API}/items` with `params={"limit": 500, "page": n}`, `timeout=15`, header UA. Cache JSON: `{"fetched_at": float, "items": [[id, name, npc_price], ...]}`.

- [ ] **Step 5: Run** `pytest tests/test_catalog.py -v` → all PASS. Then live smoke (not a test): `python -c "import requests;from mapletool.catalog import *;print(len(fetch_all(requests.Session())))"` → prints several thousand items, no error.

- [ ] **Step 6: Commit** `git add -A && git commit -m "feat: scaffold, models, settings, MeowDB catalog"`

---

### Task 2: Glyph-template OCR engine

**Files:**
- Create: `mapletool/glyphs.py`
- Test: `tests/test_glyphs.py`

**Interfaces:**
- Produces:
  - `binarize(gray: np.ndarray, ink_max_gray: int) -> np.ndarray` — bool array, True where `gray < ink_max_gray` (dark text on light panel).
  - `column_runs(ink: np.ndarray) -> list[tuple[int, int]]` — `[x0, x1)` spans of columns containing any ink.
  - `glyph_key(ink: np.ndarray) -> bytes` — `bytes([h, w]) + np.packbits(ink).tobytes()`; caller passes full box height so vertical position is part of the key.
  - `ReadResult(text: str, unknown: int)` dataclass.
  - `class GlyphSet`: `GlyphSet(glyphs: dict[str, list[np.ndarray]], space_gap: int, tolerance: float = 0.06)`; `add(char: str, ink: np.ndarray)`; `read(ink: np.ndarray) -> ReadResult`; `save(path: Path)`; `GlyphSet.load(path: Path) -> GlyphSet`. JSON format: `{"space_gap": int, "tolerance": float, "glyphs": [{"char": c, "h": h, "w": w, "bits": base64(packbits)}]}`.

`read` algorithm (not determined by signature — implement exactly):
```
runs = column_runs(ink)
for each run (x0, x1), with gap = x0 - prev_x1:
    if prev exists and gap >= space_gap: emit " "
    seg = ink[:, x0:x1]
    if glyph_key(seg) in exact: emit char; continue
    pos = 0
    while pos < seg.width:                       # touching glyphs: greedy split
        for w in widths_desc (<= seg.width - pos):
            if key(seg[:, pos:pos+w]) in exact: emit; pos += w; break
        else:
            best = nearest same-width template by Hamming over the whole remaining seg,
                   accepted if mismatched_pixels / (h*w) <= tolerance
            if best: emit best.char; pos = seg.width
            else: emit "?"; unknown += 1; pos = seg.width
        skip empty columns after pos
```

- [ ] **Step 1: Write failing tests** (synthetic glyphs drawn as small bool arrays in the test; helper `render(chars, gap=1, space=4)` concatenates them).

```python
def test_binarize_threshold():
    assert binarize(np.array([[0, 99, 100, 255]], np.uint8), 100).tolist() == [[True, True, False, False]]
def test_column_runs():
    ink = np.array([[1,1,0,0,1,0,1]], bool)
    assert column_runs(ink) == [(0,2),(4,5),(6,7)]
def test_read_exact_word_with_space():
    gs = make_set("abc")           # 3 distinct 5x3 glyphs, space_gap=3
    assert gs.read(render("ab c")) == ReadResult("ab c", 0)
def test_read_touching_glyphs_split():
    assert make_set("abc").read(render("abc", gap=0)).text == "abc"
def test_read_hamming_tolerance_accepts_one_flipped_pixel():   # 1/15 px off with tolerance 0.07
def test_read_unknown_glyph_marks_question():
    r = make_set("ab").read(render("aXb"))
    assert r.text == "a?b" and r.unknown == 1
def test_save_load_roundtrip(tmp_path):
def test_read_speed_line_under_200us():   # @pytest.mark.perf; 20-char line, median of 200 runs
```

- [ ] **Step 2: Run** `pytest tests/test_glyphs.py -v` → FAIL.
- [ ] **Step 3: Implement** per Interfaces + algorithm. Keep `exact: dict[bytes, str]` and `by_width: dict[int, list[(np.ndarray, str)]]` built in `__init__`/`add`.
- [ ] **Step 4: Run** `pytest tests/test_glyphs.py -v` → PASS (incl. perf).
- [ ] **Step 5: Commit** `git commit -am "feat: glyph-template OCR engine"` (add new files first).

---

### Task 3: Fixtures, layout, calibration

Needs the user. **Blocking step:** ask the user for 3–5 full-client screenshots (PNG, game's native resolution, no scaling) of open player shops: varied items, ≥1 row with qty > 1, ≥1 sold-out (grey) row, ≥1 long truncated name, one with a hovered/selected row. Plus one screenshot with no shop open.

**Files:**
- Create: `tests/fixtures/shops/*.png` + `*.json`, `mapletool/layout.py`, `mapletool/calibrate.py`, `mapletool/assets/anchor.png`, `mapletool/assets/layout.json`, `mapletool/assets/glyphs.json`
- Test: `tests/test_calibrate.py`

**Interfaces:**
- Consumes: `binarize`, `GlyphSet`, `column_runs` (Task 2).
- Produces:
  - Fixture label `<name>.json`: `{"shop": true, "rows": [{"name": str, "qty": int, "price": int} | null, ...]}` — one entry per visible grid row top-to-bottom, `null` for empty or sold-out rows. No-shop screenshot: `{"shop": false, "rows": []}`.
  - `Layout` dataclass (`layout.py`): `anchor_to_grid: tuple[int,int]`, `rows: int`, `row_pitch: int`, `name_box: tuple[int,int,int,int]`, `price_box: tuple[int,int,int,int]` (x, y, w, h relative to row origin), `ink_max_gray: int`, `label_dx: int`, `price_format: str`; `load_layout(path: Path) -> Layout`; method `Layout.row_origin(anchor_xy: tuple[int,int], row: int) -> tuple[int,int]` = `(ax + anchor_to_grid[0], ay + anchor_to_grid[1] + row * row_pitch)`.
  - `price_text(qty: int, price: int, fmt: str) -> str` in `calibrate.py`; default fmt `"{qty} for {price:,} mesos"` (reference implementation's format). If screenshots show another wording, change `price_format` in layout.json and Task 4's `PRICE_RE` to match.
  - CLI: `python -m mapletool.calibrate build FIXTURE_DIR --out mapletool/assets/glyphs.json [--holdout NAME]` → prints `trained=<n> glyphs, holdout lines=<n>, wrong=<n>, unknown=<n>`; exits 1 if `wrong > 0`.
  - CLI: `python -m mapletool.calibrate draw SHOT.png --out dbg.png` → draws anchor match, row boxes, name/price boxes.

- [ ] **Step 1: Label fixtures.** Save screenshots to `tests/fixtures/shops/`; write each `.json` from what is visible (ask user to confirm the labels).
- [ ] **Step 2: Measure geometry.** Crop the shop header into `assets/anchor.png` (≥ 60×12 px, static pixels only — no item text). Measure grid offsets/pitch/boxes and darkest grey of sold text vs. ink; set `ink_max_gray` between them (reference: ink ≈ 0, sold ≥ 160 → default 100). Write `assets/layout.json`.
- [ ] **Step 3: Write failing tests**

```python
def test_layout_boxes_cover_text():          # for every fixture row != null, name and price boxes contain ink; null rows contain none
def test_build_from_fixtures_no_wrong_on_holdout():   # build with holdout=<last fixture>; wrong == 0
def test_glyph_alignment_skips_mismatched_lines():    # line whose run count != len(label without spaces) is skipped, reported, not trained
def test_space_gap_between_intra_and_inter_word_gaps():  # max intra-word gap < space_gap <= min inter-word gap
```

- [ ] **Step 4: Run** `pytest tests/test_calibrate.py -v` → FAIL.
- [ ] **Step 5: Implement** `layout.py` and `calibrate.py`. Build: for each labelled row, binarize name/price boxes, take `column_runs`, compare against label chars with spaces removed; equal count → `GlyphSet.add` each; else skip + report. `space_gap` = `max_intra + 1` clamped to `<= min_inter`. Holdout evaluation: `wrong` = chars that read as a different non-`?` char.
- [ ] **Step 6: Run** `python -m mapletool.calibrate draw tests/fixtures/shops/<first>.png --out dbg.png`, open `dbg.png`, confirm boxes sit on text. Then `python -m mapletool.calibrate build tests/fixtures/shops --out mapletool/assets/glyphs.json --holdout <last>` → `wrong=0`. Then rebuild without `--holdout` for the shipped set. Run `pytest tests/test_calibrate.py -v` → PASS.
- [ ] **Step 7: Gate.** If `wrong > 0` cannot be fixed by tuning `ink_max_gray`/`tolerance` (font is anti-aliased and unstable), stop and report to the user: plan falls back to Windows OCR as primary (Task 5 module used for every row, still cached by `glyph_key`).
- [ ] **Step 8: Commit** `git add -A && git commit -m "feat: shop fixtures, layout, glyph calibration"`

---

### Task 4: Shop locator and row parsing

**Files:**
- Create: `mapletool/detect.py`, `mapletool/shop.py`
- Test: `tests/test_shop.py`

**Interfaces:**
- Consumes: `Layout`, `load_layout`, `Layout.row_origin` (Task 3); `binarize` (Task 2); `Listing` (Task 1).
- Produces:
  - `class ShopLocator(anchor: np.ndarray, min_score: float = 0.92)`; `locate(gray: np.ndarray) -> tuple[int,int] | None`. Cached position re-verified by `np.abs(region - anchor).mean() <= 4` (int16 math); else `cv2.matchTemplate(TM_CCOEFF_NORMED)` full search; below `min_score` → clear cache, return None. `reset()` clears cache.
  - `RowInk(row: int, name: np.ndarray, price: np.ndarray)` dataclass (bool inks).
  - `row_inks(gray: np.ndarray, anchor_xy: tuple[int,int], layout: Layout) -> list[RowInk]` — skips rows where both boxes have no ink (empty/sold); skips rows whose boxes fall outside the frame.
  - `PRICE_RE = re.compile(r"^([\d,]+) for ([\d,]+) mesos$")` (must match `layout.price_format`).
  - `parse_price(text: str) -> tuple[int | None, int | None]` — `(qty, price)`, `(None, None)` on no match or `?` present.
  - `to_listing(row: int, name_text: str, price_text: str, unknown: int) -> Listing` — `uncertain = unknown > 0 or qty is None or price is None or not name_text.strip()`.

- [ ] **Step 1: Write failing tests**

```python
def test_locate_on_each_shop_fixture():      # returns a position; draw-check consistent with layout
def test_locate_no_shop_fixture_none():
def test_locate_uses_cache_fast():            # perf: second call on same frame < 0.2 ms median
def test_locate_after_window_moved():         # np.roll frame by (37, 21); new position == old + offset
def test_row_inks_skips_sold_and_empty():     # count == number of non-null rows in label
def test_parse_price():
    assert parse_price("100 for 5,000 mesos") == (100, 5000)
    assert parse_price("1 for 2,147,483,647 mesos") == (1, 2147483647)
def test_parse_price_unknown_glyph():
    assert parse_price("1 for 5,?00 mesos") == (None, None)
def test_rows_read_match_labels():            # every fixture: glyphs.read on row_inks → to_listing equals label rows (name, qty, price), none uncertain
```

- [ ] **Step 2: Run** `pytest tests/test_shop.py -v` → FAIL.
- [ ] **Step 3: Implement** `detect.py`, `shop.py` per Interfaces.
- [ ] **Step 4: Run** `pytest tests/test_shop.py -v` → PASS.
- [ ] **Step 5: Commit** `git add -A && git commit -m "feat: shop locator and row parsing"`

---

### Task 5: Windows OCR fallback

**Files:**
- Create: `mapletool/fallback_ocr.py`
- Test: `tests/test_fallback_ocr.py`

**Interfaces:**
- Produces: `class WindowsOcr` with `WindowsOcr.create() -> WindowsOcr | None` (None if winrt import fails or no OCR language installed); `read(gray: np.ndarray) -> str` — upscales 3× with `cv2.INTER_NEAREST`, pads 8 px white, converts to `SoftwareBitmap` (Gray8), runs `OcrEngine.try_create_from_user_profile_languages().recognize_async` synchronously, returns lines joined by space, stripped.

- [ ] **Step 1: Write failing tests** (skip module if `WindowsOcr.create()` is None)

```python
def test_reads_fixture_name_box():   # crop first labelled row name box from a fixture; rapidfuzz ratio(read, label) >= 90
def test_reads_fixture_price_box():  # parse_price(read) == (qty, price) for at least 4 of 5 fixture rows sampled
def test_blank_crop_returns_empty():
```

- [ ] **Step 2: Run** `pytest tests/test_fallback_ocr.py -v` → FAIL.
- [ ] **Step 3: Implement** per Interfaces.
- [ ] **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** `git add -A && git commit -m "feat: Windows OCR fallback"`

---

### Task 6: Evaluation, scanner pipeline, offline image mode

**Files:**
- Create: `mapletool/evaluate.py`, `mapletool/scanner.py`, `mapletool/__main__.py` (offline part)
- Test: `tests/test_evaluate.py`, `tests/test_scanner.py`

**Interfaces:**
- Consumes: `Catalog.lookup` (T1), `GlyphSet.read`, `glyph_key` (T2), `Layout` (T3), `ShopLocator`, `row_inks`, `to_listing` (T4), `WindowsOcr` (T5), `Settings`.
- Produces:
  - `evaluate(listing: Listing, catalog: Catalog, min_profit: int) -> Verdict` — unknown if uncertain or no match; `no_profit` if `npc_price <= 0`; else `npc_total = npc_price * qty`, `profit = npc_total - price`, `profit` status iff `profit >= min_profit`.
  - `ScanResult(anchor: tuple[int,int] | None, verdicts: list[Verdict], changed: bool)`.
  - `class Scanner(locator, layout, glyphs, catalog, settings, fallback: WindowsOcr | None)`; `scan(gray: np.ndarray) -> ScanResult`; `reset() -> None` (clears all caches + locator).
    - Change detection: key = `(anchor, hash(grid_region.tobytes()))`; same as last → previous verdicts, `changed=False`.
    - Text cache: `dict[bytes, str]` keyed by `glyph_key(ink)`, cleared when > 4096 entries. Fallback called only when `GlyphSet.read(...).unknown > 0` and fallback exists; fallback text counts as `unknown=0` for name, and for price only if `parse_price` succeeds.
  - `annotations(result: ScanResult, layout: Layout, show_all: bool) -> list[Annotation]` — profit: `f"NPC +{profit:,}"`; with `show_all`: no_profit `f"NPC {npc_total:,}"`, unknown `"NPC ?"`. Position: `layout.row_origin(anchor, row)` + `(layout.label_dx, 0)`.
  - CLI: `python -m mapletool --image SHOT.png --out OUT.png [--show-all]` → draws annotations with `cv2.putText` (green profit, grey others) and prints one line per verdict.

- [ ] **Step 1: Write failing tests**

```python
# test_evaluate.py  (catalog = Catalog([Item(270,"Red Potion",5), Item(9,"Event Item",0)], 0))
def test_profit():          # Listing(0,"Red Potion",100,400,False) → profit 100, status "profit"
def test_break_even_below_min_profit():   # price 500, min_profit 1 → "no_profit", profit 0
def test_evaluate_uncertain_never_profit():   # uncertain=True, price 1 → "unknown"
def test_unmatched_name_unknown():
def test_zero_npc_price_no_profit():
# test_scanner.py (stub catalog built from fixture labels, npc_price chosen so row 0 profits and row 1 does not)
def test_scan_fixture_verdicts():
def test_scan_unchanged_frame_not_changed():
def test_scan_no_shop_clears():       # scan shop fixture, then no-shop fixture → anchor None, verdicts []
def test_annotations_show_all_toggle():
def test_scan_cold_under_5ms():       # perf: reset text cache each run, locator cached; median of 50
def test_scan_unchanged_under_1ms():  # perf
def test_cli_image_mode(tmp_path):    # subprocess run; OUT.png exists; stdout has one line per non-null fixture row
```

- [ ] **Step 2: Run** `pytest tests/test_evaluate.py tests/test_scanner.py -v` → FAIL.
- [ ] **Step 3: Implement** `evaluate.py`, `scanner.py`, offline branch of `__main__.py` (argparse; live branch stub raises `NotImplementedError` until Task 7).
- [ ] **Step 4: Run** `pytest -v` → all PASS (whole suite).
- [ ] **Step 5: Commit** `git add -A && git commit -m "feat: evaluation, scanner pipeline, offline image mode"`

---

### Task 7: Live capture, overlay, hotkeys

**Files:**
- Create: `mapletool/capture.py`, `mapletool/overlay.py`, `mapletool/hotkeys.py`, `README.md`
- Modify: `mapletool/__main__.py` (live branch)
- Test: `tests/test_live.py`

**Interfaces:**
- Consumes: `Scanner`, `annotations` (T6); `Settings`, `load_settings`, `data_dir` (T1); `Catalog.load/sync/is_stale` (T1).
- Produces:
  - `capture.find_client_rect(title: str) -> tuple[int,int,int,int] | None` — screen `(x, y, w, h)` of client area via `FindWindowW`, `IsIconic` (minimized → None), `GetClientRect`, `ClientToScreen`.
  - `class Grabber(base_size: tuple[int,int])`; `grab(rect) -> np.ndarray` (uint8 gray at base resolution, via `layout.to_base`). dxcam `grab(region=...)`; dxcam returns None when frame unchanged → return last frame; dxcam init failure → mss.
  - `overlay.Label(x: int, y: int, text: str, color: str)`; `to_screen(anns: list[Annotation], client_xy: tuple[int,int], scale: float) -> list[Label]` (pure; color `#3ddc84` for status "profit", `#b0b0b0` otherwise); `class Overlay(root: tk.Tk)` with `show(labels)`, `clear()`, `status(text: str | None)`. Window: `overrideredirect(True)`, `-topmost`, `-transparentcolor` key color `#010101`, covers virtual screen; click-through via `SetWindowLongW(GWL_EXSTYLE, | WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE)`.
  - `hotkeys.HotkeyThread(bindings: dict[str, Callable[[], None]])` — daemon thread, `RegisterHotKey(None, id, 0, VK)` for `"F1".."F12"` (VK `0x70 + n - 1`), `GetMessageW` loop; registration failure logged, not fatal.
  - Live loop in `__main__`: `SetProcessDpiAwareness(2)` first; load settings + catalog; stale/empty catalog → `sync` in background thread, then swap; empty after sync → `overlay.status("No price data")`. Worker thread at `poll_hz`: rect None → push clear; else grab → `scanner.scan` → push labels when `changed` or anchor became None. F8 → `scanner.reset()`; F9 → clear + suppress until next `changed`. Tk main thread drains `queue.Queue` via `root.after(16, ...)`. Logging to `data_dir()/log.txt`.

- [ ] **Step 1: Write failing tests**

```python
def test_find_client_rect_missing_window_none():
    assert find_client_rect("no such window 7f3a") is None
def test_to_screen_offsets():
    assert to_screen([Annotation(0, 10, 20, "NPC +5", "profit")], (100, 200), 1.0) == [Label(110, 220, "NPC +5", "#3ddc84")]
    assert to_screen([Annotation(0, 100, 40, "NPC ?", "unknown")], (0, 0), 2.811) == [Label(281, 112, "NPC ?", "#b0b0b0")]
def test_hotkey_vk_codes():          # vk("F8") == 0x77, vk("F9") == 0x78
@pytest.mark.live
def test_live_end_to_end():          # game running with shop open: one scan returns anchor != None
```

- [ ] **Step 2: Run** `pytest tests/test_live.py -v` → FAIL.
- [ ] **Step 3: Implement** per Interfaces. README: install, `python -m mapletool`, run as administrator if the game runs elevated (hotkeys), re-calibration after a game UI patch (Task 3 CLIs), MeowDB disclaimer, "reads the screen only".
- [ ] **Step 4: Run** `pytest -v` → all PASS.
- [ ] **Step 5: Manual verify with the user in game:** open a player shop → labels appear beside profitable rows within ~0.2 s; scroll the shop → labels follow; move the game window → labels follow; close shop → labels vanish; F9 clears; F8 rescans; Task Manager shows overlay CPU < 3% idle with shop open. Then `pytest -m live -v` → PASS.
- [ ] **Step 6: Commit** `git add -A && git commit -m "feat: live capture, overlay, hotkeys"`
