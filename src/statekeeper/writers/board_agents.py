"""Agents registered on John's work board (http://127.0.0.1:7788/api/agents), read only, written as keyed `agent.status` states.

The transcript writer (agent_status.py) knows an agent only by its transcript id; the board knows what it is FOR (card or PR, role, worktree) and
whether it reported. This writer keeps one thing per board agent ("board agent <id>") with one keyed value, e.g.
"running · builder · card PR-3349 · beat 12m ago", so the what's-running roll-up can say what is in flight without the hand-written note.
Agents that ended (reported, done, cancelled) long ago are not read again; one that ends while observed gets its final value once.
"""
from __future__ import annotations

import json
import os
import urllib.request
from datetime import datetime, timezone
from typing import Callable, Optional

from ..store import Evidence, Store
from .common import close_unobserved, observe_keyed

WRITER = "observer:board-agents"
PARENT = "board agents"
ACTIVE = ("running", "waiting-ci", "waiting-human", "waiting-john", "stalled", "waiting")
RECENT_HOURS = 24


def fetch_agents(base: Optional[str] = None, timeout: float = 5.0) -> list[dict]:
    base = base or os.environ.get("BOARD_URL", "http://127.0.0.1:7788")
    with urllib.request.urlopen(base + "/api/agents", timeout=timeout) as r:
        data = json.loads(r.read().decode("utf-8") or "[]")
    return data if isinstance(data, list) else data.get("items", [])


def _ago(ts: Optional[str], now: datetime) -> str:
    try:
        t = datetime.fromisoformat((ts or "").replace("Z", "+00:00"))
    except ValueError:
        return "?"
    m = max(0, int((now - t).total_seconds() // 60))
    return f"{m}m ago" if m < 120 else f"{m // 60}h ago"


def status_text(a: dict, now: datetime) -> str:
    parts = [a.get("state") or "?", a.get("role") or a.get("kind") or "agent"]
    if a.get("card"):
        parts.append(f"card {a['card']}")
    parts.append(f"beat {_ago(a.get('heartbeatAt') or a.get('updatedAt'), now)}")
    return " · ".join(parts)


def observe_board_agents(store: Store, agents: list[dict], *, clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc)) -> dict:
    """Write the active (and recently ended, once) board agents. Returns {"agents": n, "changed": [...]}. The 'beat' age is not part of the value
    compared for change (it would change on every read): only state/role/card are; the age is refreshed in the text when those change."""
    now = clock()
    out = {"agents": 0, "changed": []}
    written: set[str] = set()
    for a in agents:
        aid = a.get("id") or a.get("name")
        if not aid:
            continue
        thing = f"board agent {aid}"
        state = (a.get("state") or "").lower()
        try:
            touched = datetime.fromisoformat((a.get("updatedAt") or a.get("heartbeatAt") or "").replace("Z", "+00:00"))
        except ValueError:
            continue
        recent = (now - touched).total_seconds() <= RECENT_HOURS * 3600
        known = store.find(thing)
        if not (state in ACTIVE and recent) and not (known and recent):
            continue                                            # an agent that ended long ago, or that the store never saw running
        text = status_text(a, now)
        if known:
            cur = [s for s in store.states_of(known.id) if s.state_key == "agent.status" and s.status == "active"]
            if cur and cur[0].text.split(" · beat ")[0] == text.split(" · beat ")[0]:
                text = cur[0].text                              # unchanged apart from the heartbeat age: confirm, do not add a new state
        ev = Evidence("tool", f"board /api/agents/{aid}", json.dumps({k: a.get(k) for k in ("state", "role", "card", "worktree", "heartbeatAt")})[:400])
        r = observe_keyed(store, writer=WRITER, thing=thing, parent=PARENT, observations={"agent.status": text}, evidence=ev,
                          handle=f"bash C:/repos/work-board/scripts/board.sh agent-get {aid}", as_of=None)
        out["agents"] += 1
        out["changed"] += [f"{aid}:{k}" for k in r["changed"]]
        written.add(thing)
    # a known agent the board no longer reports (removed, or ended more than RECENT_HOURS ago): its last value is closed, not kept as current
    for name in close_unobserved(store, writer=WRITER, seen=written, prefix="board agent "):
        out["changed"].append(f"{name[len('board agent '):]}:closed")
    return out
