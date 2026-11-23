"""Open asks on John's work board (http://127.0.0.1:7788/api/asks), read only, written as keyed `ask.state` states.

One thing per ask ("board ask <id>") with one keyed value, e.g. "open · decide · since 2026-11-16 · CRM-3151 Lob webhook signing ...": title only (never the
body), capped. The value carries the creation date, not an age, so a re-read of an unchanged ask only confirms. An ask that gets done gets its final value
once ("done"); one the board no longer lists is closed ("no longer observed"), so nothing about it stands as current.
"""
from __future__ import annotations

import json
import os
import urllib.request
from datetime import datetime, timezone
from typing import Callable, Optional

from ..store import Evidence, Store
from .common import close_unobserved, observe_keyed

WRITER = "observer:board-asks"
PARENT = "board asks"
PREFIX = "board ask "
MAX_OPEN = 40          # open asks written per read (newest first)
TITLE_CHARS = 110


def fetch_asks(base: Optional[str] = None, timeout: float = 5.0) -> list[dict]:
    base = base or os.environ.get("BOARD_URL", "http://127.0.0.1:7788")
    with urllib.request.urlopen(base + "/api/asks", timeout=timeout) as r:
        data = json.loads(r.read().decode("utf-8") or "[]")
    return data if isinstance(data, list) else data.get("items", [])


def is_done(a: dict) -> bool:
    return bool(a.get("doneByJohn") or a.get("doneAt"))


def ask_text(a: dict, state: str) -> str:
    since = (a.get("createdAt") or "")[:10] or "?"
    title = " ".join((a.get("title") or "").split())[:TITLE_CHARS]
    return f"{state} · {a.get('kind') or 'ask'} · since {since} · {title}"


def observe_board_asks(store: Store, asks: list[dict], *, clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc)) -> dict:
    """Open asks (newest MAX_OPEN) are written; a known ask that was done or is no longer listed gets its final value once. Returns {"open": n, "changed": [...]}."""
    out = {"open": 0, "changed": []}
    seen = set()
    opened = sorted((a for a in asks if a.get("id") and not is_done(a)), key=lambda a: a.get("createdAt") or "", reverse=True)[:MAX_OPEN]
    for a in asks:
        if a.get("id"):
            seen.add(a["id"])
    todo = [(a, "open") for a in opened] + [(a, "done") for a in asks if a.get("id") and is_done(a) and store.find(PREFIX + a["id"])]
    for a, state in todo:
        thing = PREFIX + a["id"]
        text = ask_text(a, state)
        ev = Evidence("tool", f"board /api/asks#{a['id']}", json.dumps({k: a.get(k) for k in ("kind", "createdAt", "updatedAt", "doneAt", "prUrl")})[:300])
        r = observe_keyed(store, writer=WRITER, thing=thing, parent=PARENT, observations={"ask.state": text}, evidence=ev,
                          handle=f"curl -s http://127.0.0.1:7788/api/asks  (id {a['id']})", as_of=None)
        if state == "open":
            out["open"] += 1
        out["changed"] += [f"{a['id']}:{k}" for k in r["changed"]]
    # asks the board no longer lists (removed, or done and cleared): their value is closed, so nothing stands as current for them
    for name in close_unobserved(store, writer=WRITER, seen={PREFIX + i for i in seen}, prefix=PREFIX):
        out["changed"].append(f"{name[len(PREFIX):]}:closed")
    return out
