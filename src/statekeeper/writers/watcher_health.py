"""Health of the orchestrator's board watcher, read only, written as one keyed `watcher.status` state on "work-board watcher".

Two reads: the heartbeat file the watcher touches (C:/repos/_handoffs/orch-watch-heartbeat: epoch seconds in the file, file time as the fallback) and whether the
board server answers. The pause flag (orch-watch.paused) means the watcher is deliberately idle. The value is a state word plus the heartbeat age; a re-read
with the same state word only confirms (the age is refreshed whenever the word changes).
"""
from __future__ import annotations

import os
import time
import urllib.request
from pathlib import Path
from typing import Callable, Optional

from ..store import Evidence, Store, StoreError
from .common import observe_keyed

WRITER = "observer:watcher-health"
THING = "work-board watcher"
PARENT = "work-board"
ALIAS = "watcher"
FRESH_MIN = 20        # the watcher beats every few minutes; older than this while not paused means it is not running
DEAD_MIN = 180


def handoffs_dir() -> Path:
    return Path(os.environ.get("STATEKEEPER_HANDOFFS_DIR", "C:/repos/_handoffs"))


def board_answers(base: Optional[str] = None, timeout: float = 3.0) -> bool:
    base = base or os.environ.get("BOARD_URL", "http://127.0.0.1:7788")
    try:
        with urllib.request.urlopen(base + "/", timeout=timeout) as r:
            return r.status == 200
    except Exception:
        return False


def heartbeat_age_min(directory: Path, now: float) -> Optional[int]:
    f = directory / "orch-watch-heartbeat"
    try:
        beat = float(f.read_text(encoding="utf-8").strip().split()[0])
        if beat < 1e9 or beat > now + 86400:
            raise ValueError
    except (OSError, ValueError, IndexError):
        try:
            beat = f.stat().st_mtime
        except OSError:
            return None
    return max(0, int((now - beat) // 60))


def status_text(age: Optional[int], paused: bool, server_up: bool) -> str:
    if age is None:
        word = "no heartbeat file"
    elif paused:
        word = "paused (idle flag set, watcher not expected)"
    elif age <= FRESH_MIN:
        word = "running"
    elif age <= DEAD_MIN:
        word = "heartbeat stale (watcher may be stuck)"
    else:
        word = "not running (heartbeat old)"
    srv = "board server answering" if server_up else "board server NOT answering"
    ago = "" if age is None else f" · last heartbeat {age}m ago" if age < 120 else f" · last heartbeat {age // 60}h ago"
    return f"{word} · {srv}{ago}"


def observe_watcher(store: Store, *, directory: Optional[Path] = None, server_up: Optional[Callable[[], bool]] = None,
                    now: Callable[[], float] = time.time) -> dict:
    d = directory or handoffs_dir()
    t = now()
    text = status_text(heartbeat_age_min(d, t), (d / "orch-watch.paused").exists(), (server_up or board_answers)())
    known = store.find(THING)
    if known:
        cur = [s for s in store.states_of(known.id) if s.state_key == "watcher.status" and s.status == "active"]
        if cur and cur[0].text.split(" · last heartbeat")[0] == text.split(" · last heartbeat")[0]:
            text = cur[0].text                                  # same state, only the heartbeat age moved: confirm
    ev = Evidence("tool", str(d / "orch-watch-heartbeat"), text[:300])
    r = observe_keyed(store, writer=WRITER, thing=THING, parent=PARENT, observations={"watcher.status": text}, evidence=ev,
                      handle="bash C:/repos/work-board/scripts/board.sh health --json", as_of=None)
    if store.find(ALIAS) is None:
        try:                                                        # "is the watcher running": people say the short name
            store.add_alias(THING, ALIAS, "what people call it", actor=WRITER)
        except StoreError:
            pass                                                    # the word already names something else: leave it
    return {"changed": r["changed"]}
