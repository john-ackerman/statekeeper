"""Run the automated miss detectors over the person's past messages (from a legacy export) against a copy of a database; report counts and a sample to judge.

    python tools/misses_report.py [--export out/legacy-export.json] [--db out/statekeeper.db] [--threshold 0.93] [--hours 24]

Needs the local embedding server. Writes into out/misses-try.db (a copy). Measured counts only; the sample is for a human to judge.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from statekeeper import SqliteStore  # noqa: E402
from statekeeper.embed import HttpEmbedder, ensure_vectors  # noqa: E402
from statekeeper.misses import log_misses  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--export", default="out/legacy-export.json")
    ap.add_argument("--db", default="out/statekeeper.db")
    ap.add_argument("--threshold", type=float, default=0.93)
    ap.add_argument("--hours", type=float, default=24)
    args = ap.parse_args()
    copy = ROOT / "out" / "misses-try.db"
    shutil.copyfile(args.db, copy)
    s = SqliteStore(copy)
    emb = HttpEmbedder()
    ensure_vectors(s, emb)
    inputs = json.loads(Path(args.export).read_text(encoding="utf-8"))["inputs"]
    for i in inputs:
        s.db.execute("INSERT OR REPLACE INTO turn_texts (turn_ref, kind, session_id, created_utc, text) VALUES (?,?,?,?,?)", (f"in-{i['id']}", "prompt", "user", i["created"], i["content"]))
    counts = log_misses(s, emb, threshold=args.threshold, hours=args.hours)
    print(json.dumps({"inputs": len(inputs), "threshold": args.threshold, "hours": args.hours, **counts}))
    texts = {r[0]: r[3] for r in s.turn_texts("prompt")}
    for o in [o for o in s.outcomes(kind="missed") if o.source == "restated"][:12]:
        d = json.loads(o.detail)
        print(f"\n[{d['cosine']}] EARLIER: {texts.get(d['repeats'], '')[:150]!r}\n        LATER:   {texts.get(o.turn_ref, '')[:150]!r}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

