"""Live probes: a few read-only reads of what is true right now (the board's health, the spend cap, open asks, the build-queue lock), shown per prompt
as one short "Live now" block when something is abnormal or changed since the last turn of this session. Stored states go stale between observer
runs; these are read fresh (or from a cache at most a probe's ttl old) and never written to the store.

Runner (called by inject_hook before the prompt context): probes whose cached value is older than their ttl and that are cheap run inline, in
daemon threads, under one overall deadline (DEADLINE seconds); a slow or late probe keeps its cached value, shown with its age. Slow probes (the
board's health check takes seconds) only ever run in the background refresher this module starts detached (`python probes.py --refresh <home>`),
at most every REFRESH_EVERY seconds. Every child process goes through statekeeper.proc.run (no console window). Any exception yields no block.

Shown: abnormal lines always (a failing health check, the spend cap paused, a build lock held too long); other lines only when they are new or
changed since this session's last turn (a new ask, a check that turned WARN). The first prompt of a session shows only abnormal lines (the session
block already listed the rest). STATEKEEPER_PROBES=0 turns it off.
"""
from __future__ import annotations

import json
import os
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

BOARD = os.environ.get("STATEKEEPER_BOARD_SH") or "C:/repos/work-board/scripts/board.sh"
BOARD_API = os.environ.get("STATEKEEPER_BOARD_API") or "http://127.0.0.1:7788/api"
BUILD_QUEUE = os.environ.get("STATEKEEPER_BUILD_QUEUE") or "C:/repos/_handoffs/local-build-queue.json"
DEADLINE = 1.5
REFRESH_EVERY = 30
MAX_CHARS = 600
LOCK_LONG_MIN = 45
CACHE = "probes-cache.json"
SHOWN = "probes-shown.json"


@dataclass(frozen=True)
class Probe:
    name: str
    read: Callable[[], list[dict]]    # -> [{"key", "text", "sig", "abnormal"}]
    ttl: int
    inline: bool = True


def _run(argv: list[str], timeout: float):
    from statekeeper.proc import run
    return run(argv, capture_output=True, text=True, encoding="utf-8", timeout=timeout)


def spend() -> list[dict]:
    r = _run(["bash", BOARD, "spend-status"], 1.2)
    paused = r.returncode == 3
    line = (r.stdout or "").strip().splitlines()[0] if (r.stdout or "").strip() else f"exit {r.returncode}"
    return [{"key": "spend", "text": f"spend cap: {line[:120]}", "sig": "paused" if paused else "ok", "abnormal": paused}]


def asks() -> list[dict]:
    import urllib.request
    with urllib.request.urlopen(f"{BOARD_API}/asks", timeout=0.8) as r:
        rows = json.loads(r.read().decode("utf-8"))
    from statekeeper.writers.board_asks import is_done
    out = []
    for a in rows if isinstance(rows, list) else []:
        if not isinstance(a, dict) or is_done(a):
            continue
        aid = str(a.get("id") or a.get("title") or "")
        out.append({"key": f"ask:{aid}", "text": f"ask ({a.get('kind') or 'ask'}): {str(a.get('title') or aid)[:110]}", "sig": aid, "abnormal": False})
    return out


def build_queue() -> list[dict]:
    try:
        data = json.loads(Path(BUILD_QUEUE).read_text(encoding="utf-8") or "{}")
    except (OSError, ValueError):
        return []
    holders = data.get("holders") if isinstance(data, dict) else None
    if holders is None and isinstance(data, dict) and data.get("holder"):
        holders = [data]
    out = []
    for h in holders or []:
        if not isinstance(h, dict):
            continue
        who = str(h.get("holder") or h.get("agent") or h.get("owner") or "?")
        since = str(h.get("since") or h.get("acquiredUtc") or h.get("at") or "")
        age = None
        try:
            from datetime import datetime, timezone
            age = (datetime.now(timezone.utc) - datetime.fromisoformat(since.replace("Z", "+00:00"))).total_seconds() / 60
        except ValueError:
            pass
        long = age is not None and age > LOCK_LONG_MIN
        out.append({"key": f"build-lock:{who}", "text": f"build lock held by {who}" + (f" for {int(age)}m" if age is not None else ""),
                    "sig": who, "abnormal": long})
    return out


def board_health() -> list[dict]:
    r = _run(["bash", BOARD, "health", "--json"], 30)
    data = json.loads(r.stdout or "{}")
    out = []
    for c in data.get("checks") or []:
        st = str(c.get("status") or "").upper()
        if st in ("WARN", "FAIL"):
            out.append({"key": f"health:{c.get('name')}", "text": f"{st} {c.get('name')}: {str(c.get('msg') or '')[:100]}", "sig": st, "abnormal": st == "FAIL"})
    return out


PROBES: list[Probe] = [Probe("spend", spend, 120), Probe("asks", asks, 60), Probe("build-queue", build_queue, 15),
                       Probe("board-health", board_health, 180, inline=False)]


def _load(path: Path) -> dict:
    try:
        got = json.loads(path.read_text(encoding="utf-8"))
        return got if isinstance(got, dict) else {}
    except (OSError, ValueError):
        return {}


def _save(path: Path, data: dict) -> None:
    tmp = path.with_suffix(f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps(data), encoding="utf-8")
    os.replace(tmp, path)


def refresh(home: Path, probes: Optional[list[Probe]] = None, *, inline_only: bool = False, deadline: Optional[float] = None,
            now: Optional[float] = None) -> dict:
    """Reads the stale probes (all of them, or only the inline ones under `deadline` seconds) into the cache; returns the cache."""
    probes = PROBES if probes is None else probes
    now = now or time.time()
    cache = _load(home / CACHE)
    todo = [p for p in probes if now - float((cache.get(p.name) or {}).get("at") or 0) > p.ttl and (p.inline or not inline_only)]
    got: dict[str, dict] = {}

    def one(p: Probe) -> None:
        try:
            got[p.name] = {"at": time.time(), "lines": p.read()}
        except Exception as e:
            got[p.name] = {"at": time.time(), "lines": (cache.get(p.name) or {}).get("lines", []), "error": f"{type(e).__name__}"[:60]}
    threads = [threading.Thread(target=one, args=(p,), daemon=True) for p in todo]
    for t in threads:
        t.start()
    end = time.time() + (deadline if deadline is not None else 60)
    for t in threads:
        t.join(max(0.0, end - time.time()))
    if got:
        cache = {**_load(home / CACHE), **dict(got)}               # re-read: a background refresher may have written meanwhile
        _save(home / CACHE, cache)
    return cache


def start_background(home: Path, now: Optional[float] = None) -> bool:
    """Starts `probes.py --refresh <home>` detached, at most every REFRESH_EVERY seconds (stamp touched first, so two hooks do not both start one)."""
    import subprocess
    now = now or time.time()
    stamp = home / "probes.stamp"
    try:
        if now - stamp.stat().st_mtime < REFRESH_EVERY:
            return False
    except OSError:
        pass
    stamp.write_text(str(now), encoding="utf-8")
    flags = (getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NO_WINDOW", 0)) if os.name == "nt" else 0
    argv = [sys.executable, str(Path(__file__).resolve()), "--refresh", str(home)]
    subprocess.Popen(argv, creationflags=flags, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, close_fds=True)
    return True


def _age(seconds: float) -> str:
    return f"{int(seconds)}s" if seconds < 90 else f"{int(seconds // 60)}m" if seconds < 5400 else f"{int(seconds // 3600)}h"


def live_block(home: Path, session: str, *, probes: Optional[list[Probe]] = None, now: Optional[float] = None, background: bool = True) -> str:
    """The "Live now" block for this prompt ("" when nothing is abnormal or changed). Never raises."""
    try:
        probes = PROBES if probes is None else probes
        home.mkdir(parents=True, exist_ok=True)
        cache = refresh(home, probes, inline_only=True, deadline=DEADLINE, now=now)
        t = now or time.time()
        if background and any(t - float((cache.get(p.name) or {}).get("at") or 0) > p.ttl for p in probes):
            try:
                start_background(home, t)
            except Exception:
                pass
        shown_all = _load(home / SHOWN)
        first = session not in shown_all
        before = shown_all.get(session) or {}
        now_sigs: dict[str, str] = {}
        lines: list[str] = []
        for p in probes:
            entry = cache.get(p.name) or {}
            age = _age(max(0.0, t - float(entry.get("at") or t)))
            for ln in entry.get("lines") or []:
                key, sig = str(ln.get("key")), str(ln.get("sig"))
                now_sigs[key] = sig
                if ln.get("abnormal") or (not first and before.get(key) != sig):
                    lines.append(f"- {ln.get('text')} [{p.name}, read {age} ago]")
        shown_all[session] = now_sigs
        if len(shown_all) > 50:                                          # keep the newest sessions only
            shown_all = dict(list(shown_all.items())[-50:])
        _save(home / SHOWN, shown_all)
        if not lines:
            return ""
        out, used = ["Live now (read-only probes; changed since your last turn, or abnormal):"], 0
        for i, ln in enumerate(lines):
            if used + len(ln) > MAX_CHARS:
                out.append(f"- ... and {len(lines) - i} more")
                break
            out.append(ln)
            used += len(ln)
        return "\n".join(out)
    except Exception:
        return ""


if __name__ == "__main__":
    if len(sys.argv) >= 3 and sys.argv[1] == "--refresh":
        sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
        refresh(Path(sys.argv[2]))
