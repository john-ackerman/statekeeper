"""Find duplicate proposals by meaning in a database (dry run by default) and list the pairs to judge.

    python tools/dedupe_report.py --db out/extract-sample.db [--threshold 0.95] [--apply]

Needs the local embedding server for vectors that are missing. With --apply it merges (rejects the newer duplicate with a reason, copies its evidence).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from statekeeper import SqliteStore  # noqa: E402
from statekeeper.dedupe import find_duplicates, merge_duplicates  # noqa: E402
from statekeeper.embed import HttpEmbedder, ensure_vectors  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--threshold", type=float, default=0.95)
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    s = SqliteStore(args.db)
    emb = HttpEmbedder()
    print("embedded:", ensure_vectors(s, emb))
    pairs = find_duplicates(s, emb.model, args.threshold)
    total = len(s.searchable_states())
    print(f"{len(pairs)} duplicate proposals among {total} searchable states at threshold {args.threshold}")
    for a, b, sim in sorted(pairs, key=lambda p: p[2]):
        sa, sb = s.get_state(a), s.get_state(b)
        print(f"{sim:.3f}  KEEP #{a} {sa.thing_name}: {sa.text[:110]}\n       DROP #{b} {sb.thing_name}: {sb.text[:110]}")
    if args.apply:
        print("merged:", merge_duplicates(s, pairs))
    return 0


if __name__ == "__main__":
    sys.exit(main())
