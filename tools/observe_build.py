"""Observe one Azure DevOps build (read only) and write its state as a verified keyed state.

    STATEKEEPER_ADO_ORG=https://dev.azure.com/<org> STATEKEEPER_ADO_PROJECT=<project> python tools/observe_build.py BUILD_ID [--db out/statekeeper.db]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from statekeeper import SqliteStore  # noqa: E402
from statekeeper.writers.ado_build import fetch, observe_build  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("build_id", type=int)
    ap.add_argument("--db", default="out/statekeeper.db")
    args = ap.parse_args()
    out = observe_build(SqliteStore(args.db), fetch(args.build_id))
    s = out["build.state"]
    print(f"{s.thing_name}: {s.text}  [last changed {s.recorded_utc[:19]}, last verified {(s.confirmed_utc or '')[:19]}]  changed={out['changed'] or 'nothing'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
