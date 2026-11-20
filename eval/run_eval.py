"""Retrieval eval for Statekeeper: does the real per-prompt recall path put the CURRENT state of what is being asked about in front of the model?

    python eval/run_eval.py [--db PATH] [--cases eval/cases.json] [--json eval/results.json] [--mode hook|recall|both]
                            [--gh] [--verbose] [--only ID,ID] [--telemetry]

What it runs (the same code the hooks run, not a lookalike):
  hook mode    adapters/claude_code/shadow_hook.prompt_context(), the function the UserPromptSubmit inject hook calls, with the hook's own parameters
               (floor LIVE_FLOOR, max things/states, budget, excluded kinds, 'working note' excluded), a fresh session (no recent-skip, no topic carry-over),
               and the hook's 4000-char cap. It wraps statekeeper.surface only to read which state ids came back; behaviour is unchanged.
  recall mode  tools/sk.py recall(): the looser selection agents get from `sk recall` (5 things / 8 states / 4000 chars).
  core         session_block(budget=16000), what the SessionStart hook injects: reported as size, working-note age, and (with --gh) how many 'Tracked now'
               PRs are already merged/closed on GitHub. Each case also records whether its expected states are reachable from that block.

Read-only against the store: the database is copied (SQLite online backup) to a temp file first, STATEKEEPER_HOME points at a temp dir so the hook's
log/state files land there, and observe/cue side effects are disabled. Nothing in ~/.claude/statekeeper is written. The tool-cue append at the end of
inject_hook.main is not exercised (it is not recall and it writes a shown-file); the 4000-char cap is.

Metrics per case: hit@1/@3/@all (any expected state among the first k injected states), MRR, full (every expected group satisfied), injected states,
~tokens (chars/4), noise (injected states that are in no expected group), stale leakage (a state listed in the case's 'stale', or any superseded/closed
state), and whether the prompt injected nothing.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sqlite3
import statistics
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EVAL = Path(__file__).resolve().parent
DEFAULT_SNAPSHOT = EVAL / "snapshots" / "shadow-20261120-092640.db"
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "adapters" / "claude_code"))


from statekeeper.parity import tokens  # noqa: E402  (chars/4, the estimate the store's own parity report uses)


def copy_db(src: Path, dest: Path) -> None:
    a = sqlite3.connect(f"file:{src.as_posix()}?mode=ro", uri=True)
    b = sqlite3.connect(dest)
    a.backup(b)
    a.close()
    b.close()


def groups_of(case: dict) -> list[dict]:
    out = []
    for g in case.get("expect", []):
        out.append({"ids": set(g), "re": []} if isinstance(g, list) else {"ids": set(g.get("ids", [])), "re": [re.compile(p, re.I) for p in g.get("re", [])]})
    return out


def state_lines(states: list) -> dict[int, str]:
    return {s.id: f"{s.thing_name}: {s.text}" for s in states}


def ordered_states(items) -> list:
    """Injected states in the order they appear in the block: per thing, the keyed (current-value) states first, then the rest."""
    out = []
    for it in items:
        out += [s for s in it.states if s.state_key] + [s for s in it.states if not s.state_key]
    return out


def grade(case: dict, shown: list, store, block: str) -> dict:
    groups = groups_of(case)
    lines = state_lines(shown)
    ids = [s.id for s in shown]
    wanted = set().union(*[g["ids"] for g in groups]) if groups else set()
    sat = []
    for g in groups:
        ok = bool(g["ids"] & set(ids)) or any(p.search(l) for p in g["re"] for l in lines.values())
        sat.append(ok)
    rank = next((i + 1 for i, sid in enumerate(ids) if sid in wanted), 0)
    stale_ids = set(case.get("stale", []))
    hard = []
    for sid in ids:
        st = store.get_state(sid)
        if st is None or st.superseded_by or st.status not in ("active", "proposed"):
            hard.append(sid)
    leaked = [sid for sid in ids if sid in stale_ids]
    ok = set(case.get("also_ok", []))
    noise = [sid for sid in ids if sid not in wanted and sid not in stale_ids and sid not in ok]
    lenient = next((i + 1 for i, sid in enumerate(ids) if sid in wanted or sid in ok), 0)
    proposed = [sid for sid in ids if (store.get_state(sid) and store.get_state(sid).status == "proposed")]
    return {"id": case["id"], "category": case["category"], "prompt": case["prompt"], "shown": ids, "n_shown": len(ids), "chars": len(block), "tokens": tokens(block),
            "empty": not ids, "hit@1": rank == 1, "hit@3": 0 < rank <= 3, "hit@all": rank > 0, "rank": rank, "mrr": (1 / rank) if rank else 0.0, "lenient_hit": lenient > 0, "lenient_hit@1": lenient == 1, "lenient_mrr": (1 / lenient) if lenient else 0.0,
            "full": bool(sat) and all(sat), "groups_satisfied": f"{sum(sat)}/{len(sat)}", "noise": noise, "stale_leaked": leaked, "hard_stale": hard, "proposed_shown": proposed,
            "lines": {str(i): lines[i][:160] for i in ids}}


def make_hook_runner(db_copy: Path, home: Path):
    os.environ["STATEKEEPER_HOME"] = str(home)
    os.environ["STATEKEEPER_NO_OBSERVE_IN_TESTS"] = "1"
    os.environ["STATEKEEPER_DB"] = str(db_copy)
    import statekeeper
    import shadow_hook
    from statekeeper import SqliteStore
    from statekeeper.embed import HttpEmbedder
    store = SqliteStore(str(db_copy))
    try:
        embedder = HttpEmbedder(timeout=5.0)
        embedder.embed(["ping"])
    except Exception:
        embedder = None
    real = statekeeper.surface
    box: dict = {}

    def spy(*a, **k):
        out = real(*a, **k)
        box["out"] = out
        return out
    statekeeper.surface = spy

    def run(prompt: str):
        box.clear()
        state_file = home / "shadow-state.json"
        state_file.write_text("{}", encoding="utf-8")                      # a fresh session for every prompt
        event = {"hook_event_name": "UserPromptSubmit", "prompt": prompt, "session_id": "eval"}
        t0 = time.time()
        _rec, block = shadow_hook.prompt_context(event, store, embedder, {}, state_file, home)
        ms = int((time.time() - t0) * 1000)
        out = box.get("out")
        block = block[:4000]                                                # inject_hook.CAP
        return (ordered_states(out.items) if out else []), block, ms
    return store, embedder, run, shadow_hook


def make_recall_runner(store, embedder):
    sys.path.insert(0, str(ROOT / "tools"))
    import sk
    from statekeeper import surface

    def run(prompt: str):
        t0 = time.time()
        out = surface(store, prompt, embedder, floor=store.get_param("relevance_floor", 0.72) - 0.02, max_things=5, max_states=8, budget=4000, exclude_names=(sk.NOTE_THING,))
        return ordered_states(out.items), out.block, int((time.time() - t0) * 1000)
    return run


def session_state_ids(store) -> set[int]:
    """Ids of the states whose text the SessionStart block carries WITHOUT the hand-written note (core, topic index, tracked now, what's running):
    an answer the model already has at session start is not a recall miss. Matched by text (whitespace-normalised), since the block has no ids."""
    import statekeeper.session as sess
    was, sess.WITH_NOTE = sess.WITH_NOTE, False
    try:
        block = " ".join(sess.session_block(store, budget=16000).split())
    finally:
        sess.WITH_NOTE = was
    return {s.id for s in store.searchable_states() if len(s.text) >= 20 and " ".join(s.text.split())[:200] in block}


def add_core_credit(rows: list[dict], cases: list[dict], in_session: set[int]) -> None:
    """core_hit / core_full: the hook's result OR the expected state already being in the session block. Reported beside, never instead of, the old numbers."""
    by_id = {c["id"]: c for c in cases}
    for r in rows:
        groups = groups_of(by_id[r["id"]])
        r["in_session_block"] = sorted(set().union(*[g["ids"] for g in groups] or [set()]) & in_session)
        r["core_hit"] = bool(r["hit@all"] or r["in_session_block"])
        r["core_full"] = bool(groups) and all(bool(g["ids"] & (set(r["shown"]) | in_session)) or any(p.search(l) for p in g["re"] for l in r["lines"].values())
                                              for g in groups)


def summarize(rows: list[dict]) -> dict:
    def mean(key):
        return round(statistics.mean(r[key] for r in rows), 3) if rows else 0.0
    toks = sorted(r["tokens"] for r in rows)
    return {"cases": len(rows), "hit@1": mean("hit@1"), "hit@3": mean("hit@3"), "hit@all": mean("hit@all"), "mrr": mean("mrr"), "full": mean("full"),
            "lenient_hit": mean("lenient_hit"), "lenient_hit@1": mean("lenient_hit@1"), "lenient_mrr": mean("lenient_mrr"),
            "empty_rate": mean("empty"), "mean_states": round(statistics.mean(r["n_shown"] for r in rows), 2) if rows else 0,
            "mean_tokens": round(statistics.mean(toks), 1) if toks else 0, "p90_tokens": toks[int(0.9 * (len(toks) - 1))] if toks else 0,
            "noise_per_prompt": round(statistics.mean(len(r["noise"]) for r in rows), 2) if rows else 0,
            "noise_share": round(sum(len(r["noise"]) for r in rows) / max(1, sum(r["n_shown"] for r in rows)), 3),
            "stale_leak_rate": round(sum(1 for r in rows if r["stale_leaked"]) / max(1, sum(1 for r in rows if r.get("has_stale"))), 3),
            "stale_leak_cases": sum(1 for r in rows if r["stale_leaked"]), "stale_checked_cases": sum(1 for r in rows if r.get("has_stale")),
            "hard_stale_states": sum(len(r["hard_stale"]) for r in rows), "proposed_states_shown": sum(len(r["proposed_shown"]) for r in rows)}


def by_category(rows: list[dict]) -> dict:
    cats: dict = {}
    for r in rows:
        cats.setdefault(r["category"], []).append(r)
    return {c: {k: v for k, v in summarize(rs).items() if k in ("cases", "hit@1", "hit@3", "hit@all", "mrr", "full", "lenient_hit", "empty_rate", "noise_share", "stale_leak_cases")} for c, rs in sorted(cats.items())}


def session_eval(store, shadow_hook, gh: bool, snapshot_taken: str) -> dict:
    from statekeeper.session import session_block
    block = session_block(store, budget=16000)
    out = {"chars": len(block), "tokens": tokens(block), "truncated": block.endswith("[truncated]")}
    m = re.search(r"Working note \(last changed ([\d\- :]+), verified ([\d\- :]*)\)", block)
    if m:
        try:
            changed = datetime.strptime(m.group(1).strip(), "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc)
            now = datetime.now(timezone.utc)
            out["working_note_last_changed"] = m.group(1)
            out["working_note_age_hours"] = round((now - changed).total_seconds() / 3600, 1)
        except ValueError:
            pass
        i = block.index("Working note")
        j = block.index("Tracked now") if "Tracked now" in block else len(block)
        out["working_note_chars"] = j - i
    sections = {"core": block.split("\n\nWorking note")[0], "tracked": block.split("Tracked now")[-1] if "Tracked now" in block else ""}
    out["core_chars"] = len(sections["core"])
    out["tracked_chars"] = len(sections["tracked"])
    prs = re.findall(r"^- (VirtuousV2|VirtuousWorkspace) PR (\d+): ([^;]+)", block, re.M)
    out["tracked_prs"] = len(prs)
    if gh:
        mism = []
        for repo, num, said in prs:
            cwd = f"C:/repos/{repo}"
            r = subprocess.run(["gh", "pr", "view", num, "--json", "state,isDraft,mergedAt"], capture_output=True, text=True, encoding="utf-8", cwd=cwd, timeout=60)
            if r.returncode != 0:
                continue
            d = json.loads(r.stdout)
            truth = "merged" if d["state"] == "MERGED" else "closed" if d["state"] == "CLOSED" else ("draft, open" if d["isDraft"] else "open")
            if not said.strip().startswith(truth.split(",")[0]) or ("draft" in said) != ("draft" in truth):
                mism.append({"pr": f"{repo} #{num}", "tracked_says": said.strip(), "github_says": truth, "merged_at": d.get("mergedAt")})
        out["tracked_pr_mismatches_vs_github"] = mism
        note_ids = sorted(set(re.findall(r"#(\d{4})\b", block.split("Tracked now")[0])))
        gh_note = {}
        for n in note_ids:
            r = subprocess.run(["gh", "pr", "view", n, "--json", "state"], capture_output=True, text=True, encoding="utf-8", cwd="C:/repos/VirtuousV2", timeout=60)
            if r.returncode == 0:
                gh_note[n] = json.loads(r.stdout)["state"].lower()
        out["working_note_pr_refs_github_state"] = gh_note
    return out


def telemetry(db: Path) -> dict:
    d = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
    rows = d.execute("SELECT length(text) FROM shadow_injections WHERE source='statekeeper' AND kind='concept_map'").fetchall()
    sizes = sorted(r[0] for r in rows)
    empty = sum(1 for s in sizes if s == 0)
    outc = dict(d.execute("SELECT kind, COUNT(*) FROM outcomes GROUP BY kind").fetchall())
    surfaced_turns = d.execute("SELECT COUNT(DISTINCT turn_ref) FROM outcomes WHERE kind='surfaced' AND turn_ref IS NOT NULL").fetchone()[0]
    used_turns = d.execute("SELECT COUNT(DISTINCT turn_ref) FROM outcomes WHERE kind='used' AND turn_ref IS NOT NULL").fetchone()[0]
    both = d.execute("SELECT COUNT(DISTINCT a.turn_ref) FROM outcomes a JOIN outcomes b ON a.turn_ref=b.turn_ref AND b.kind='used' WHERE a.kind='surfaced'").fetchone()[0]
    d.close()
    n = len(sizes)
    return {"live_injections_logged": n, "empty_share": round(empty / max(1, n), 3), "mean_chars": round(statistics.mean(sizes), 0) if sizes else 0,
            "p50_chars": sizes[n // 2] if n else 0, "p90_chars": sizes[int(0.9 * (n - 1))] if n else 0, "outcomes": outc,
            "turns_with_surfaced": surfaced_turns, "turns_where_a_surfaced_state_was_used": both,
            "turn_level_use_rate": round(both / max(1, surfaced_turns), 3)}


def validate(case_file: dict, store) -> list[str]:
    problems = []
    live = {s.id for s in store.searchable_states()}
    for c in case_file["cases"]:
        for g in c.get("expect", []):
            for i in (g if isinstance(g, list) else g.get("ids", [])):
                if i not in live:
                    st = store.get_state(i)
                    problems.append(f"{c['id']}: expected #{i} is not retrievable ({'missing' if st is None else 'superseded by #' + str(st.superseded_by) if st.superseded_by else st.status})")
        for i in c.get("stale", []):
            if store.get_state(i) is None:
                problems.append(f"{c['id']}: stale #{i} does not exist")
    return problems


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(DEFAULT_SNAPSHOT))
    ap.add_argument("--cases", default=str(EVAL / "cases.json"))
    ap.add_argument("--json", default=str(EVAL / "results.json"))
    ap.add_argument("--mode", default="both", choices=["hook", "recall", "both"])
    ap.add_argument("--gh", action="store_true", help="check the session block's Tracked-now PRs and the working note's PR refs against GitHub (read-only gh calls)")
    ap.add_argument("--telemetry", action="store_true", help="include live-hook telemetry from the store's own logs")
    ap.add_argument("--verbose", action="store_true")
    ap.add_argument("--only", default="")
    ap.add_argument("--validate-only", action="store_true")
    args = ap.parse_args()
    data = json.loads(Path(args.cases).read_text(encoding="utf-8"))
    cases = [c for c in data["cases"] if not args.only or c["id"] in args.only.split(",")]
    tmp = Path(tempfile.mkdtemp(prefix="sk-eval-"))
    try:
        db_copy = tmp / "eval.db"
        copy_db(Path(args.db), db_copy)
        home = tmp / "home"
        home.mkdir()
        store, embedder, run_hook, shadow_hook = make_hook_runner(db_copy, home)
        problems = validate(data, store)
        for p in problems:
            print("CASE PROBLEM:", p)
        if args.validate_only:
            print(f"{len(cases)} cases, {len(problems)} problems")
            return 1 if problems else 0
        if embedder is None:
            print("WARNING: no embedding server, recall falls back to names only; numbers are not the baseline")
        runners = {"hook": run_hook}
        if args.mode in ("recall", "both"):
            runners["recall"] = make_recall_runner(store, embedder)
        results: dict = {}
        session_ids = session_state_ids(store)
        for mode, run in runners.items():
            if args.mode != "both" and mode != args.mode:
                continue
            rows = []
            for c in cases:
                shown, block, ms = run(c["prompt"])
                r = grade(c, shown, store, block)
                r["ms"] = ms
                r["has_stale"] = bool(c.get("stale"))
                rows.append(r)
                tag = "HIT " if r["hit@all"] else ("EMPTY" if r["empty"] else "miss")
                print(f"[{mode}] {tag:<5} {c['id']:<34} rank {r['rank']} shown {r['n_shown']} ~{r['tokens']}tok noise {len(r['noise'])} stale {len(r['stale_leaked'])} {ms}ms")
                if args.verbose:
                    for sid, line in r["lines"].items():
                        mark = "+" if int(sid) in set().union(*[g["ids"] for g in groups_of(c)] or [set()]) else ("!" if int(sid) in c.get("stale", []) else "-")
                        print(f"        {mark} #{sid} {line[:130]}")
            add_core_credit(rows, cases, session_ids)
            added = {c["id"] for c in cases if c.get("added")}
            guards = {c["id"] for c in cases if c.get("category") == "guard"}
            orig = [r for r in rows if r["id"] not in added]
            extra = [r for r in rows if r["id"] in added and r["id"] not in guards]
            results[mode] = {"summary": summarize(orig), "by_category": by_category(orig), "summary_added": summarize(extra) if extra else {},
                             "by_category_added": by_category(extra) if extra else {},
                             "guards": {r["id"]: {"leaked": r["stale_leaked"], "shown": r["shown"]} for r in rows if r["id"] in guards}, "rows": rows}
        out = {"snapshot": str(args.db), "run_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "embedding_server": embedder is not None, "case_problems": problems,
               "session_start": session_eval(store, shadow_hook, args.gh, ""), "results": results}
        if args.telemetry:
            out["live_telemetry"] = telemetry(Path(args.db))
        Path(args.json).write_text(json.dumps(out, indent=1), encoding="utf-8")
        for mode, res in results.items():
            print(f"\n== {mode}: {json.dumps(res['summary'])}")
            added_ids = {c["id"] for c in cases if c.get("added")}
            orig_rows = [r for r in res["rows"] if r["id"] not in added_ids]
            if orig_rows:
                print(f"== {mode} with credit for facts already in the session block (note off): hit@all {sum(r['core_hit'] for r in orig_rows) / len(orig_rows):.3f} "
                      f"full {sum(r['core_full'] for r in orig_rows) / len(orig_rows):.3f}; credited beyond the hook: "
                      f"{[r['id'] for r in orig_rows if r['core_hit'] and not r['hit@all']]}")
            print(json.dumps(res["by_category"]))
            if res.get("summary_added"):
                print(f"== {mode} added cases: hit@all {res['summary_added']['hit@all']} lenient {res['summary_added']['lenient_hit']} "
                      f"empty {res['summary_added']['empty_rate']} ({res['summary_added']['cases']} cases)")
            if res.get("guards"):
                print(f"== {mode} guards: " + ", ".join(f"{k} {'LEAKED ' + str(v['leaked']) if v['leaked'] else 'ok'}" for k, v in res["guards"].items()))
        print("\n== session start:", json.dumps(out["session_start"]))
        print(f"wrote {args.json}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
