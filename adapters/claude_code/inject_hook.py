"""Live injection hook (UserPromptSubmit), behind a flag that is OFF by default.

Registered synchronously (it must be able to add context), but it exits silently and immediately unless STATEKEEPER_INJECT=1. When on, it prints the
state-first context for the prompt as hook additionalContext, and logs exactly what the shadow adapter logs (surfaced/omitted outcomes, the prompt, the
injected text), so the comparison with Legacy keeps running. Quiet when nothing relevant exists: it prints nothing. It never fails a turn: any error
prints nothing and exits 0; the whole call is bounded (embedding timeout 1 s, hook timeout set at install).

One-line rollback: STATEKEEPER_INJECT=0 (shell or settings.json env), or `python tools/install_shadow_hooks.py --remove`. STATEKEEPER_SHADOW=0 turns off
all statekeeper adapters. Legacy's own recall is a separate switch; this hook does not touch it.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import shadow_hook  # noqa: E402

CAP = 4000


def main() -> int:
    if os.environ.get("STATEKEEPER_SHADOW", "1").strip().lower() in shadow_hook.OFF or not shadow_hook.inject_on():
        return 0
    try:
        event = json.load(sys.stdin)
        if event.get("hook_event_name") not in (None, "UserPromptSubmit"):
            return 0
        from statekeeper import SqliteStore
        from statekeeper.embed import HttpEmbedder
        home = shadow_hook.HOME
        home.mkdir(parents=True, exist_ok=True)
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
