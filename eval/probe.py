"""Why did a prompt miss? For one prompt: each expected/stale state's cosine to the input, its best stored-request score, the rare words it shares,
and the top states by cosine. Read-only against a copy of the snapshot.

    python eval/probe.py "<prompt>" [--ids 1,2,3] [--top 12]
"""
from __future__ import annotations

import argparse
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from statekeeper import SqliteStore  # noqa: E402
from statekeeper.embed import HttpEmbedder  # noqa: E402
from statekeeper.retrieve import _dot, _unit, split_identifiers, terms, words  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("prompt")
    ap.add_argument("--ids", default="")
    ap.add_argument("--top", type=int, default=12)
    ap.add_argument("--db", default=str(ROOT / "eval" / "snapshots" / "shadow-20261120-092640.db"))
    a = ap.parse_args()
    sys.path.insert(0, str(ROOT / "eval"))
    from run_eval import copy_db
    tmp = Path(tempfile.mkdtemp(prefix="sk-probe-")) / "p.db"
    copy_db(Path(a.db), tmp)
    store = SqliteStore(str(tmp))
    emb = HttpEmbedder(timeout=5.0)
    q = _unit(emb.embed([split_identifiers(a.prompt)])[0])
    pool = {s.id: s for s in store.searchable_states()}
    cos = {v["owner_id"]: _dot(q, _unit(v["vector"])) for v in store.vectors("state", emb.model) if v["owner_id"] in pool}
    asked = terms(a.prompt)
    df: dict[str, int] = {}
    for s in pool.values():
        for w in terms(f"{s.thing_name} {s.text}"):
            df[w] = df.get(w, 0) + 1
    print("asked terms (df):", {w: df.get(w, 0) for w in sorted(asked)}, "rare cut", max(3, len(pool) // 100))
    for sid in [int(x) for x in a.ids.split(",") if x]:
        s = pool.get(sid)
        if s is None:
            print(f"#{sid}: not searchable")
            continue
        print(f"#{sid} cos {cos.get(sid, 0):.3f} shared {sorted(asked & terms(s.thing_name + ' ' + s.text))} | {s.thing_name}: {s.text[:110]}")
    print("-- top by cosine")
    for sid, c in sorted(cos.items(), key=lambda x: -x[1])[:a.top]:
        s = pool[sid]
        print(f"#{sid} {c:.3f} {s.volatility}/{s.status} | {s.thing_name}: {s.text[:100]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
