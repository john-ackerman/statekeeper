"""Run every live deterministic writer once (read only): The owner's open PRs, the newest ADO build of each, recently active agents. Writes to the shadow store.

    python tools/observe_all.py [--db ~/.claude/statekeeper/shadow.db]

ADO org/project default to the owner's (STATEKEEPER_ADO_ORG / STATEKEEPER_ADO_PROJECT override). Safe to run often; the shadow hook starts it detached at most
every 20 minutes while a session is active, and the nightly job runs it too.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from statekeeper import SqliteStore  # noqa: E402
from statekeeper.writers.live import observe_everything, recent_transcripts  # noqa: E402

os.environ.setdefault("STATEKEEPER_ADO_ORG", "https://dev.azure.com/example-org")
os.environ.setdefault("STATEKEEPER_ADO_PROJECT", "Example Project")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(Path.home() / ".claude" / "statekeeper" / "shadow.db"))
    args = ap.parse_args()
    result = observe_everything(SqliteStore(args.db), transcripts=recent_transcripts(Path.home() / ".claude" / "projects"))
    print(json.dumps(result))
    return 0 if not result["errors"] else 1


if __name__ == "__main__":
    sys.exit(main())
