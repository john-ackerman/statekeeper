"""Deterministic writer: what a background agent or session is doing, read from its transcript file (not from what it says about itself).

The status words are working (the transcript changed in the last two minutes), finished (the last entry is an end-of-turn answer), silent (the last
entry is a tool call and nothing was written for ten minutes: possibly stalled) and idle (anything else). The state text stays the same while the
status is the same, so last-changed means "it changed status" and last-verified means "we looked again". The status is a live state with a
short life: nothing else (the extractor, an agent's own report) should write one of these.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Callable

from ..store import Evidence, Store, now
from .common import observe_keyed

WRITER = "rule:agent-status"
WORKING_SECONDS = 120
SILENT_SECONDS = 600


def normalize(p: dict) -> dict[str, str]:
    task = f" on {p['task']}" if p.get("task") else ""
    return {"agent.status": f"{p['status']}{task}"}


def _last_entry(path: Path) -> dict | None:
    last = None
    with open(path, "rb") as f:
        f.seek(0, os.SEEK_END)
        f.seek(max(0, f.tell() - 200_000))
        for line in f.read().decode("utf-8", "replace").splitlines():
            try:
                d = json.loads(line)
            except ValueError:
                continue
            if d.get("type") in ("assistant", "user"):
                last = d
    return last


def status_from_transcript(path: str | Path, clock: Callable[[], float] = time.time) -> dict:
    path = Path(path)
    age = clock() - path.stat().st_mtime
    last = _last_entry(path)
    msg = (last or {}).get("message") or {}
    ended = (last or {}).get("type") == "assistant" and msg.get("stop_reason") == "end_turn"
    if ended:
        status = "finished"
    elif age <= WORKING_SECONDS:
        status = "working"
    elif age > SILENT_SECONDS:
        status = "silent"
    else:
        status = "idle"
    return {"agent": path.stem, "status": status, "last_activity": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(path.stat().st_mtime)), "source": str(path)}


def observe_agent(store: Store, p: dict, clock: Callable[[], str] = now) -> dict:
    ev = Evidence("tool", f"transcript {p.get('source') or p['agent']}", json.dumps({k: p.get(k) for k in ("status", "last_activity")}))
    handle = f"python -c \"import statekeeper.writers.agent_status as a; print(a.status_from_transcript(r'{p.get('source', '<transcript>')}'))\""
    return observe_keyed(store, writer=WRITER, thing=p["agent"], parent=None, observations=normalize(p), evidence=ev, handle=handle,
                         as_of=p.get("last_activity"), clock=clock)
