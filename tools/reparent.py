"""File things under their parents (statekeeper.reparent: identifier prefixes, a PR's ticket, flag keys, name containment).

    python tools/reparent.py --db <db>                  # dry run: counts per rule and every planned move (child < parent [rule])
    python tools/reparent.py --db <db> --apply          # backs the database up (tools/backup_db.py) first, then makes the moves

A flag said two ways ("<key> flag" and "<key>") is merged first. Idempotent (a second run plans nothing); each move is one 'thing-reparented' event, and `Store.set_parent(id, previous, ...)` undoes it.
"""
from __future__ import annotations

import argparse
import collections
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from statekeeper import SqliteStore  # noqa: E402
from statekeeper.reparent import apply, merge_plan, plan  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--actor", default="tool:reparent")
    args = ap.parse_args()
    if args.apply:
        from statekeeper.proc import run
        r = run([sys.executable, str(ROOT / "tools" / "backup_db.py"), args.db], capture_output=True, text=True)
        if r.returncode != 0:
            print("backup failed; nothing moved", file=sys.stderr)
            return 1
        print(f"backup: {r.stdout.strip()}")
    s = SqliteStore(args.db)
    merges = merge_plan(s)
    for a, b in merges:
        print(f"  merge {a} -> {b}")
    if args.apply and merges:
        apply(s, [], actor=args.actor, merges=merges)
    moves = plan(s)
    print("planned:", dict(collections.Counter(m["rule"] for m in moves)), "total", len(moves), "merges", len(merges))
    for m in moves:
        print(f"  {m['child']} < {m['parent']} [{m['rule']}]" + (f" (was under {m['was']})" if m["was"] else ""))
    if args.apply:
        done = apply(s, moves, actor=args.actor)
        print(f"moved {len(done)} of {len(moves)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
