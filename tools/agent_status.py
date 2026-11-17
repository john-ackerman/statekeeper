"""Read an agent's status from its transcript file and print it; with --observe also write it as a verified keyed state.

    python tools/agent_status.py <transcript.jsonl> [--observe] [--db out/statekeeper.db] [--task TICKET-1]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from statekeeper import SqliteStore  # noqa: E402
from statekeeper.writers.agent_status import observe_agent, status_from_transcript  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("transcript")
    ap.add_argument("--observe", action="store_true")
    ap.add_argument("--db", default="out/statekeeper.db")
    ap.add_argument("--task", default=None)
    args = ap.parse_args()
    p = status_from_transcript(args.transcript)
    if args.task:
        p["task"] = args.task
    print(json.dumps(p))
    if args.observe:
        out = observe_agent(SqliteStore(args.db), p)
        print("changed:", out["changed"] or "nothing")
    return 0


if __name__ == "__main__":
    sys.exit(main())
