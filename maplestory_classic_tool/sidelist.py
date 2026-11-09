"""Side list: rows accumulated while one shop stays open, drawn as one panel beside the shop window."""
from __future__ import annotations

import base64
import hashlib
from dataclasses import dataclass, field, replace

import cv2

from maplestory_classic_tool import draw
from maplestory_classic_tool.catalog import name_key

PROFIT = "#3ddc84"  # summary-line colours (Panel.lines) and the overlay status text
OTHER = "#b0b0b0"

# Player-shop window colours, sampled from the fixtures at base resolution (shop2). None may be the
# overlay's transparent key colour (#010101).
SHOP_EDGE = "#5d6670"  # dark outline around the window
SHOP_FRAME = "#96aabe"  # blue-grey frame band
SHOP_FRAME_HI = "#d4d8de"  # light line just inside the outline
SHOP_TITLE_BG = "#ffffff"  # title strip ("Stuff")
SHOP_TITLE_EDGE = "#dcdfe2"
SHOP_TITLE_TEXT = "#0f0f0f"
SHOP_LIST_BG = "#f5f6f7"  # the item list behind the rows
SHOP_LIST_EDGE = "#7891ad"  # the list's blue border
SHOP_ROW_BG = "#cdd9e3"  # row background
SHOP_ROW_SPLIT = "#f3f6f9"  # white line between a row's name and price lines
SHOP_ROW_LINE = "#acbbcc"  # separator between rows
SHOP_ICON_TILE = "#e1dfd5"  # cream item cell
SHOP_ICON_EDGE = "#c1c0b7"
SHOP_TEXT = "#1d1f21"  # row text (darkest sampled #0d0e0f; anti-aliased Segoe reads darker)
DEAL_BADGE = "#7fa639"  # the shop's SELLER INFO green / the status bar's accent
DEAL_BADGE_EDGE = "#4f6e1c"
NPC_BADGE = "#8795a4"  # blue-grey like the frame, darker for white text
NPC_BADGE_EDGE = "#5d6a78"
BADGE_TEXT = "#ffffff"

ABSENT_CLEAR_S = 1.0  # anchor gone longer than this -> the list is forgotten
MAX_ROWS = 20
MAX_IDS = 8  # identities remembered while the shop stays open (cursor over the title bar etc.)
MIN_SEEN = 2  # observes before a row is shown/counted (a single glitched frame never sticks)
MAX_ICON_VARIANTS = 8  # distinct icon bitmaps remembered per listing (cursor-overlapped variants)

# Panel geometry in base pixels (scaled like everything else by client_w / base_w).
PANEL_GAP = 8
PAD = 4  # outline + frame band around the title strip and the list
TITLE_H = 18  # white title strip (the shop's is 21)
TITLE_PAD = 6
TITLE_PX = 11
LIST_GAP = 3  # frame band between the title strip and the list
LIST_PAD = 2
ROW_PITCH = 40  # the shop's row pitch (icon cell 35 + gap)
ROW_PITCH_COMPACT = 37  # when the rows would not fit the game window at ROW_PITCH
ROW_TEXT_H = 30  # row height without icons
ROW_PITCH_TEXT = 34
ICON_GAP = 2  # between the icon cell and the row background
TEXT_PAD = 5
NAME_PX = 12
BADGE_PX = 10
BADGE_H = 15
BADGE_PAD = 5
BADGE_GAP = 10  # between the longest text and the badge column
BADGE_RADIUS = 3
FRAME_RADIUS = 4
MORE_H = 16
MORE_PX = 10
FONT_PX = 10  # reference size of the CHAR_W estimate
CHAR_W = 5  # average advance of Segoe UI at FONT_PX (estimate; the renderers measure the text)
MIN_W = 120
MAX_CHARS = 56  # summary line (Panel.lines)
NAME_MAX_CHARS = 32  # name line of a row; the price line is never clipped


@dataclass
class ListedRow:
    name: str  # catalog name (not the OCR text, which may carry cursor junk)
    qty: int | None
    price: int | None
    status: str  # "profit" | "no_profit" | "unknown"
    npc_total: int | None
    profit: int | None
    count: int  # max identical rows seen in a single frame
    order: int  # first-seen order (shop order while scrolling down)
    seen: int = 0  # number of separate observes that contained this key
    icon: object = field(default=None, compare=False, repr=False)  # most frequently seen BGR icon crop
    icon_key: bytes | None = None  # digest of `icon`
    icon_votes: dict = field(default_factory=dict, compare=False, repr=False)  # digest -> [count, crop]

    def vote_icon(self, crop) -> None:
        """Count one sighting of an icon bitmap; the most frequent wins (ties keep the earlier best).
        The cursor sprite only sometimes overlaps an icon, so the clean bitmap wins over time."""
        key = hashlib.blake2b(crop.tobytes(), digest_size=12, person=bytes(str(crop.shape), "ascii")[:16]).digest()
        votes = self.icon_votes
        entry = votes.get(key)
        if entry is None:
            if len(votes) >= MAX_ICON_VARIANTS:  # forget the rarest variant other than the best
                drop = min((k for k in votes if k != self.icon_key), key=lambda k: votes[k][0])
                del votes[drop]
            entry = votes[key] = [0, crop]
        entry[0] += 1
        if self.icon_key is None or (key != self.icon_key and entry[0] > votes[self.icon_key][0]):
            self.icon_key, self.icon = key, entry[1]


@dataclass
class _Shop:
    rows: dict = field(default_factory=dict)  # key -> ListedRow
    slots: dict = field(default_factory=dict)  # row index in the last observed frame -> key
    next_order: int = 0


class ShopListing:
    """Union of every row read while one shop stays open, keyed by (catalog item id, qty, price).

    Only rows matched to a catalog item with a known verdict are listed: unknown/unmatched rows are
    never listed or counted (also with show all), and OCR variants of one listing (cursor junk such as
    "Kumbi Throwing Stars II") collapse into one entry shown under the catalog name. A truncated name
    that fits several same-value tiers ("Two-handed Axe Attack S...") is keyed by its normalized OCR text
    and shown as read, so it never collapses into, or is shown as, one particular tier.

    observe() per frame with the shop's identity; absent() per frame without a shop. A new
    identity starts an empty list; an identity seen earlier while the shop stayed open gets its
    list back. Absence longer than ABSENT_CLEAR_S forgets everything.

    Stability: a key is shown and counted only once it was seen in `min_seen` separate observes,
    so a single glitched frame never becomes a sticky deal. When a row slot shows a different key,
    the key it showed before is dropped if it was seen fewer than `min_seen` times - unless that key
    is still visible elsewhere in the frame (scrolling moves rows between slots).
    """

    def __init__(self, min_seen: int = MIN_SEEN):
        self.min_seen = min_seen
        self._lists: dict = {}  # shop_id -> _Shop
        self.shop_id = None
        self._last_seen = None
        self.version = 0

    @property
    def _shop(self):
        return self._lists.get(self.shop_id) if self.shop_id is not None else None

    def _stable(self) -> list:
        shop = self._shop
        if shop is None:
            return []
        return [r for r in shop.rows.values() if r.seen >= self.min_seen]

    @property
    def rows(self) -> list:
        """Stable rows in shop order."""
        return sorted(self._stable(), key=lambda r: r.order)

    @property
    def item_count(self) -> int:
        return sum(r.count for r in self._stable())

    @property
    def deal_count(self) -> int:
        return sum(r.count for r in self._stable() if r.status == "profit")

    def _signature(self) -> tuple:
        return tuple(sorted((id(r), r.count, r.icon_key or b"") for r in self._stable()))

    def clear(self) -> None:
        if self._lists or self.shop_id is not None:
            self.version += 1
        self._lists = {}
        self.shop_id = None
        self._last_seen = None

    def observe(self, shop_id, verdicts, now: float, icons: dict | None = None) -> bool:
        """Merge one frame's verdicts; True when the visible (stable) list changed.

        icons: {frame row: BGR icon crop at base resolution} for this frame (optional); each listed row
        votes its crop into its entry's icon."""
        if shop_id is None:  # shop identity not visible: nothing safe to accumulate
            return False
        before = self._signature()
        self._last_seen = now
        if shop_id != self.shop_id:
            self.shop_id = shop_id
            if shop_id not in self._lists:
                if len(self._lists) >= MAX_IDS:
                    self._lists.pop(next(iter(self._lists)))
                self._lists[shop_id] = _Shop()
        shop = self._lists[shop_id]
        rows = shop.rows
        per_frame: dict = {}
        slots: dict = {}
        for v in verdicts:
            if v.item is None or v.status == "unknown":
                continue  # never listed: no catalog match or no trustworthy verdict
            li = v.listing
            display = getattr(v, "display", "")
            if display:  # truncated name fitting several same-value tiers: keyed and shown by the OCR text
                key, name = ("~" + name_key(display), li.qty, li.price), display
            else:
                key, name = (v.item.id, li.qty, li.price), v.item.name
            per_frame[key] = per_frame.get(key, 0) + 1
            slots[li.row] = key
            if key not in rows:
                rows[key] = ListedRow(name, li.qty, li.price, v.status, v.npc_total, v.profit,
                                      0, shop.next_order)
                shop.next_order += 1
        for slot, key in slots.items():
            prev = shop.slots.get(slot)
            if prev is None or prev == key or prev in per_frame or prev not in rows:
                continue
            if rows[prev].seen < self.min_seen:
                del rows[prev]
        shop.slots = slots
        for key, n in per_frame.items():
            r = rows[key]
            r.seen += 1
            r.count = max(r.count, n)
        if icons:
            for slot, key in slots.items():
                crop = icons.get(slot)
                if crop is not None and key in rows:
                    rows[key].vote_icon(crop)
        changed = self._signature() != before
        if changed:
            self.version += 1
        return changed

    def absent(self, now: float) -> bool:
        """No shop this frame; True when the list was just forgotten."""
        if self._last_seen is None or now - self._last_seen <= ABSENT_CLEAR_S:
            return False
        self.clear()
        return True


def row_text(r: ListedRow, max_chars: int | None = None) -> str:
    """"head  name  qty for price  (xN)"; when too long the NAME is clipped, never the numbers."""
    if r.status == "profit":
        head = f"+{r.profit:,}"
    elif r.status == "no_profit":
        head = f"NPC {r.npc_total:,}" if r.npc_total is not None else "NPC -"
    else:
        head = "?"
    tail = ""
    if r.qty is not None and r.price is not None:
        tail += f"  {r.qty} for {r.price:,}"
    if r.count > 1:
        tail += f"  (x{r.count})"
    name = r.name
    if max_chars is not None:
        room = max_chars - len(head) - 2 - len(tail)
        if len(name) > room:
            name = name[:max(room - 1, 0)] + "…" if room > 0 else ""
    text = f"{head}  {name}{tail}"
    if max_chars is not None and len(text) > max_chars:
        text = text[:max_chars - 1] + "…"
    return text


@dataclass(frozen=True)
class PanelRow:
    """One entry of the side list, drawn like a shop row: icon, name over the price line, NPC badge."""
    name: str  # clipped to NAME_MAX_CHARS (the price line is never clipped)
    detail: str  # "<qty> for <price> mesos" (+ "  (xN)")
    badge: str  # "+12,345" (deal) or "NPC 1,234"
    deal: bool
    text: str  # one-line summary "head  name  qty for price" (logs, tests)
    icon: object = field(default=None, compare=False, repr=False)  # BGR crop (base) / base64 PNG (screen)


@dataclass(frozen=True)
class Panel:
    """A side panel; base pixels from build_panel, screen pixels after panel_to_screen.

    Geometry constants are base pixels; a screen panel carries `scale` (client_w / base_w) and every
    metric is multiplied by it when drawn (panel_prims). `w` is an estimate: the renderer measures the
    text and grows the box away from the shop (leftwards when grow_left)."""
    x: int
    y: int
    w: int
    h: int
    title: str
    rows: tuple = ()  # PanelRow, ...
    more: str = ""  # "… and K more" when rows were capped
    scale: float = 1.0
    row_h: float = ROW_PITCH_TEXT  # row pitch (base px)
    icon_w: float = 0  # item icon cell (base px); 0 without icons
    grow_left: bool = False  # placed left of the shop: when the text is wider than w, extend leftwards

    @property
    def lines(self) -> tuple:
        """((summary text, colour), ...) per row, then the "more" line."""
        out = tuple((r.text, PROFIT if r.deal else OTHER) for r in self.rows)
        return out + (((self.more, OTHER),) if self.more else ())

    @property
    def icons(self) -> tuple:
        return tuple(r.icon for r in self.rows)

    @property
    def font_px(self) -> float:
        return NAME_PX * self.scale


def _clip(name: str, n: int) -> str:
    return name if len(name) <= n else name[:max(n - 1, 0)].rstrip() + "…"


def panel_row(r: ListedRow) -> PanelRow:
    if r.status == "profit":
        badge = f"+{r.profit:,}"
    elif r.npc_total is not None:
        badge = f"NPC {r.npc_total:,}"
    else:
        badge = "NPC -"
    detail = f"{r.qty} for {r.price:,} mesos" if r.qty is not None and r.price is not None else ""
    if r.count > 1:
        detail += f"  (x{r.count})"
    return PanelRow(_clip(r.name, NAME_MAX_CHARS), detail, badge, r.status == "profit", row_text(r, MAX_CHARS),
                    r.icon)


def panel_height(n_rows: int, pitch: float, more: bool) -> int:
    """Outer height (base px): frame + title strip, then the list area when it has anything."""
    h = 2 * PAD + TITLE_H
    if n_rows or more:
        h += LIST_GAP + 2 * LIST_PAD + n_rows * pitch + (MORE_H if more else 0)
    return round(h)


def _row_metrics(icon_w: float) -> tuple:
    """(row content height, pitch, compact pitch) in base px."""
    if icon_w:
        return icon_w, ROW_PITCH, ROW_PITCH_COMPACT
    return ROW_TEXT_H, ROW_PITCH_TEXT, ROW_PITCH_TEXT


def _est(text: str, px: float, bold: bool) -> float:
    """Width estimate (base px) used for placement before the renderer measures the real text."""
    return len(text) * CHAR_W * px / FONT_PX


def build_panel(listing: ShopListing, anchor, layout, show_all: bool, frame_w: int | None = None,
                frame_h: int | None = None) -> Panel:
    """Deals first (profit desc), then (show_all) the rest in shop order; at most MAX_ROWS rows. When
    they do not fit in `frame_h` (the game window height) the rows get the compact pitch, then the
    rest becomes "… and K more".

    Placed left of the shop window, or right of it when there is no room on the left; moved up when it
    would reach below the window.
    """
    rows = listing.rows
    deals = sorted((r for r in rows if r.status == "profit"), key=lambda r: (-(r.profit or 0), r.order))
    shown = deals + ([r for r in rows if r.status != "profit"] if show_all else [])
    box = getattr(layout, "icon_box", None)
    icon_w = box[2] if box else 0
    _, pitch, compact = _row_metrics(icon_w)
    n = min(len(shown), MAX_ROWS)
    if frame_h is not None and panel_height(n, pitch, n < len(shown)) > frame_h:
        pitch = compact
        while n > 0 and panel_height(n, pitch, n < len(shown)) > frame_h:
            n -= 1
    visible = tuple(panel_row(r) for r in shown[:n])
    more = f"… and {len(shown) - n} more" if len(shown) > n else ""
    title = f"{listing.item_count} items · {listing.deal_count} deals"
    h = panel_height(len(visible), pitch, bool(more))
    w = max(MIN_W, round(panel_width(Panel(0, 0, 0, h, title, visible, more, row_h=pitch, icon_w=icon_w), _est)))
    wx, wy, ww, _ = layout.window_rect(anchor)
    x = wx - PANEL_GAP - w
    grow_left = x >= 0
    if not grow_left:
        x = wx + ww + PANEL_GAP
        if frame_w is not None and x + w > frame_w:
            x = max(0, frame_w - w)
    y = max(0, wy)
    if frame_h is not None:
        y = max(0, min(y, frame_h - h))
    return Panel(x, y, w, h, title, visible, more, row_h=pitch, icon_w=icon_w, grow_left=grow_left)


def _badge_w(text: str, s: float, measure) -> float:
    return measure(text, BADGE_PX * s, True) + 2 * BADGE_PAD * s


def panel_width(p: Panel, measure) -> float:
    """Width the content needs, in the panel's pixels (measure works in the same pixels)."""
    s = p.scale
    title = 2 * (PAD + TITLE_PAD) * s + measure(p.title, TITLE_PX * s, True)
    if not p.rows and not p.more:
        return title
    text = max([measure(r.name, NAME_PX * s, False) for r in p.rows]
               + [measure(r.detail, NAME_PX * s, False) for r in p.rows] + [0])
    badge = max([_badge_w(r.badge, s, measure) for r in p.rows] + [0])
    icon = (p.icon_w + ICON_GAP) * s if p.icon_w else 0
    row = 2 * (PAD + LIST_PAD) * s + icon + 2 * TEXT_PAD * s + text + (BADGE_GAP * s + badge if badge else 0)
    more = 2 * (PAD + LIST_PAD + TEXT_PAD) * s + measure(p.more, MORE_PX * s, False) if p.more else 0
    return max(title, row, more)


def panel_prims(p: Panel, measure) -> tuple:
    """(primitives, x, w): the panel drawn like the shop window, in the panel's pixels.

    The width is the measured content width (at least MIN_W); the edge next to the shop stays where
    build_panel put it (the right edge when grow_left)."""
    s = p.scale
    w = round(max(panel_width(p, measure), MIN_W * s))
    x = p.x + p.w - w if p.grow_left else p.x
    y, h = p.y, p.h
    pad = PAD * s
    lw = max(1, round(s))  # 1 base px lines
    out = [draw.Rect(x, y, x + w, y + h, SHOP_FRAME, SHOP_EDGE, lw, FRAME_RADIUS * s),
           draw.Line(x + pad, y + lw, x + w - pad, y + lw, SHOP_FRAME_HI, lw)]
    tx0, tx1 = x + pad, x + w - pad
    ty0, ty1 = y + pad, y + pad + TITLE_H * s
    out.append(draw.Rect(tx0, ty0, tx1, ty1, SHOP_TITLE_BG, SHOP_TITLE_EDGE, lw))
    out.append(draw.Text(tx0 + TITLE_PAD * s, (ty0 + ty1) / 2, p.title, SHOP_TITLE_TEXT, TITLE_PX * s, True))
    if not p.rows and not p.more:
        return out, x, w
    ly0 = ty1 + LIST_GAP * s
    out.append(draw.Rect(tx0, ly0, tx1, y + h - pad, SHOP_LIST_BG, SHOP_LIST_EDGE, lw))
    rx0, rx1 = tx0 + LIST_PAD * s, tx1 - LIST_PAD * s
    rh = _row_metrics(p.icon_w)[0] * s
    pitch = p.row_h * s
    for i, r in enumerate(p.rows):
        top = ly0 + LIST_PAD * s + i * pitch
        bx0 = rx0
        if p.icon_w:
            iw = p.icon_w * s
            if r.icon is not None:  # the crop is the shop's own cream cell
                out.append(draw.Image(rx0, top, r.icon))
            else:
                out.append(draw.Rect(rx0, top, rx0 + iw, top + rh, SHOP_ICON_TILE, SHOP_ICON_EDGE, lw))
            bx0 = rx0 + iw + ICON_GAP * s
        out.append(draw.Rect(bx0, top, rx1, top + rh, SHOP_ROW_BG))
        mid = top + rh / 2
        out.append(draw.Line(bx0 + TEXT_PAD * s, round(mid), rx1 - TEXT_PAD * s, round(mid), SHOP_ROW_SPLIT, lw))
        out.append(draw.Text(bx0 + TEXT_PAD * s, top + rh * 0.27, r.name, SHOP_TEXT, NAME_PX * s))
        out.append(draw.Text(bx0 + TEXT_PAD * s, top + rh * 0.75, r.detail, SHOP_TEXT, NAME_PX * s))
        bw = _badge_w(r.badge, s, measure)
        bx1 = rx1 - TEXT_PAD * s
        bh = BADGE_H * s
        fill, edge = (DEAL_BADGE, DEAL_BADGE_EDGE) if r.deal else (NPC_BADGE, NPC_BADGE_EDGE)
        out.append(draw.Rect(bx1 - bw, mid - bh / 2, bx1, mid + bh / 2, fill, edge, lw, BADGE_RADIUS * s))
        out.append(draw.Text(bx1 - BADGE_PAD * s, mid, r.badge, BADGE_TEXT, BADGE_PX * s, True, "e"))
        if i < len(p.rows) - 1 or p.more:
            sep = round(top + rh + (pitch - rh) / 2)
            out.append(draw.Line(rx0, sep, rx1, sep, SHOP_ROW_LINE, lw))
    if p.more:
        top = ly0 + LIST_PAD * s + len(p.rows) * pitch
        out.append(draw.Text(rx0 + TEXT_PAD * s, top + MORE_H * s / 2, p.more, SHOP_TEXT, MORE_PX * s))
    return out, x, w


_PNG_CACHE: dict = {}
PNG_CACHE_MAX = 256


def icon_png_b64(crop, scale: float) -> str | None:
    """base64 PNG of a BGR crop scaled for the screen (what Tk's PhotoImage(data=...) takes)."""
    if crop is None:
        return None
    h, w = crop.shape[:2]
    size = (max(1, round(w * scale)), max(1, round(h * scale)))
    key = (hashlib.blake2b(crop.tobytes(), digest_size=12).digest(), crop.shape, size)
    hit = _PNG_CACHE.get(key)
    if hit is not None:
        return hit
    if size != (w, h):
        interp = cv2.INTER_AREA if size[0] < w else cv2.INTER_LINEAR
        crop = cv2.resize(crop, size, interpolation=interp)
    ok, buf = cv2.imencode(".png", crop)
    if not ok:
        return None
    if len(_PNG_CACHE) >= PNG_CACHE_MAX:
        _PNG_CACHE.clear()
    out = _PNG_CACHE[key] = base64.b64encode(buf.tobytes()).decode("ascii")
    return out


def panel_to_screen(p: Panel, client_xy, scale: float) -> Panel:
    cx, cy = client_xy
    rows = tuple(replace(r, icon=icon_png_b64(r.icon, scale)) for r in p.rows)
    return Panel(round(p.x * scale + cx), round(p.y * scale + cy), round(p.w * scale), round(p.h * scale),
                 p.title, rows, p.more, p.scale * scale, p.row_h, p.icon_w, p.grow_left)
