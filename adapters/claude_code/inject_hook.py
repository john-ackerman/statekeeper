"""Live injection hook (UserPromptSubmit and SessionStart), behind a flag that is OFF by default.

Registered synchronously (it must be able to add context), but it exits silently and immediately unless STATEKEEPER_INJECT=1. When on:
  UserPromptSubmit  prints the state-first context for the prompt as hook additionalContext, and logs exactly what the shadow adapter logs
                    (surfaced/omitted outcomes, the prompt, the injected text), so the comparison with Legacy keeps running. Quiet when nothing
                    relevant exists: it prints nothing.
  SessionStart      (startup, resume, clear and compact) first runs the optional `session_refresh_command` from the configuration (bounded, e.g. a
                    re-read of the core and working note from wherever they are kept), then prints the session block: the core states about the
                    agent and the people it works with, the working note, and what is tracked now.
It never fails a turn: any error prints nothing and exits 0; the whole call is bounded (embedding timeout 1 s, refresh timeout 20 s, hook timeout
set at install).

One-line rollback: STATEKEEPER_INJECT=0 (shell or settings.json env), or `python tools/install_shadow_hooks.py --remove`. STATEKEEPER_SHADOW=0 turns off
all statekeeper adapters. Legacy's own recall is a separate switch; this hook does not touch it.
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import shadow_hook  # noqa: E402

CAP = 4000
SESSION_CAP = 9000
REFRESH_TIMEOUT = 20


def refresh() -> str:
    """Runs the configured session_refresh_command, if any; returns a short status for the log. Never raises."""
    try:
        from statekeeper import config
        from statekeeper.proc import run
        cmd = config.load().get("session_refresh_command")
        if not isinstance(cmd, list) or not cmd:
            return "none"
        r = run([str(c) for c in cmd], capture_output=True, text=True, encoding="utf-8", timeout=REFRESH_TIMEOUT)
        return f"exit {r.returncode}"
    except BaseException as e:
        return f"{type(e).__name__}: {str(e)[:120]}"


def main() -> int:
    if os.environ.get("STATEKEEPER_SHADOW", "1").strip().lower() in shadow_hook.OFF or not shadow_hook.inject_on():
        return 0
    try:
        event = json.load(sys.stdin)
        name = event.get("hook_event_name")
        if name not in (None, "UserPromptSubmit", "SessionStart"):
            return 0
        from statekeeper import SqliteStore
        home = shadow_hook.HOME
        home.mkdir(parents=True, exist_ok=True)
        if name == "SessionStart":
            t0 = time.time()
            status = refresh()
            from statekeeper.session import session_block
            store = SqliteStore(os.environ.get("STATEKEEPER_DB") or home / "shadow.db")
            block = session_block(store, budget=SESSION_CAP)
            if block:
                text = shadow_hook.MARK + "Session start: standing state from the statekeeper store.\n\n" + block
                sys.stdout.write(json.dumps({"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": text[:SESSION_CAP]}}))
            shadow_hook.log({"event": "inject-session-start", "source": event.get("source"), "session": str(event.get("session_id") or ""),
                             "refresh": status, "block_chars": len(block), "ms": int((time.time() - t0) * 1000)})
            return 0
        from statekeeper.embed import HttpEmbedder
        store = SqliteStore(os.environ.get("STATEKEEPER_DB") or home / "shadow.db")
        state_file = home / "shadow-state.json"
        try:
            state = json.loads(state_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            state = {}
        _, block = shadow_hook.prompt_context(event, store, HttpEmbedder(timeout=1.0), state, state_file, home)
        if block:
            sys.stdout.write(json.dumps({"hookSpecificOutput": {"hookEventName": "UserPromptSubmit", "additionalContext": block[:CAP]}}))
    except BaseException as e:
        try:
            shadow_hook.log({"event": "inject-error", "error": f"{type(e).__name__}: {str(e)[:200]}"})
        except BaseException:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
