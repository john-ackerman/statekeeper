"""Combination policies from eval_reader's JSON: old only, new only, new only when old is empty (fallback), and both blocks (union).

    python experiments/haiku_interface/policies.py out/haiku-exp/reader-all.json
"""
import json
import sys
from pathlib import Path

d = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
for cf, s in d["sets"].items():
    cases = {c["id"]: c for c in json.loads(Path(cf).read_text(encoding="utf-8"))["cases"]}
    pairs = [(o, n) for o, n in zip(s["old_rows"], s["new_rows"]) if not cases[o["id"]].get("added")]
    k = len(pairs)

    def pol(name, pick):
        hits = sum(1 for o, n in pairs if any(r["hit@all"] for r in pick(o, n)))
        noise = sum(sum(len(r["noise"]) for r in pick(o, n)) for o, n in pairs) / k
        states = sum(sum(r["n_shown"] for r in pick(o, n)) for o, n in pairs) / k
        haiku_calls = sum(1 for o, n in pairs if any(r is n for r in pick(o, n)))
        print(f"  {name:<22} hit@all {hits / k:.3f}  noise/prompt {noise:.2f}  states/prompt {states:.2f}  Haiku shown on {haiku_calls}/{k}")
    print(f"{cf} ({k} cases)")
    pol("old", lambda o, n: [o])
    pol("new", lambda o, n: [n])
    pol("new when old empty", lambda o, n: [n] if o["empty"] else [o])
    pol("union (both blocks)", lambda o, n: [o, n])
