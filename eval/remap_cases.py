"""Carry cases.json over to a newer snapshot: every expected/also_ok id that is no longer standing there is replaced by what replaced it (its
superseded_by chain, or for a keyed live value the current value of the same thing and key). Stale ids are kept as they are. Prints what moved, so
each moved case can be re-verified by hand (gh for PR states) before the new file is trusted.

    python eval/remap_cases.py --db eval/snapshots/<new>.db --out eval/cases-<new>.json
"""
from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path

EVAL = Path(__file__).resolve().parent


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--cases", default=str(EVAL / "cases.json"))
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    db = sqlite3.connect(f"file:{Path(args.db).as_posix()}?mode=ro", uri=True)

    def row(i):
        return db.execute("SELECT id, thing_id, state_key, status, superseded_by FROM states WHERE id = ?", (i,)).fetchone()

    def current(i):
        seen = set()
        r = row(i)
        while r and r[0] not in seen:
            seen.add(r[0])
            if r[3] in ("active", "proposed") and r[4] is None:
                return r[0]
            if r[4]:
                r = row(r[4])
                continue
            if r[2]:                                   # a keyed value closed without a pointer: the thing's current value for that key
                c = db.execute("SELECT id FROM states WHERE thing_id = ? AND state_key = ? AND status = 'active' AND superseded_by IS NULL "
                               "ORDER BY id DESC LIMIT 1", (r[1], r[2])).fetchone()
                return c[0] if c else None
            return None
        return None

    data = json.loads(Path(args.cases).read_text(encoding="utf-8"))
    moved = 0
    for case in data["cases"]:
        changes = []
        for g in case.get("expect", []):
            ids = g if isinstance(g, list) else g.get("ids", [])
            for k, i in enumerate(list(ids)):
                c = current(i)
                if c != i:
                    changes.append(f"{i}->{c}")
                    if c is not None:
                        ids[k] = c
            if isinstance(g, list):
                g[:] = [x for x in dict.fromkeys(ids) if current(x) == x]
        if case.get("also_ok"):
            new = []
            for i in case["also_ok"]:
                c = current(i)
                if c != i:
                    changes.append(f"also {i}->{c}")
                if c is not None:
                    new.append(c)
            case["also_ok"] = list(dict.fromkeys(new))
        if changes:
            moved += 1
            case["remapped"] = ", ".join(changes)
            print(f"{case['id']}: {case['remapped']}")
    data["snapshot"] = f"snapshots/{Path(args.db).name}"
    data["_remapped_from"] = "cases.json (original snapshot); ids remapped by eval/remap_cases.py, moved cases re-verified by hand (see 'reverified')"
    Path(args.out).write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"{moved} cases had ids that moved; wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
