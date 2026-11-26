"""Old vs new per set (original 55 cases.json cases apart from the added ones) and per category, from eval_reader's JSON.

    python experiments/haiku_interface/summarize.py out/haiku-exp/reader-all.json
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "eval"))
import run_eval  # noqa: E402

d = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
for cf, s in d["sets"].items():
    cases = {c["id"]: c for c in json.loads(Path(cf).read_text(encoding="utf-8"))["cases"]}
    groups = {"all": lambda r: True}
    if cf.endswith("cases.json"):
        groups = {"original 55": lambda r: not cases[r["id"]].get("added"), "added": lambda r: cases[r["id"]].get("added")}
    for g, f in groups.items():
        o = [r for r in s["old_rows"] if f(r)]
        n = [r for r in s["new_rows"] if f(r)]
        so, sn = run_eval.summarize(o), run_eval.summarize(n)
        print(f"{cf} [{g}] {len(o)} cases: hit@1 {so['hit@1']}->{sn['hit@1']} hit@all {so['hit@all']}->{sn['hit@all']} lenient {so['lenient_hit']}->{sn['lenient_hit']} "
              f"empty {so['empty_rate']}->{sn['empty_rate']} noise/prompt {so['noise_per_prompt']}->{sn['noise_per_prompt']} stale cases {so['stale_leak_cases']}->{sn['stale_leak_cases']} "
              f"tokens {so['mean_tokens']}->{sn['mean_tokens']}")
        bo, bn = run_eval.by_category(o), run_eval.by_category(n)
        for c in bo:
            print(f"    {c:<16} hit@all {bo[c]['hit@all']}->{bn[c]['hit@all']}  hit@1 {bo[c]['hit@1']}->{bn[c]['hit@1']}  ({bo[c]['cases']})")
    both = sum(1 for a, b in zip(s["old_rows"], s["new_rows"]) if a["hit@all"] and b["hit@all"])
    only_o = [a["id"] for a, b in zip(s["old_rows"], s["new_rows"]) if a["hit@all"] and not b["hit@all"]]
    only_n = [a["id"] for a, b in zip(s["old_rows"], s["new_rows"]) if b["hit@all"] and not a["hit@all"]]
    print(f"    both hit {both}; old only {len(only_o)} {only_o}; new only {len(only_n)} {only_n}")
