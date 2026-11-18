"""A thin Claude Code UserPromptSubmit adapter: reads the hook event from stdin, asks the Store for the state block, prints additionalContext.

NOT installed or wired anywhere. It depends only on the Store interface and `surface`; nothing here knows SQL. To try it by hand:

    echo '{"prompt": "what is the core quota?", "session_id": "s1"}' | python adapters/claude_code/recall_hook.py

Environment: STATEKEEPER_DB (default out/statekeeper.db), STATEKEEPER_EMBED_URL / STATEKEEPER_EMBED_MODEL (optional; names only without them),
STATEKEEPER_RECALL=0 turns it off. It never fails the turn: any error prints nothing.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from statekeeper import SqliteStore, surface  # noqa: E402
from statekeeper.embed import HttpEmbedder  # noqa: E402

CAP = 9000


def run(event: dict, store, embedder) -> str:
    """The additionalContext text for a UserPromptSubmit event ('' when nothing bears on the prompt)."""
    prompt = (event.get("prompt") or "").strip()
    if not prompt:
        return ""
    return surface(store, prompt, embedder).block[:CAP]


def main() -> int:
    if os.environ.get("STATEKEEPER_RECALL") == "0":
        return 0
    try:
        event = json.loads(sys.stdin.buffer.read().decode("utf-8", "replace") or "{}")
        store = SqliteStore(os.environ.get("STATEKEEPER_DB") or "out/statekeeper.db")
        embedder = HttpEmbedder() if os.environ.get("STATEKEEPER_EMBED_URL") else None
        text = run(event, store, embedder)
    except Exception:  # a hook must never break the session it serves
        return 0
    if text:
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "UserPromptSubmit", "additionalContext": text}}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
