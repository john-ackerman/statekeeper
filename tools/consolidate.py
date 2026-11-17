"""One consolidation pass over a database, and the validation read of past passes.

    python tools/consolidate.py --db <db> [--dry-run]       # embeds new states, merges duplicates, links related ones; prints counts
    python tools/consolidate.py --db <db> --validate        # what the logs say about past merges

Bounded (50 merges, 200 links per pass), idempotent, reversible (`Store.reopen`), and parameterised by dedupe_threshold / related_threshold in param_history.
Not scheduled by anything. Back the database up first (tools/backup_db.py).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from statekeeper import SqliteStore  # noqa: E402
from statekeeper.consolidate import run_pass, validate  # noqa: E402
from statekeeper.embed import HttpEmbedder  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--validate", action="store_true")
    args = ap.parse_args()
    s = SqliteStore(args.db)
    if args.validate:
        print(json.dumps(validate(s)))
        return 0
    print(json.dumps(run_pass(s, HttpEmbedder(), apply=not args.dry_run)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
