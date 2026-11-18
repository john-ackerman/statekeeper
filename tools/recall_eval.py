"""Recall regression: does the live selection inject the expected state for each prompt, and how much else does it inject?

    python tools/recall_eval.py <cases.json> [--db PATH] [--json OUT] [--control N] [--verbose]

cases.json: {"skip_state_ids": [..], "cases": [{"id", "prompt", "expect": [regex, ...]}]}. A case is a HIT when any injected line
("<thing>: <text>") matches any of its patterns. NOISE on a case = injected states matching none of its patterns.
--control N also replays the last N person-typed prompts recorded in the store (turn_texts) and reports how many states each would inject:
a precision guard for prompts that are not in the set (a change that helps the set by injecting more everywhere shows up there).
Selection uses the live hook's parameters (adapters/claude_code/shadow_hook.py) and writes no outcomes. Read-only apart from embedding
vectors of states that have none.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "adapters" / "claude_code"))

from statekeeper import SqliteStore, surface  # noqa: E402
from statekeeper.embed import HttpEmbedder  # noqa: E402


def live_params():
    import shadow_hook
    return dict(floor=shadow_hook.LIVE_FLOOR, max_things=shadow_hook.LIVE_MAX_THINGS, budget=shadow_hook.LIVE_BUDGET,
                exclude_kinds=("agent", "person"), exclude_names=("working note",), max_states=shadow_hook.LIVE_MAX_STATES)


def run_one(store, embedder, prompt: str, skip: frozenset):
    out = surface(store, prompt, embedder, skip_state_ids=skip, **live_params())
    return [(s.id, f"{s.thing_name}: {s.text}") for it in out.items for s in it.states]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("cases")
    ap.add_argument("--db", default=os.environ.get("STATEKEEPER_DB") or str(Path.home() / ".claude" / "statekeeper" / "shadow.db"))
    ap.add_argument("--json", default="")
    ap.add_argument("--control", type=int, default=0)
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()
    data = json.loads(Path(args.cases).read_text(encoding="utf-8"))
    store = SqliteStore(args.db)
    try:
        embedder = HttpEmbedder(timeout=5.0)
        embedder.embed(["ping"])
    except Exception:
        embedder = None
        print("no embedding server: names only")
    skip = frozenset(data.get("skip_state_ids", []))
    rows = []
    for c in data["cases"]:
        shown = run_one(store, embedder, c["prompt"], skip)
        pats = [re.compile(p) for p in c["expect"]]
        good = [sid for sid, line in shown if any(p.search(line) for p in pats)]
        rows.append({"id": c["id"], "hit": bool(good), "shown": len(shown), "noise": len(shown) - len(good), "states": [sid for sid, _ in shown]})
        mark = "HIT " if good else "miss"
        print(f"{mark} {c['id']:<28} shown {len(shown)} noise {len(shown) - len(good)}  {c.get('note', '')}")
        if args.verbose:
            for sid, line in shown:
                print(f"       {'+' if sid in good else '-'} #{sid} {' '.join(line.split())[:150]}")
    hits = sum(r["hit"] for r in rows)
    summary = {"cases": len(rows), "hits": hits, "hit_rate": round(hits / max(1, len(rows)), 3),
               "noise_per_prompt": round(statistics.mean(r["noise"] for r in rows), 2) if rows else 0}
    if args.control:
        prompts = [t for _r, _s, _c, t in store.turn_texts("prompt") if t.strip() and not t.lstrip().startswith("<")][-args.control:]
        counts = [len(run_one(store, embedder, p, skip)) for p in prompts]
        summary.update({"control_prompts": len(counts), "control_injected_mean": round(statistics.mean(counts), 2) if counts else 0,
                        "control_quiet_share": round(sum(1 for n in counts if n == 0) / max(1, len(counts)), 3)})
        if args.json:
            summary["control_states"] = [run_one(store, embedder, p, skip) for p in prompts] if args.verbose else None
    print(json.dumps({k: v for k, v in summary.items() if k != "control_states"}))
    if args.json:
        Path(args.json).write_text(json.dumps({"summary": summary, "rows": rows}, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
