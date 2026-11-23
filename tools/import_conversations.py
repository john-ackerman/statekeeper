"""Import the frozen Persistence store's conversation fragments into Statekeeper as unreviewed, identifier-only states (statekeeper.convimport).

    python tools/import_conversations.py --db <db> [--sample 60] [--batch 200] [--workers 4] [--limit N] [--run-dir DIR] [--no-backup]

Backs the database up first (tools/backup_db.py), then works in batches: the model reads each fragment (claude -p, the extractor's prompt and model;
answers cached in <run-dir>/answers.jsonl so a rerun never pays twice), then the facts are filtered, deduplicated against the store (exact text or
cosine >= 0.95: the fragment becomes more evidence for the state it repeats) and written. Resumable: fragments already in import_map or in
<run-dir>/progress.json are skipped. Between batches it checks the work board's spend status and stops on a pause (exit 3).
Persistence is opened read-only (?mode=ro): its CLI rewrites LastAccessedUtc on reads, sqlite in ro mode does not.
"""
from __future__ import annotations

import argparse
import json
import math
import sqlite3
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from statekeeper import SqliteStore  # noqa: E402
from statekeeper.convimport import done_fragments, import_fragment  # noqa: E402
from statekeeper.embed import HttpEmbedder  # noqa: E402
from statekeeper.extract import MODEL, Extractor  # noqa: E402
from statekeeper.proc import run  # noqa: E402

PERSISTENCE = "C:/repos/_handoffs/persistence-store/claude.db"
DEFAULT_DB = str(Path.home() / ".claude" / "statekeeper" / "shadow.db")
SAME = 0.95
MIN_CHARS = 80
BOARD = "C:/repos/work-board/scripts/board.sh"


def fragments(path: str, sample: int | None) -> list[dict]:
    db = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    rows = db.execute("SELECT f.Id, f.Content, f.CreatedUtc, (SELECT s.Name FROM ContextFragmentSources cs JOIN Sources s ON s.Id = cs.SourceId "
                      "WHERE cs.ContextFragmentId = f.Id LIMIT 1) FROM ContextFragments f WHERE f.FragmentType = '3' AND f.IsDeleted = 0 "
                      "ORDER BY f.CreatedUtc").fetchall()
    db.close()
    if sample:
        step = len(rows) / sample
        rows = [rows[int(i * step)] for i in range(sample)]
    return [{"id": int(fid), "content": c or "", "created": (cr or "")[:19], "source": src or "local"} for fid, c, cr, src in rows
            if len((c or "").strip()) >= MIN_CHARS]


def call(prompt: str) -> tuple[str, float]:
    with tempfile.TemporaryDirectory() as cwd:
        r = run(["claude", "-p", "--setting-sources", "project", "--model", MODEL, "--tools", "", "--no-session-persistence", "--output-format", "json"],
                input=prompt, capture_output=True, text=True, timeout=300, cwd=cwd, encoding="utf-8")
    try:
        d = json.loads(r.stdout or "{}")
    except ValueError:
        d = {}
    return (d.get("result") or "") if r.returncode == 0 else "", float(d.get("total_cost_usd") or 0)


def spend_paused() -> bool:
    try:
        r = run(["bash", BOARD, "spend-status"], capture_output=True, text=True, timeout=60)
    except Exception:
        return False                                                  # board unreachable: carry on
    return r.returncode == 3


def unit(v):
    n = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / n for x in v]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=DEFAULT_DB)
    ap.add_argument("--persistence", default=PERSISTENCE)
    ap.add_argument("--sample", type=int, default=None, help="an evenly spaced sample of this many fragments (for a trial on a copy)")
    ap.add_argument("--batch", type=int, default=200)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--limit", type=int, default=None, help="at most this many fragments this run")
    ap.add_argument("--run-dir", default=None)
    ap.add_argument("--no-backup", action="store_true")
    ap.add_argument("--no-spend-check", action="store_true")
    args = ap.parse_args()
    run_dir = Path(args.run_dir or Path(args.db).parent / "import-conversations")
    run_dir.mkdir(parents=True, exist_ok=True)
    if not args.no_backup:
        r = run([sys.executable, str(ROOT / "tools" / "backup_db.py"), args.db], capture_output=True, text=True)
        if r.returncode != 0:
            print("backup failed; nothing imported", file=sys.stderr)
            return 1
        print(json.dumps({"backup": r.stdout.strip()}), flush=True)
    store = SqliteStore(args.db)
    progress_file = run_dir / "progress.json"
    progress = set(json.loads(progress_file.read_text(encoding="utf-8"))) if progress_file.exists() else set()
    answers_file = run_dir / "answers.jsonl"
    answers: dict[int, str] = {}
    if answers_file.exists():
        for line in answers_file.read_text(encoding="utf-8").splitlines():
            try:
                row = json.loads(line)
                answers[int(row["id"])] = row["answer"]
            except (ValueError, KeyError):
                continue
    done = done_fragments(store) | progress
    todo = [f for f in fragments(args.persistence, args.sample) if f["id"] not in done]
    if args.limit:
        todo = todo[:args.limit]
    emb = HttpEmbedder(timeout=10.0)
    ids, vecs = [], []
    standing = {s.id: s for s in store.searchable_states()}
    for v in store.vectors("state", emb.model):
        if v["owner_id"] in standing:
            ids.append(v["owner_id"])
            vecs.append(unit(v["vector"]))
    exact = {f"{s.thing_name}: {s.text}".strip().lower(): s.id for s in standing.values()}

    def near(text: str):
        if text.strip().lower() in exact:
            return exact[text.strip().lower()]
        try:
            q = unit(emb.embed([text])[0])
        except Exception:
            return None                                               # meaning is down: only an exact repeat counts as one
        best, at = 0.0, None
        for i, u in zip(ids, vecs):
            c = sum(a * b for a, b in zip(q, u))
            if c > best:
                best, at = c, i
        return at if best >= SAME else None

    total = {"fragments": 0, "written": 0, "corroborated": 0, "dropped": {}, "rejected": {}, "errors": 0, "cost_usd": 0.0}
    session = f"import-conversations-{time.strftime('%Y%m%d-%H%M%S')}"
    print(json.dumps({"to_do": len(todo), "already_done": len(done)}), flush=True)
    for b in range(0, len(todo), args.batch):
        if b and not args.no_spend_check and spend_paused():
            print(json.dumps({"stopped": "spend paused", "at_batch": b // args.batch, **total}), flush=True)
            return 3
        batch = todo[b:b + args.batch]
        ex = Extractor(store, user_source="John")
        need = [f for f in batch if f["id"] not in answers]
        prompts = {f["id"]: ex.prompt({"id": f"persistence-conv:{f['id']}", **{k: f[k] for k in ("content", "created", "source")}}) for f in need}
        with ThreadPoolExecutor(args.workers) as pool:
            got = dict(zip(prompts, pool.map(call, prompts.values())))
        with answers_file.open("a", encoding="utf-8") as fh:
            for fid, (ans, cost) in got.items():
                total["cost_usd"] += cost
                if ans:
                    answers[fid] = ans
                    fh.write(json.dumps({"id": fid, "answer": ans, "cost": cost}) + "\n")
        for f in batch:
            if f["id"] not in answers:
                total["errors"] += 1                                  # the model call failed: not marked done, a rerun retries it
                continue
            rec = import_fragment(store, f, answers[f["id"]], near=near, session=session)
            total["fragments"] += 1
            total["written"] += rec.get("written", 0)
            total["corroborated"] += rec.get("corroborated", 0)
            total["errors"] += 1 if rec.get("error") else 0
            for key in ("dropped", "rejected"):
                for why, n in (rec.get(key) or {}).items():
                    total[key][why] = total[key].get(why, 0) + n
            for sid in rec.get("written_ids", []):
                s = store.get_state(sid)
                if s is not None:
                    exact[f"{s.thing_name}: {s.text}".strip().lower()] = sid
                    try:
                        ids.append(sid)
                        vecs.append(unit(emb.embed([f"{s.thing_name}: {s.text}"])[0]))
                    except Exception:
                        pass
            progress.add(f["id"])
        progress_file.write_text(json.dumps(sorted(progress)), encoding="utf-8")
        print(json.dumps({"batch": b // args.batch + 1, "of": math.ceil(len(todo) / args.batch), **total}, default=str), flush=True)
    total["cost_usd"] = round(total["cost_usd"], 3)
    print(json.dumps({"done": True, **total}), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
