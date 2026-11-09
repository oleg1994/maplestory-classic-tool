from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path

import requests
from rapidfuzz import fuzz, process

from . import __version__
from .model import Item, Match

API = "https://meowdb.com/msclassic/api"
USER_AGENT = f"maplestory-classic-tool/{__version__}"
FUZZY_CUTOFF = 90
MAX_PAGES = 200
CACHE_VERSION = 2  # v2 adds rechargeable/slot_max; older caches are not trusted (treated as empty)
RECHARGEABLE_SUBTYPES = {"Throwing Star", "Bullet"}


def normalize(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", name.lower()).strip()


def fold_il(text: str) -> str:
    """The game font draws I and l identically; fold both to "l"."""
    return text.replace("I", "l")


def _key(name: str) -> str:
    return normalize(fold_il(name))


name_key = _key  # public: the normalized, I/l-folded form names are matched (and listed) by


def _value(it: Item) -> tuple:
    """What decides an item's NPC value: candidates that agree on this are interchangeable for a verdict."""
    return (it.npc_price, it.rechargeable, it.slot_max)


def _prefix_display(text: str, cands: list) -> str:
    """The OCR text of a truncated name, cleaned: whitespace runs collapsed, and I/l glitches (the game
    font draws them alike) taken from the candidates' common prefix where it agrees. The trailing
    ".."/"..."/"…" is kept, so the panel never claims one tier of several."""
    text = " ".join(text.split())
    body = text.rstrip(".…")
    tail = text[len(body):]
    common = os.path.commonprefix([c.name for c in cands])
    out = list(body)
    for i in range(min(len(body), len(common))):
        if fold_il(body[i]) != fold_il(common[i]):
            break
        out[i] = common[i]
    return "".join(out) + tail


class Catalog:
    def __init__(self, items: list[Item], fetched_at: float):
        self.items = list(items)
        self.fetched_at = fetched_at
        self._keys: dict[str, Item] = {}
        for it in self.items:
            key = _key(it.name)
            cur = self._keys.get(key)
            if cur is None or it.npc_price < cur.npc_price:
                self._keys[key] = it

    def __len__(self) -> int:
        return len(self.items)

    @classmethod
    def load(cls, path: Path) -> "Catalog":
        try:
            raw = json.loads(Path(path).read_text(encoding="utf-8"))
            if raw.get("version") != CACHE_VERSION:
                return cls([], 0)
            items = [Item(int(i), str(n), int(p), bool(r), max(1, int(m))) for i, n, p, r, m in raw["items"]]
            return cls(items, float(raw["fetched_at"]))
        except (OSError, ValueError, KeyError, TypeError):
            return cls([], 0)

    def save(self, path: Path) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "version": CACHE_VERSION,
            "fetched_at": self.fetched_at,
            "items": [[i.id, i.name, i.npc_price, i.rechargeable, i.slot_max] for i in self.items],
        }
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps(data), encoding="utf-8")
        os.replace(tmp, path)

    def is_stale(self, max_age_days: int, now: float) -> bool:
        return now - self.fetched_at > max_age_days * 86400

    def lookup(self, text: str) -> Match | None:
        stripped = text.rstrip()
        if stripped.endswith(("..", "…")):
            # Truncated by the game UI: prefix match only, never fuzzy.
            norm = _key(stripped.rstrip(".…"))
            if not norm:
                return None
            cands = [it for k, it in self._keys.items() if k.startswith(norm)]
            if cands and len({_value(c) for c in cands}) == 1:
                # the lowest id stands in for the (shared) value; with several candidates the name shown
                # is the OCR text, never one confident tier
                display = _prefix_display(stripped, cands) if len(cands) > 1 else ""
                return Match(min(cands, key=lambda c: c.id), 100.0, "prefix", len(cands), display)
            return None
        norm = _key(text)
        if not norm:
            return None
        hit = self._keys.get(norm)
        if hit is not None:
            return Match(hit, 100.0, "exact")
        found = process.extract(norm, list(self._keys), scorer=fuzz.ratio,
                                score_cutoff=FUZZY_CUTOFF, limit=None)
        if not found:
            return None
        if len({_value(self._keys[k]) for k, _, _ in found}) != 1:
            return None  # ambiguous: near-tie across different NPC values
        key, score, _ = max(found, key=lambda f: f[1])
        return Match(self._keys[key], float(score), "fuzzy")


def _stats(stats) -> dict:
    """MeowDB `stats`: a dict or a JSON string; {} when unusable."""
    if isinstance(stats, str):
        try:
            stats = json.loads(stats)
        except ValueError:
            return {}
    return stats if isinstance(stats, dict) else {}


def _int(value, default: int) -> int:
    try:
        return int(value or default)
    except (TypeError, ValueError):
        return default


def _item(e: dict) -> Item:
    stats = _stats(e.get("stats"))
    rechargeable = e.get("subtype") in RECHARGEABLE_SUBTYPES or "unitPrice" in stats
    return Item(int(e["id"]), str(e["name"]), _int(stats.get("price"), 0), rechargeable,
                max(1, _int(stats.get("slotMax"), 1)))


def fetch_all(session: requests.Session) -> list[Item]:
    items: list[Item] = []
    for page in range(1, MAX_PAGES + 1):
        r = session.get(f"{API}/items", params={"limit": 500, "page": page},
                        timeout=15, headers={"User-Agent": USER_AGENT})
        r.raise_for_status()
        body = r.json()
        if not isinstance(body, dict) or not isinstance(body.get("items"), list):
            raise ValueError("unexpected MeowDB response")
        if not body["items"]:
            break
        for e in body["items"]:
            items.append(_item(e))
        if not body.get("hasMore"):
            break
    else:
        raise ValueError(f"MeowDB pagination exceeded {MAX_PAGES} pages")
    if not items:
        raise ValueError("MeowDB returned no items")
    return items


def sync(path: Path, session: requests.Session) -> Catalog:
    try:
        cat = Catalog(fetch_all(session), time.time())
        cat.save(path)
        return cat
    except Exception:
        return Catalog.load(path)
