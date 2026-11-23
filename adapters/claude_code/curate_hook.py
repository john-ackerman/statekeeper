"""Stop hook: once per session stretch, when the context passes a threshold, hold the turn once and ask for a curation pass into Statekeeper
(what this stretch earned that is not yet stored, the working note brought up to date) before compaction resets the context.

Synchronous (a Stop hook blocks by printing {"decision": "block", "reason": ...}). Fires at most once per session until the next compaction or
clear (the SessionStart inject hook removes the marker). Threshold: STATEKEEPER_CURATE_AT tokens (default 500000; 0 turns it off). Never fails a
turn: any error prints nothing.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import shadow_hook  # noqa: E402

SK = f'python "{Path(__file__).resolve().parents[2] / "tools" / "sk.py"}"'


def context_tokens(path: str) -> int:
    """The context the last main-thread reply was given: input plus cache reads and writes (from the transcript's usage)."""
    try:
        with open(path, "rb") as f:
            f.seek(0, 2)
            f.seek(max(0, f.tell() - 4_000_000))
            lines = f.read().decode("utf-8", "replace").splitlines()
    except OSError:
        return 0
    for line in reversed(lines):
        try:
            e = json.loads(line)
        except ValueError:
            continue
        if e.get("type") != "assistant" or e.get("isSidechain"):
            continue
        u = (e.get("message") or {}).get("usage") or {}
        if u:
            return u.get("input_tokens", 0) + u.get("cache_read_input_tokens", 0) + u.get("cache_creation_input_tokens", 0)
    return 0


def marker(home: Path, session: str) -> Path:
    return home / "curated" / f"{session or 'unknown'}.marker"


def decide(event: dict, home: Path, threshold: int) -> str:
    """The block reason when a curation pass is due, else ''. Writes the marker when it fires."""
    if threshold <= 0 or event.get("stop_hook_active"):
        return ""
    m = marker(home, str(event.get("session_id") or ""))
    if m.exists():
        return ""
    tokens = context_tokens(event.get("transcript_path") or "")
    if tokens < threshold:
        return ""
    m.parent.mkdir(parents=True, exist_ok=True)
    m.write_text(str(tokens), encoding="utf-8")
    return (f"Your context is at {tokens:,} tokens. Before long it is reset, and what you know then is Statekeeper: the core, the working note "
            f"and what is tracked. This is your curation pass, once per stretch. Save what this stretch earned and is not stored yet: decisions in the "
            f"person's own words, tools and how to use them, what is running and how to pick it up, what you learned about the work, the person and "
            f"yourself (`{SK} add <thing> \"<sentence>\" [--stable|--rule] [--evidence <ref>]`, under the most specific thing, `\"Parent > new thing\"` "
            f"for a new one under a known parent; `{SK} search <words>` first, and `{SK} supersede "
            f"<id> \"<new text>\"` rather than adding beside a stale state). Bring the working note up to date (`{SK} note --content -`). Leave out "
            "the transcript itself (the conversation is recorded), what the core already says, and narration: keep conclusions, decisions and how to "
            "resume. Then carry on with what you were doing.")


def main() -> int:
    if os.environ.get("STATEKEEPER_SHADOW", "1").strip().lower() in shadow_hook.OFF:
        return 0
    try:
        event = shadow_hook.read_event()
        reason = decide(event, shadow_hook.HOME, int(os.environ.get("STATEKEEPER_CURATE_AT", "500000")))
        if reason:
            sys.stdout.write(json.dumps({"decision": "block", "reason": reason}))
            shadow_hook.log({"event": "curate-pass", "session": str(event.get("session_id") or "")})
    except BaseException as e:
        try:
            shadow_hook.log({"event": "inject-error", "hook": "curate", "error": f"{type(e).__name__}: {str(e)[:200]}"})
        except BaseException:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
