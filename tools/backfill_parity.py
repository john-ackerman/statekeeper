"""Backfill shadow injections for turns already recorded: re-surface each stored prompt (what statekeeper would inject NOW) and read what Legacy injected from
the session transcript by timestamp window (turn start - 10 s to the next turn's start - 10 s). Read-only on transcripts; writes into the shadow store.

    python tools/backfill_parity.py [--db ~/.claude/statekeeper/shadow.db]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "adapters" / "claude_code"))

import shadow_hook  # noqa: E402
from statekeeper import SqliteStore, surface  # noqa: E402
from statekeeper.embed import HttpEmbedder  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(Path.home() / ".claude" / "statekeeper" / "shadow.db"))
    args = ap.parse_args()
    store = SqliteStore(args.db)
    have = {i[0] for i in store.injections()}
    emb = HttpEmbedder(timeout=2.0)
    by_session: dict[str, list[tuple[str, float, str]]] = {}
    for ref, session, _created, text in store.turn_texts("prompt"):
        try:
            by_session.setdefault(session or "", []).append((ref, int(ref.rsplit(":", 1)[1]) / 1000.0, text))
        except (ValueError, IndexError):
            continue
    done = 0
    for session, turns in by_session.items():
        paths = list((Path.home() / ".claude" / "projects").glob(f"*/{session}.jsonl"))
        turns.sort(key=lambda t: t[1])
        for i, (ref, started, text) in enumerate(turns):
            if ref in have:
                continue
            store.record_injection(ref, "statekeeper", "UserPromptSubmit", "concept_map", surface(store, text, emb).block)
            if paths:
                end = turns[i + 1][1] - 10 if i + 1 < len(turns) else float("inf")
                import json as _json
                from datetime import datetime
                for line in open(paths[0], "rb").read().decode("utf-8", "replace").splitlines():
                    if '"hook_additional_context"' not in line:
                        continue
                    try:
                        d = _json.loads(line)
                        a = d["attachment"]
                        at = datetime.fromisoformat(d["timestamp"].replace("Z", "+00:00")).timestamp()
                    except (ValueError, KeyError):
                        continue
                    if a.get("type") == "hook_additional_context" and str(a.get("hookName")).startswith("UserPromptSubmit") and started - 10 <= at < end:
                        c = a.get("content")
                        body = "\n".join(c) if isinstance(c, list) else str(c or "")
                        store.record_injection(ref, "legacy", a["hookName"], shadow_hook.classify(body), body[:60000])
            done += 1
    print(f"backfilled {done} turns")
    return 0


if __name__ == "__main__":
    sys.exit(main())
