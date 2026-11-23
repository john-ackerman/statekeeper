"""End-to-end check on a temporary database: store -> retrieval -> the Claude Code adapter. No network, no settings touched.

    python tools/smoke.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from statekeeper import Evidence, SqliteStore, surface  # noqa: E402
from statekeeper.proc import run  # noqa: E402


def check(ok: bool, what: str) -> None:
    print(("ok   " if ok else "FAIL ") + what)
    if not ok:
        sys.exit(1)


def main() -> int:
    with tempfile.TemporaryDirectory() as d:
        db = os.path.join(d, "smoke.db")
        s = SqliteStore(db)
        ev = Evidence("message", "smoke-1", "the converter caps replicas at 75")
        st = s.add_state("the converter", "caps replicas at 75", evidence=[ev], writer="smoke", trust="verified", learned_how="read")
        check(st.status == "active", "a verified, evidenced state stands")
        claim = s.add_state("the converter", "is probably idle", evidence=[ev], writer="agent-1", trust="claimed", learned_how="claimed")
        check(claim.status == "proposed", "an agent claim is only a proposal")
        out = surface(s, "why does the converter cap replicas?", None)
        check("caps replicas at 75" in out.block, "retrieval surfaces the state for a named thing")
        check(surface(s, "lunch?", None).block == "", "an unrelated input surfaces nothing")
        s.close()
        env = dict(os.environ, STATEKEEPER_DB=db, PYTHONUTF8="1")
        env.pop("STATEKEEPER_EMBED_URL", None)
        hook = str(ROOT / "adapters" / "claude_code" / "recall_hook.py")
        r = run([sys.executable, hook], input=json.dumps({"prompt": "the converter?"}), capture_output=True, text=True, env=env)
        check("caps replicas at 75" in r.stdout, "the adapter prints the state as additional context")
    print("smoke passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
