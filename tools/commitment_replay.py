"""Replay stored Claude Code transcripts through the offline commitment detector (not wired to anything) and write a sample of open commitments to judge.

    python tools/commitment_replay.py <transcript.jsonl> [...] [--sample 30] [--out out/commitment-replay]

A turn runs from one user message to the next. Reports turns, commitments found, and how many were still open at the end of their turn; the sample
(seeded) lists open ones with the turn's closing context so each can be marked a real unfulfilled promise or a false positive. Judging is the agent's
until the owner reviews the sample.
"""
from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from statekeeper.commitments import Action, find_commitments, open_commitments, _stems as stems  # noqa: E402

FOLLOW = 10   # how many following turns may close an open commitment (a measured proxy: shared words with a later action or report)


def turns(path: str):
    events, user = [], None
    for line in open(path, encoding="utf-8"):
        try:
            d = json.loads(line)
        except ValueError:
            continue
        if d.get("isSidechain") or d.get("type") not in ("user", "assistant"):
            continue
        content = (d.get("message") or {}).get("content")
        if d["type"] == "user":
            blocks = content if isinstance(content, list) else [{"type": "text", "text": content or ""}]
            if any(b.get("type") == "tool_result" for b in blocks if isinstance(b, dict)):
                continue
            if events:
                yield user, events
            user, events = " ".join(b.get("text", "") for b in blocks if isinstance(b, dict))[:200], []
        else:
            for b in content if isinstance(content, list) else []:
                if b.get("type") == "text" and b.get("text", "").strip():
                    events.append(b["text"])
                elif b.get("type") == "tool_use":
                    events.append(Action(b.get("name", ""), json.dumps(b.get("input", {}))[:500]))
    if events:
        yield user, events


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("transcripts", nargs="+")
    ap.add_argument("--sample", type=int, default=30)
    ap.add_argument("--out", default="out/commitment-replay")
    args = ap.parse_args()
    n_turns = made = still_open = 0
    opens = []
    for p in args.transcripts:
        all_turns = list(turns(p))
        for t, (user, events) in enumerate(all_turns):
            n_turns += 1
            made += sum(len(find_commitments(e)) for e in events if isinstance(e, str))
            op = open_commitments(events)
            still_open += len(op)
            for c in op:
                tail = next((e for e in reversed(events) if isinstance(e, str)), "")
                later = [e for _, ev in all_turns[t + 1:t + 1 + FOLLOW] for e in ev]
                closed = any((isinstance(e, Action) and c.stems & stems(f"{e.tool} {e.detail}")) or (isinstance(e, str) and len(c.stems & stems(e)) >= 2) for e in later)
                opens.append({"transcript": Path(p).name, "user": user, "commitment": c.sentence, "turn_actions_after": sum(1 for e in events[c.position + 1:] if isinstance(e, Action)),
                              "closing_text": tail[-300:], "closed_in_later_turns": closed})
    sample = random.Random(20261116).sample(opens, min(args.sample, len(opens)))
    closed_n = sum(1 for o in opens if o["closed_in_later_turns"])
    summary = {"transcripts": len(args.transcripts), "turns": n_turns, "commitments": made, "open_at_end_of_turn": still_open,
               "open_rate": round(still_open / made, 3) if made else None, "open_per_100_turns": round(100 * still_open / n_turns, 1) if n_turns else None,
               "later_turn_window": FOLLOW, "closed_in_later_turns": closed_n, "never_closed_in_window": still_open - closed_n}
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out + ".json").write_text(json.dumps({"summary": summary, "sample": sample}, indent=1), encoding="utf-8")
    Path(args.out + ".md").write_text("\n\n".join(f"{i}. [{s['transcript'][:8]}] COMMITMENT: {s['commitment']}\n   actions after it: {s['turn_actions_after']}\n   closing: {s['closing_text']!r}" for i, s in enumerate(sample, 1)), encoding="utf-8")
    print(json.dumps(summary))
    return 0


if __name__ == "__main__":
    sys.exit(main())
