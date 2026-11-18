"""Shadow-mode adapter for Claude Code hooks: LOG ONLY, runs beside Legacy, never changes what the model sees.

One entry point for three events (the hook event name is in the stdin JSON):
  UserPromptSubmit  surface() for the prompt with outcome logging (surfaced/omitted); records the prompt text; prints NOTHING.
  Stop              reads the agent's answer for the turn, labels used/missed from it, records it, counts the commitments it made; prints nothing.
  SessionStart      logs the session start (source: startup/resume/compact/clear) to the shadow log; prints nothing.

Safety: registered async (it cannot block a turn); prints nothing to stdout ever; exits 0 on every error; each call is bounded (embedding timeout 1 s);
STATEKEEPER_SHADOW=0 (or off/false) turns it off completely. State lives outside the repo: STATEKEEPER_DB (default ~/.claude/statekeeper/shadow.db) and the
event log ~/.claude/statekeeper/shadow.log.jsonl. Nothing here edits settings or touches Legacy.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

HOME = Path(os.environ.get("STATEKEEPER_HOME") or Path.home() / ".claude" / "statekeeper")
OFF = ("0", "off", "false", "no")


def log(event: dict) -> None:
    event["at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    HOME.mkdir(parents=True, exist_ok=True)
    with open(HOME / "shadow.log.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(event) + "\n")


def last_answer(transcript_path: str, limit: int = 400_000) -> str:
    """The assistant text blocks written since the last real user message in the transcript."""
    text, parts = "", []
    with open(transcript_path, "rb") as f:
        f.seek(0, os.SEEK_END)
        f.seek(max(0, f.tell() - limit))
        raw = f.read().decode("utf-8", "replace").splitlines()
    for line in raw:
        try:
            d = json.loads(line)
        except ValueError:
            continue
        if d.get("isSidechain") or d.get("type") not in ("user", "assistant"):
            continue
        content = (d.get("message") or {}).get("content")
        if d["type"] == "user":
            blocks = content if isinstance(content, list) else [{"type": "text", "text": content or ""}]
            if not any(isinstance(b, dict) and b.get("type") == "tool_result" for b in blocks):
                parts = []
        else:
            parts += [b.get("text", "") for b in (content if isinstance(content, list) else []) if isinstance(b, dict) and b.get("type") == "text"]
    return "\n".join(p for p in parts if p.strip())


def classify(text: str) -> str:
    head = text.lstrip()[:200]
    if head.startswith("[statekeeper]"):
        return "statekeeper_live"
    if head.startswith("Concept map"):
        return "concept_map"
    if head.startswith("## Tracked now"):
        return "tracked"
    if head.startswith("# Who you are") or "my working note" in text[:400]:
        return "session_core"
    return "other"


def injected_since(transcript_path: str, since_epoch: float, hook_prefix: str, limit: int = 6_000_000) -> list[tuple[str, str]]:
    """(hookName, text) of the hook_additional_context attachments Claude Code recorded since `since_epoch` for hooks whose name starts with `hook_prefix`.
    This is how Legacy's actual injection is read back: from the transcript, after the fact, never by intercepting the hook."""
    from datetime import datetime
    out = []
    with open(transcript_path, "rb") as f:
        f.seek(0, os.SEEK_END)
        f.seek(max(0, f.tell() - limit))
        raw = f.read().decode("utf-8", "replace").splitlines()
    for line in raw:
        if '"hook_additional_context"' not in line:
            continue
        try:
            d = json.loads(line)
            a = d.get("attachment") or {}
            if a.get("type") != "hook_additional_context" or not str(a.get("hookName", "")).startswith(hook_prefix):
                continue
            at = datetime.fromisoformat(d["timestamp"].replace("Z", "+00:00")).timestamp()
        except (ValueError, KeyError, TypeError):
            continue
        if at >= since_epoch:
            content = a.get("content")
            out.append((a["hookName"], "\n".join(content) if isinstance(content, list) else str(content or "")))
    return out


OBSERVE_EVERY_SECONDS = 20 * 60


def maybe_observe(state_dir: Path, now: float | None = None) -> bool:
    """Starts the live writers (read-only GitHub/ADO/agent-status reads) detached, at most every 20 minutes, from a prompt event. Returns whether it started one.
    The stamp is touched BEFORE starting, so concurrent hooks do not both start one. STATEKEEPER_OBSERVE=0 turns this off."""
    import subprocess
    if os.environ.get("STATEKEEPER_OBSERVE", "1").strip().lower() in OFF:
        return False
    now = now or time.time()
    stamp = state_dir / "observe.stamp"
    try:
        if now - stamp.stat().st_mtime < OBSERVE_EVERY_SECONDS:
            return False
    except OSError:
        pass
    stamp.write_text(str(now), encoding="utf-8")
    script = Path(__file__).resolve().parents[2] / "tools" / "observe_all.py"
    args = [sys.executable, str(script)] + (["--db", os.environ["STATEKEEPER_DB"]] if os.environ.get("STATEKEEPER_DB") else [])
    flags = (getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NO_WINDOW", 0)) if os.name == "nt" else 0
    with open(state_dir / "observe.log", "a", encoding="utf-8") as out:
        subprocess.Popen(args, stdout=out, stderr=out, stdin=subprocess.DEVNULL, creationflags=flags, close_fds=True)
    return True


MARK = "[statekeeper] "
LIVE_BUDGET = 1800
LIVE_FLOOR = 0.78          # stricter than the 0.72 default: on 27 real prompts 0.72 injected on 25, 0.78 on 18 and halves the size
LIVE_MAX_THINGS = 3
LIVE_MAX_STATES = 4
RECENT_TURNS = 3


def inject_on() -> bool:
    return os.environ.get("STATEKEEPER_INJECT", "0").strip().lower() in ("1", "true", "on", "yes")


def prompt_context(event: dict, store, embedder, state: dict, state_file: Path, state_dir: Path) -> tuple[dict, str]:
    """The state-first context for one prompt: surfaces, logs outcomes and the would-have-injected text, records the prompt. Returns (log record, block).
    The block is what the live inject hook prints when the flag is on; in shadow mode the caller discards it. Quiet by design: the agent and people (they are
    in the session-start core), the working note and states shown in the last three turns of this session are left out."""
    from statekeeper import surface
    session = str(event.get("session_id") or "")
    prompt = (event.get("prompt") or "").strip()
    if not prompt:
        return {}, ""
    turn_ref = f"{session}:{int(time.time() * 1000)}"
    t0 = time.time()
    recent = state.get(f"recent:{session}") or []
    skip = frozenset(i for ids in recent for i in ids)
    out = surface(store, prompt, embedder, log_turn=turn_ref, floor=LIVE_FLOOR, max_things=LIVE_MAX_THINGS, budget=LIVE_BUDGET, exclude_kinds=("agent", "person"),
                  exclude_names=("working note",), skip_state_ids=skip, max_states=LIVE_MAX_STATES)
    block = (MARK + out.block) if out.block else ""
    store.record_turn_text(turn_ref, "prompt", prompt[:20000], session_id=session)
    store.record_injection(turn_ref, "statekeeper", "UserPromptSubmit", "concept_map", block)
    state[session] = turn_ref
    state[f"recent:{session}"] = (recent + [[s.id for i in out.items for s in i.states]])[-RECENT_TURNS:]
    state_file.write_text(json.dumps(dict(list(state.items())[-300:])), encoding="utf-8")
    rec = {"event": "UserPromptSubmit", "session": session, "turn": turn_ref, "things": [i.thing.name for i in out.items], "block_chars": len(block), "ms": int((time.time() - t0) * 1000),
           "prompt_sha": hashlib.sha256(prompt.encode("utf-8")).hexdigest()[:12], "live": inject_on()}
    rec["observe_started"] = maybe_observe(state_dir) if os.environ.get("STATEKEEPER_NO_OBSERVE_IN_TESTS") != "1" else False
    log(rec)
    return rec, block


def handle(event: dict, store, embedder, state_dir: Path) -> dict:
    """Does the shadow work for one hook event; returns what it logged (also used by tests)."""
    name = event.get("hook_event_name") or event.get("event") or ""
    session = str(event.get("session_id") or "")
    state_file = state_dir / "shadow-state.json"
    try:
        state = json.loads(state_file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        state = {}
    if name == "UserPromptSubmit":
        if inject_on():
            return {}                      # the live inject hook handles this prompt (and logs it the same way); doing it twice would double-count
        return prompt_context(event, store, embedder, state, state_file, state_dir)[0]
    if name == "Stop":
        from statekeeper.commitments import find_commitments
        from statekeeper.outcomes import label_turn
        turn_ref = state.get(session)
        try:
            answer = last_answer(event["transcript_path"]) if event.get("transcript_path") else ""
        except OSError:
            answer = ""
        answer = answer or str(event.get("last_assistant_message") or "")
        if not turn_ref or not answer:
            return {}
        counts = label_turn(store, turn_ref, answer, source="shadow-stop")
        store.record_turn_text(turn_ref, "output", answer[:40000], session_id=session)
        if event.get("transcript_path"):
            try:
                started = int(turn_ref.rsplit(":", 1)[1]) / 1000.0
                for hook, text in injected_since(event["transcript_path"], started - 10, "UserPromptSubmit"):
                    if classify(text) != "statekeeper_live":              # our own live injection is not Legacy's
                        store.record_injection(turn_ref, "legacy", hook, classify(text), text[:60000])
                pending = state.pop(f"start:{session}", None)
                if pending:
                    for hook, text in injected_since(event["transcript_path"], pending["at"] - 10, "SessionStart"):
                        store.record_injection(pending["ref"], "legacy", hook, classify(text), text[:60000])
                    state_file.write_text(json.dumps(state), encoding="utf-8")
            except (OSError, ValueError, IndexError):
                pass
        rec = {"event": name, "session": session, "turn": turn_ref, "used": counts["used"], "missed": counts["missed"], "commitments": len(find_commitments(answer)), "answer_chars": len(answer)}
        log(rec)
        return rec
    if name == "SessionStart":
        from statekeeper.session import session_block
        ref = f"sess:{session}:{int(time.time() * 1000)}"
        block = session_block(store)
        store.record_injection(ref, "statekeeper", f"SessionStart:{event.get('source') or 'startup'}", "session_core", block)
        state[f"start:{session}"] = {"ref": ref, "at": time.time()}
        state_file.write_text(json.dumps(dict(list(state.items())[-200:])), encoding="utf-8")
        rec = {"event": name, "session": session, "source": event.get("source"), "block_chars": len(block)}
        log(rec)
        return rec
    return {}


def main() -> int:
    if os.environ.get("STATEKEEPER_SHADOW", "1").strip().lower() in OFF:
        return 0
    try:
        event = json.load(sys.stdin)
        from statekeeper import SqliteStore
        from statekeeper.embed import HttpEmbedder
        HOME.mkdir(parents=True, exist_ok=True)
        store = SqliteStore(os.environ.get("STATEKEEPER_DB") or HOME / "shadow.db")
        embedder = HttpEmbedder(timeout=1.0)
        handle(event, store, embedder, HOME)
    except BaseException as e:  # never fail, never print: a shadow adapter must be invisible
        try:
            log({"event": "adapter-error", "error": f"{type(e).__name__}: {str(e)[:200]}"})
        except BaseException:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
