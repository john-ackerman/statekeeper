"""Per-turn parity report: what Legacy injected vs what statekeeper would have (shadow mode). Measured only.

    python tools/parity_report.py [--db ~/.claude/statekeeper/shadow.db] [--turns]      (--turns lists every turn)
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from statekeeper import SqliteStore  # noqa: E402
from statekeeper.parity import build_parity  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(Path.home() / ".claude" / "statekeeper" / "shadow.db"))
    ap.add_argument("--turns", action="store_true")
    args = ap.parse_args()
    r = build_parity(SqliteStore(args.db))
    if not args.turns:
        r = {k: v for k, v in r.items() if k != "turns"}
    print(json.dumps(r, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
