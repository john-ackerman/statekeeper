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
SESSION_CAP = 16000
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


def ensure_embed_server() -> str:
    """Starts the embedding server when nothing answers at STATEKEEPER_EMBED_URL and the configuration names a command that starts it
    (`embed_server_command`, run detached). Meaning-based recall needs it; without it recall falls back to names. Never raises."""
    try:
        import socket
        from urllib.parse import urlparse
        url = urlparse(os.environ.get("STATEKEEPER_EMBED_URL") or "http://127.0.0.1:8081/v1")
        try:
            socket.create_connection((url.hostname, url.port or 80), timeout=1).close()
            return "up"
        except OSError:
            pass
        from statekeeper import config
        cmd = config.load().get("embed_server_command")
        if not isinstance(cmd, list) or not cmd:
            return "down (no embed_server_command)"
        import subprocess
        flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) | getattr(subprocess, "CREATE_NO_WINDOW", 0)
        subprocess.Popen([str(c) for c in cmd], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=flags, close_fds=True)
        return "started"
    except BaseException as e:
        return f"{type(e).__name__}: {str(e)[:120]}"


def main() -> int:
    if os.environ.get("STATEKEEPER_SHADOW", "1").strip().lower() in shadow_hook.OFF or not shadow_hook.inject_on():
        return 0
    try:
        event = shadow_hook.read_event()
        name = event.get("hook_event_name")
        if name not in (None, "UserPromptSubmit", "SessionStart"):
            return 0
        from statekeeper import SqliteStore
        home = shadow_hook.HOME
        home.mkdir(parents=True, exist_ok=True)
        if name == "SessionStart":
            t0 = time.time()
            embed = ensure_embed_server()
            if event.get("source") in ("compact", "clear"):
                (home / "curated" / f"{event.get('session_id') or 'unknown'}.marker").unlink(missing_ok=True)   # a new stretch: the curation pass may fire again
            status = refresh()
            from statekeeper.session import session_block
            store = SqliteStore(os.environ.get("STATEKEEPER_DB") or home / "shadow.db")
            block = session_block(store, budget=SESSION_CAP)
            if block:
                text = shadow_hook.MARK + "Session start: standing state from the statekeeper store.\n\n" + block
                sys.stdout.write(json.dumps({"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": text[:SESSION_CAP]}}))
            shadow_hook.log({"event": "inject-session-start", "source": event.get("source"), "session": str(event.get("session_id") or ""),
                             "refresh": status, "embed": embed, "block_chars": len(block), "ms": int((time.time() - t0) * 1000)})
            return 0
        from statekeeper.embed import HttpEmbedder
        store = SqliteStore(os.environ.get("STATEKEEPER_DB") or home / "shadow.db")
        state_file = home / "shadow-state.json"
        try:
            state = json.loads(state_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            state = {}
        _, block = shadow_hook.prompt_context(event, store, HttpEmbedder(timeout=1.0), state, state_file, home)
        try:
            import tool_hook
            if not shadow_hook.is_automated((event.get("prompt") or "").strip()):
                cued = tool_hook.cues(home, "prompt", event.get("prompt") or "", str(event.get("session_id") or ""))
                if cued:
                    block = (block + "\n\n" if block else shadow_hook.MARK) + "A cue set for prompts like this one:\n" + "\n".join(cued)
        except BaseException:
            pass
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
