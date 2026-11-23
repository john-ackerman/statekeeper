"""SubagentStart hook: give every subagent the same standing core the main session gets (identity, the people it works with, the working note,
what is tracked now), with the commands to look further and to write what it learns.

Synchronous (it must add context). Prints a SubagentStart additionalContext JSON, or nothing. Never fails a launch: any error prints nothing and
exits 0. Off with STATEKEEPER_SHADOW=0 or STATEKEEPER_SUBAGENT=0.
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

import shadow_hook  # noqa: E402

CAP = 16000
SK = f'python "{Path(__file__).resolve().parents[2] / "tools" / "sk.py"}"'
LEAD = ("# Who you are\n\nYou are a subagent of a Claude whose standing state lives in Statekeeper; this is that core, and so yours: the same self, "
        "with a task. Before re-deriving something from the code, look: `{sk} recall <words>` or `{sk} search <words>` (`show <id>` for the full "
        "text). When you learn something the main session cannot see from your report (a gotcha, a tool and how to use it, a fact about the code), "
        "write it: `{sk} add <thing> \"<one sentence>\" [--stable] [--evidence <file or URL>]` (the most specific thing: a flag, PR or ticket "
        "over its tool or repo; `\"Parent > new thing\"` files a new one under a known parent; it names near-duplicates; `supersede <id>` "
        "instead of adding beside one). Leave the working note to the main session.")


def block(store, budget: int = CAP) -> str:
    from statekeeper.session import session_block
    body = session_block(store, budget=budget - 800)
    return f"{LEAD.format(sk=SK)}\n\n{body}" if body.strip() else ""


def main() -> int:
    if any(os.environ.get(v, "1").strip().lower() in shadow_hook.OFF for v in ("STATEKEEPER_SHADOW", "STATEKEEPER_SUBAGENT")):
        return 0
    t0 = time.time()
    try:
        event = shadow_hook.read_event()
        from statekeeper import SqliteStore
        store = SqliteStore(os.environ.get("STATEKEEPER_DB") or shadow_hook.HOME / "shadow.db")
        text = block(store)
        if text:
            sys.stdout.write(json.dumps({"hookSpecificOutput": {"hookEventName": "SubagentStart", "additionalContext": text[:CAP]}}))
        shadow_hook.log({"event": "inject-subagent-start", "session": str(event.get("session_id") or ""), "agent": str(event.get("agent_id") or event.get("agent_type") or ""),
                         "block_chars": len(text), "ms": int((time.time() - t0) * 1000)})
    except BaseException as e:
        try:
            shadow_hook.log({"event": "inject-error", "hook": "SubagentStart", "error": f"{type(e).__name__}: {str(e)[:200]}"})
        except BaseException:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
