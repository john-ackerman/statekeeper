"""The nightly job for the statekeeper store: verified backup, an incremental re-import from an earlier fact store (only if configured), a consolidation pass, miss detection, an
outcome report, and a one-screen health summary of the live hooks (reports/health-latest.txt). Each step is independent and logged; a failing step does not stop the next. Not wired to any scheduler by this file.

    python tools/nightly.py [--home ~/.claude/statekeeper] [--skip-import] [--skip-embed]

Reports go to <home>/reports/outcomes-<date>.json; backups (kept: newest 14) to <home>/backups.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tools"))

from statekeeper import SqliteStore, config  # noqa: E402
from statekeeper.consolidate import run_pass  # noqa: E402
from statekeeper.embed import HttpEmbedder  # noqa: E402
from statekeeper.importer import import_export  # noqa: E402
from statekeeper.misses import log_misses  # noqa: E402
from statekeeper.proc import run  # noqa: E402
from statekeeper.report import build_report  # noqa: E402


def step(log: list, name: str, fn):
    t0 = time.time()
    try:
        result = fn()
        log.append({"step": name, "ok": True, "seconds": round(time.time() - t0, 1), "result": result})
    except BaseException as e:  # a failed step is recorded, the night continues
        log.append({"step": name, "ok": False, "seconds": round(time.time() - t0, 1), "error": f"{type(e).__name__}: {str(e)[:300]}", "trace": traceback.format_exc()[-600:]})


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--home", default=str(Path.home() / ".claude" / "statekeeper"))
    ap.add_argument("--skip-import", action="store_true")
    ap.add_argument("--skip-embed", action="store_true")
    ap.add_argument("--skip-questions", action="store_true", help="do not call the model to write requests for new durable states")
    args = ap.parse_args()
    home = Path(args.home)
    db = home / "shadow.db"
    log: list = []

    def backup():
        from backup_db import counts
        import sqlite3
        dest_dir = home / "backups"
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / f"shadow-{time.strftime('%Y%m%d-%H%M%S')}.db"
        a, b = sqlite3.connect(str(db)), sqlite3.connect(str(dest))
        a.backup(b)
        b.close()
        a.close()
        if counts(str(db)) != counts(str(dest)):
            raise RuntimeError("backup does not match the source")
        for old in sorted(dest_dir.glob("shadow-*.db"))[:-14]:
            old.unlink()
        return str(dest)

    def reimport():
        command = config.get_list("legacy_export_command")
        if not command:
            return {"skipped": "no legacy_export_command configured"}
        out = run(command, capture_output=True, text=True, encoding="utf-8", timeout=900)
        if out.returncode != 0:
            raise RuntimeError(f"export command failed: {out.stderr.strip()[:200]}")
        return import_export(SqliteStore(db), json.loads(out.stdout))

    def consolidate():
        return run_pass(SqliteStore(db), HttpEmbedder())

    def skills():
        """The installed skills (SKILL.md under config skill_dirs) as states: what each does and the CLIs it runs (writers/skills.py)."""
        from statekeeper import config
        from statekeeper.writers.skills import observe_skills
        return observe_skills(SqliteStore(db), config.get_list("skill_dirs"))

    def promote_corroborated():
        """Proposals corroborated by a second source, a merged duplicate, repeated use or an owner/verified state become active (statekeeper.promote)."""
        from statekeeper.promote import promote
        found = promote(SqliteStore(db), HttpEmbedder().model)
        return {"promoted": len(found), "sample": found[:5]}

    def decompose_long():
        """Long states (notes, documents, long claims) split into atomic states under their topics (statekeeper.decompose); the long one is superseded."""
        from statekeeper.decompose import apply, is_long, plan
        st = SqliteStore(db)
        todo = [x for x in st.searchable_states() if is_long(x) and x.writer != "rule:skills" and x.thing_name != "working note"
                and not (x.volatility == "rule" and x.state_key)][:60]
        return apply(st, plan(st, todo))

    def reparent():
        """Things made since last night filed under their parents (statekeeper.reparent: identifier, a PR's ticket, flag keys, leading name);
        a flag said two ways merged. Idempotent; each move is one event."""
        from statekeeper.reparent import apply, merge_plan, plan
        st = SqliteStore(db)
        merges = merge_plan(st)
        apply(st, [], actor="rule:reparent", merges=merges)
        done = apply(st, plan(st), actor="rule:reparent")
        return {"merged": len(merges), "moved": len(done), "sample": [f"{m['child']} < {m['parent']}" for m in done[:5]]}

    def questions():
        """Requests each new durable/how-to state answers (statekeeper.questions; one model call per 15 states), then their vectors."""
        from statekeeper.questions import ensure_question_vectors, generate
        st = SqliteStore(db)
        out = generate(st, limit=150)
        out["embedded"] = ensure_question_vectors(st, HttpEmbedder())
        return out

    def misses():
        return log_misses(SqliteStore(db), HttpEmbedder())

    def report():
        out = build_report(SqliteStore(db))
        d = home / "reports"
        d.mkdir(parents=True, exist_ok=True)
        path = d / f"outcomes-{time.strftime('%Y%m%d')}.json"
        path.write_text(json.dumps(out, indent=1), encoding="utf-8")
        return {"path": str(path), "outcomes_by_kind": out["outcomes_by_kind"], "live_turns_recorded": out["live_turns_recorded"], "misses_by_source": out["misses_by_source"]}

    def weights():
        """Per-state usefulness weights from the last 21 days of outcomes (statekeeper.usefulness.weigh): logged, reasoned, revertible; flags, never closes."""
        from statekeeper.usefulness import weigh
        out = weigh(SqliteStore(db), days=21, actor="nightly:usefulness")
        return {"changed": len(out["changed"]), "flagged": out["flagged"], "sample": out["changed"][:5]}

    def health():
        """The last 24 hours of the live hooks, as reports/health-<date>.txt and reports/health-latest.txt (the file to glance at each day)."""
        from statekeeper.report import health_text, injection_health
        since = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - 86400))
        try:
            lines = (home / "shadow.log.jsonl").read_text(encoding="utf-8").splitlines()[-20000:]
        except OSError:
            lines = []
        h = injection_health(lines, since)
        from statekeeper.usefulness import summary
        h["usefulness"] = summary(SqliteStore(db), days=1)
        h["open_flags"] = len(SqliteStore(db).open_flags())
        from statekeeper.usefulness import weak_connections
        h["weak_connections"] = weak_connections(SqliteStore(db), days=7)
        d = home / "reports"
        d.mkdir(parents=True, exist_ok=True)
        text = health_text(h)
        (d / f"health-{time.strftime('%Y%m%d')}.txt").write_text(text, encoding="utf-8")
        (d / "health-latest.txt").write_text(text, encoding="utf-8")
        return h

    def observe():
        out = run([sys.executable, str(ROOT / "tools" / "observe_all.py"), "--db", str(db)], capture_output=True, text=True, encoding="utf-8", timeout=900)
        return json.loads(out.stdout.strip().splitlines()[-1]) if out.stdout.strip() else {"error": out.stderr[-200:]}

    step(log, "backup", backup)
    step(log, "observe-writers", observe)
    step(log, "skills", skills)
    step(log, "reparent", reparent)
    if not args.skip_import:
        step(log, "reimport-legacy", reimport)
    if not args.skip_embed:
        step(log, "consolidate", consolidate)
        step(log, "promote-corroborated", promote_corroborated)
        if not args.skip_questions:
            step(log, "decompose-long", decompose_long)
            step(log, "questions", questions)
        step(log, "misses", misses)
    step(log, "report", report)
    step(log, "usefulness-weights", weights)
    step(log, "injection-health", health)
    home.mkdir(parents=True, exist_ok=True)
    with open(home / "nightly.log.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps({"at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "steps": log}) + "\n")
    print(json.dumps(log, indent=1))
    return 0 if all(s["ok"] for s in log) else 1


if __name__ == "__main__":
    sys.exit(main())
