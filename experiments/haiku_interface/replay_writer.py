"""Replays real transcript turns through the Haiku writer on a COPY of the store, and prints what it filed where.

    python experiments/haiku_interface/replay_writer.py --db <COPY> --transcript <session.jsonl> --since 2026-11-25 --turns 12 --ledger out/haiku-exp/ledger.jsonl

A turn = one message the person typed (not a harness/agent notification) + the assistant's text replies until the next typed message
(tool calls and tool output left out). Turns are taken evenly across the window, skipping short ones.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1] / "src"))
sys.path.insert(0, str(HERE.parents[1] / "adapters" / "claude_code"))
sys.path.insert(0, str(HERE))

AUTOMATED = re.compile(r"^\s*<(task-notification|agent-message|system-reminder|local-command|command-[a-z]+|bash-[a-z]+|user-prompt-submit-hook)\b")


def text_of(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text")
    return ""


def turns(path: Path, since: str, until: str = "9999") -> list[dict]:
    out: list[dict] = []
    cur = None
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            if '"type":"user"' not in line and '"type":"assistant"' not in line:
                continue
            try:
                e = json.loads(line)
            except ValueError:
                continue
            ts = e.get("timestamp") or ""
            if ts < since or ts > until or e.get("isSidechain"):
                continue
            msg = e.get("message") or {}
            if e.get("type") == "user":
                c = msg.get("content")
                if isinstance(c, list) and any(isinstance(b, dict) and b.get("type") == "tool_result" for b in c):
                    continue
                t = text_of(c).strip()
                if not t or AUTOMATED.match(t) or e.get("isMeta"):
                    continue
                cur = {"ts": ts, "uuid": e.get("uuid"), "user": t, "assistant": []}
                out.append(cur)
            elif cur is not None:
                t = text_of(msg.get("content")).strip()
                if t:
                    cur["assistant"].append(t)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--transcript", required=True)
    ap.add_argument("--since", required=True)
    ap.add_argument("--until", default="9999")
    ap.add_argument("--turns", type=int, default=12)
    ap.add_argument("--min-user-chars", type=int, default=60)
    ap.add_argument("--ledger", required=True)
    args = ap.parse_args()
    if Path(args.db).name == "shadow.db":
        raise SystemExit("refusing: pass a copy of the store, never the live shadow.db")
    from statekeeper import SqliteStore
    from statekeeper.embed import HttpEmbedder
    from writer import Writer
    store = SqliteStore(args.db)
    try:
        emb = HttpEmbedder(timeout=5.0)
        emb.embed(["ping"])
    except Exception:
        emb = None
    all_turns = [t for t in turns(Path(args.transcript), args.since, args.until) if len(t["user"]) >= args.min_user_chars]
    step = max(1, len(all_turns) // max(1, args.turns))
    pick = all_turns[::step][:args.turns]
    print(f"{len(all_turns)} typed turns in the window; replaying {len(pick)}")
    w = Writer(store, Path(args.ledger), embedder=emb)
    for t in pick:
        body = f"[user] {t['user'][:4000]}\n\n[assistant] " + "\n\n".join(t["assistant"])[:6000]
        rec = w.write(body, ref=f"transcript:{Path(args.transcript).stem}:{t['uuid']}", when=t["ts"][:16].replace("T", " "))
        print(f"\n=== {t['ts'][:16]} {t['user'][:100]!r}")
        print(f"    {rec.get('seconds')}s in {rec.get('in_tok')} out {rec.get('out_tok')} tok; candidates {rec.get('candidates')[:6]}{' ERROR ' + rec['error'] if rec.get('error') else ''}")
        for a in rec["applied"]:
            if a["op"] == "add":
                print(f"    ADD   #{a['new']} [{a['thing']}]{' (NEW THING)' if a.get('new_thing') else ''} {a['text'][:150]}")
            elif a["op"] == "confirm":
                print(f"    CONF  #{a['state']} [{a['thing']}] {a['old_text'][:120]}")
            else:
                print(f"    SUPER #{a['old']} -> #{a['new']} [{a['thing']}]\n          old: {a['old_text'][:140]}\n          new: {a['text'][:140]}")
        for r in rec["rejected"]:
            print(f"    REJ   {r['why']}: {r['fact'].get('op')} [{r['fact'].get('thing')}] {str(r['fact'].get('text'))[:100]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
