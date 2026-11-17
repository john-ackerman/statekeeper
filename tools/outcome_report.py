"""Report what the outcome log holds and what it would teach. Measured only; no suggestion is applied.

    python tools/outcome_report.py [--db out/replay-outcomes.db]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from statekeeper import SqliteStore  # noqa: E402
from statekeeper.report import build_report  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="out/replay-outcomes.db")
    args = ap.parse_args()
    print(json.dumps(build_report(SqliteStore(args.db)), indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
