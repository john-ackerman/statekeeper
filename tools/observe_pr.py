"""Observe one pull request through `gh` (read only) and write its state, CI and review as verified keyed states.

    python tools/observe_pr.py OWNER/NAME NUMBER [--db out/statekeeper.db]

Run it again later: an unchanged value only moves last-verified; a changed one supersedes by key.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from statekeeper import SqliteStore  # noqa: E402
from statekeeper.writers.github_pr import fetch, observe_pr  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("repo")
    ap.add_argument("number", type=int)
    ap.add_argument("--db", default="out/statekeeper.db")
    args = ap.parse_args()
    store = SqliteStore(args.db)
    out = observe_pr(store, args.repo.split("/")[-1], args.number, fetch(args.repo, args.number))
    for key in ("pr.state", "pr.ci", "pr.review"):
        s = out[key]
        print(f"{key}: {s.text}  [last changed {s.recorded_utc[:19]}, last verified {(s.confirmed_utc or '')[:19]}]")
    print("changed:", out["changed"] or "nothing")
    return 0


if __name__ == "__main__":
    sys.exit(main())
