"""Verified backup of a statekeeper database using SQLite's online backup, then a check that the copy matches (row counts per table, integrity_check).

    python tools/backup_db.py <db> [--to <dir>]      prints the backup path; exits non-zero if the copy does not match
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
import time
from pathlib import Path


def counts(path: str) -> dict[str, int]:
    db = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    names = [r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
    out = {n: db.execute(f'SELECT COUNT(*) FROM "{n}"').fetchone()[0] for n in names}
    db.close()
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("db")
    ap.add_argument("--to", default=None)
    args = ap.parse_args()
    src = Path(args.db)
    dest_dir = Path(args.to) if args.to else src.parent / "backups"
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"{src.stem}-{time.strftime('%Y%m%d-%H%M%S')}{src.suffix}"
    a, b = sqlite3.connect(str(src)), sqlite3.connect(str(dest))
    a.backup(b)
    b.close()
    a.close()
    ok = sqlite3.connect(str(dest)).execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    same = counts(str(src)) == counts(str(dest))
    print(dest)
    print(f"integrity_check ok: {ok}; table row counts match: {same}", file=sys.stderr)
    return 0 if ok and same else 1


if __name__ == "__main__":
    sys.exit(main())
