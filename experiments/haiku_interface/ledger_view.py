"""Prints a writer ledger for review: each applied change with its thing path, whether the thing is new, and the quote that justifies it.

    python experiments/haiku_interface/ledger_view.py --db <COPY> out/haiku-exp/ledger.jsonl
"""
import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1] / "src"))
sys.path.insert(0, str(HERE))

ap = argparse.ArgumentParser()
ap.add_argument("--db", required=True)
ap.add_argument("ledger")
a = ap.parse_args()
from statekeeper import SqliteStore  # noqa: E402
from tree import ThingTree  # noqa: E402
st = SqliteStore(a.db)
tr = ThingTree(st)
n = {"add": 0, "confirm": 0, "supersede": 0, "new_thing": 0, "proposals": 0, "rejected": 0}
for line in Path(a.ledger).read_text(encoding="utf-8").splitlines():
    rec = json.loads(line)
    n["proposals"] += len(rec.get("proposals", []))
    n["rejected"] += len(rec.get("rejected", []))
    print(f"--- {rec['when']} {rec['ref'][-12:]} ({rec.get('seconds')}s)")
    for x in rec.get("applied", []):
        n[x["op"]] += 1
        sid = x.get("new") or x.get("state")
        s = st.get_state(sid)
        path = tr.path(s.thing_id) if s and s.thing_id in tr.tt.things else (s.thing_name if s else "?")
        new = x.get("new_thing")
        n["new_thing"] += bool(new)
        print(f"  {x['op'].upper():<9} #{sid} [{path}]{' NEW(asked ' + x.get('asked_thing', '') + ')' if new else ''}\n      text : {(x.get('text') or x.get('old_text'))[:200]}\n      quote: {x['quote'][:200]}")
print(n)
