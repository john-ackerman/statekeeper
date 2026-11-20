"""Run every live deterministic writer once (read only): the user's open PRs, the newest ADO build of each, recently active agents. Writes to the shadow store.

    python tools/observe_all.py [--db ~/.claude/statekeeper/shadow.db]

Repositories, ADO org and project come from the configuration (src/statekeeper/config.py); STATEKEEPER_ADO_ORG / STATEKEEPER_ADO_PROJECT override. Safe to run often; the shadow hook starts it detached at most
every 20 minutes while a session is active, and the nightly job runs it too.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from statekeeper import SqliteStore, config  # noqa: E402
from statekeeper.writers.live import observe_everything, recent_transcripts  # noqa: E402

for _env, _key in (("STATEKEEPER_ADO_ORG", "ado_org"), ("STATEKEEPER_ADO_PROJECT", "ado_project")):
    if config.load().get(_key):
        os.environ.setdefault(_env, str(config.load()[_key]))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(Path.home() / ".claude" / "statekeeper" / "shadow.db"))
    args = ap.parse_args()
    from statekeeper.writers.board_agents import fetch_agents
    from statekeeper.writers.board_asks import fetch_asks
    from statekeeper.writers.watcher_health import observe_watcher
    off = lambda name: os.environ.get(name, "1") in ("0", "off")  # noqa: E731
    result = observe_everything(SqliteStore(args.db), transcripts=recent_transcripts(Path.home() / ".claude" / "projects"),
                                get_board_agents=None if off("STATEKEEPER_BOARD_AGENTS") else fetch_agents,
                                get_board_asks=None if off("STATEKEEPER_BOARD_ASKS") else fetch_asks,
                                watcher=None if off("STATEKEEPER_WATCHER") else observe_watcher)
    print(json.dumps(result))
    return 0 if not result["errors"] else 1


if __name__ == "__main__":
    sys.exit(main())
