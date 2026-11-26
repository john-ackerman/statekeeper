"""Old (the live hook's prompt_context) vs new (Haiku reader) on the same store copy and case files, graded by eval/run_eval.grade.

    python experiments/haiku_interface/eval_reader.py --db <copy> --cases eval/cases.json [--cases ...] --out out/haiku-exp/reader-eval.json

The old path gets only the prompt (as the live hook does on a fresh session); the new one also gets the case's 'context' turns when it has them.
Reader calls run 3 at a time; per-call seconds are each call's own wall-clock.
"""
from __future__ import annotations

import argparse
import json
import shutil
import statistics
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT / "eval"))
sys.path.insert(0, str(HERE))

import run_eval  # noqa: E402


def ordered(items) -> list:
    return run_eval.ordered_states(items)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--cases", action="append", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--only", default="")
    args = ap.parse_args()
    tmp = Path(tempfile.mkdtemp(prefix="sk-hreval-"))
    try:
        db_copy = tmp / "eval.db"
        run_eval.copy_db(Path(args.db), db_copy)
        home = tmp / "home"
        home.mkdir()
        store, embedder, run_hook, _ = run_eval.make_hook_runner(db_copy, home)
        if embedder is None:
            print("WARNING: no embedding server: the old path falls back to names only")
        from reader import Reader
        rd = Reader(store)
        print(f"tree: {len(rd.tree_text)} chars, {len(rd.tree.keep)} things; recent list {len(rd.recent_text)} chars")
        out: dict = {"db": args.db, "embedder": embedder is not None, "sets": {}}
        for cf in args.cases:
            data = json.loads(Path(cf).read_text(encoding="utf-8"))
            cases = [c for c in data["cases"] if not args.only or c["id"] in args.only.split(",")]
            problems = run_eval.validate({"cases": cases}, store)
            old_rows, new_rows = [], []
            for c in cases:
                shown, block, ms = run_hook(c["prompt"])
                r = run_eval.grade(c, shown, store, block)
                r["ms"] = ms
                r["has_stale"] = bool(c.get("stale"))
                old_rows.append(r)

            with ThreadPoolExecutor(args.workers) as ex:
                picks = list(ex.map(lambda c: rd.pick(c["prompt"], c.get("context") or []), cases))

            def one(c, p):
                res = rd.read(c["prompt"], c.get("context") or [], picked=p)
                r = run_eval.grade(c, ordered(res.items), store, res.block)
                r.update({"has_stale": bool(c.get("stale")), "seconds": res.seconds, "in_tok": res.input_tokens, "out_tok": res.output_tokens,
                          "cost": res.cost_usd, "picked": res.picked, "resolved": res.resolved, "unresolved": res.unresolved, "terms": res.terms,
                          "ancestors": res.ancestors, "error": res.error, "block": res.block})
                return r
            new_rows = [one(c, p) for c, p in zip(cases, picks)]
            for o, n in zip(old_rows, new_rows):
                print(f"{o['id']:<36} old {'HIT ' if o['hit@all'] else ('EMPT' if o['empty'] else 'miss')} r{o['rank']} n{o['n_shown']} noise{len(o['noise'])} | "
                      f"new {'HIT ' if n['hit@all'] else ('EMPT' if n['empty'] else 'miss')} r{n['rank']} n{n['n_shown']} noise{len(n['noise'])} {n['seconds']}s "
                      f"picked {n['resolved']}{' UNRESOLVED ' + str(n['unresolved']) if n['unresolved'] else ''}{' ERR ' + n['error'] if n['error'] else ''}")
            secs = [n["seconds"] for n in new_rows if not n["error"]]
            summ = {"old": run_eval.summarize(old_rows), "new": run_eval.summarize(new_rows),
                    "old_by_category": run_eval.by_category(old_rows), "new_by_category": run_eval.by_category(new_rows),
                    "new_latency_s": {"median": round(statistics.median(secs), 2) if secs else None, "p90": sorted(secs)[int(0.9 * (len(secs) - 1))] if secs else None,
                                      "max": max(secs) if secs else None},
                    "old_ms_median": statistics.median(r["ms"] for r in old_rows) if old_rows else None,
                    "new_tokens_in_mean": round(statistics.mean(n["in_tok"] for n in new_rows), 0) if new_rows else 0,
                    "new_tokens_out_mean": round(statistics.mean(n["out_tok"] for n in new_rows), 0) if new_rows else 0,
                    "new_cost_total": round(sum(n["cost"] for n in new_rows), 4), "errors": sum(1 for n in new_rows if n["error"]), "case_problems": problems}
            out["sets"][cf] = {"summary": summ, "old_rows": old_rows, "new_rows": new_rows}
            keys = ("hit@1", "hit@all", "lenient_hit", "empty_rate", "mean_states", "noise_per_prompt", "noise_share", "stale_leak_cases", "mean_tokens")
            print(f"\n== {cf} ({len(cases)} cases, {len(problems)} case problems)")
            for k in keys:
                print(f"   {k:<18} old {summ['old'][k]:<8} new {summ['new'][k]}")
            print(f"   latency new median {summ['new_latency_s']['median']}s p90 {summ['new_latency_s']['p90']}s; old median {summ['old_ms_median']} ms; "
                  f"tokens in/out {summ['new_tokens_in_mean']}/{summ['new_tokens_out_mean']}; cost ${summ['new_cost_total']}; errors {summ['errors']}\n")
        Path(args.out).write_text(json.dumps(out, indent=1, default=str), encoding="utf-8")
        print("wrote", args.out)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
