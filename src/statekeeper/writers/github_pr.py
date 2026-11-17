"""Deterministic writer: a pull request's state, CI and review decision, observed through `gh` (read only), written as verified keyed states.

No model is involved. Each observation is keyed (pr.state, pr.ci, pr.review). Seeing the same value again does not add a row: it moves the state's
last-verified time (`confirm`) and leaves its last-changed time (`recorded_utc`) alone. A different value is written as a new verified state, which
supersedes the old one by key and leaves it as history. The verify handle is the exact command that re-reads the truth.
"""
from __future__ import annotations

import json
from typing import Callable

from ..proc import run
from ..store import Evidence, Store, now
from .common import observe_keyed

WRITER = "rule:github-pr"
FIELDS = "state,isDraft,reviewDecision,statusCheckRollup,headRefOid,updatedAt"


def normalize(p: dict) -> dict[str, str]:
    state = str(p.get("state") or "").lower()
    pr_state = "draft, open" if state == "open" and p.get("isDraft") else state
    checks = [c for c in (p.get("statusCheckRollup") or []) if isinstance(c, dict)]
    sha = str(p.get("headRefOid") or "")[:7]
    failed = [c.get("name") or c.get("context") or "?" for c in checks if str(c.get("conclusion") or c.get("state") or "").upper() in ("FAILURE", "ERROR", "TIMED_OUT", "CANCELLED", "ACTION_REQUIRED")]
    running = [c for c in checks if str(c.get("status") or "").upper() in ("IN_PROGRESS", "QUEUED", "PENDING", "WAITING") or str(c.get("state") or "").upper() == "PENDING"]
    passed = [c for c in checks if str(c.get("conclusion") or c.get("state") or "").upper() in ("SUCCESS", "NEUTRAL", "SKIPPED")]
    if failed:
        ci = f"CI red ({', '.join(failed[:3])} failed) on {sha}"
    elif running:
        ci = f"CI pending ({len(running)} of {len(checks)} checks still running) on {sha}"
    elif checks:
        ci = f"CI green ({len(passed)} of {len(checks)} checks passed) on {sha}"
    else:
        ci = f"no CI checks reported on {sha}"
    review = {"APPROVED": "approved", "CHANGES_REQUESTED": "changes requested", "REVIEW_REQUIRED": "review required"}.get(str(p.get("reviewDecision") or "").upper(), "no review decision yet")
    return {"pr.state": pr_state, "pr.ci": ci, "pr.review": review}


def fetch(repo_slug: str, number: int) -> dict:
    """Read-only `gh pr view`. repo_slug is owner/name."""
    r = run(["gh", "pr", "view", str(number), "--repo", repo_slug, "--json", FIELDS], capture_output=True, text=True, encoding="utf-8", timeout=60)
    if r.returncode != 0:
        raise RuntimeError(f"gh pr view failed: {r.stderr.strip()[:200]}")
    return json.loads(r.stdout)


def observe_pr(store: Store, repo: str, number: int, payload: dict, clock: Callable[[], str] = now) -> dict:
    """Writes one observation of PR `repo`#`number`. Returns the state per key, plus "changed": the keys whose value differs from the previous one."""
    ev = Evidence("tool", f"gh pr view {repo}#{number}", json.dumps({k: payload.get(k) for k in ("state", "isDraft", "reviewDecision", "headRefOid", "updatedAt")})[:300])
    return observe_keyed(store, writer=WRITER, thing=f"{repo} PR {number}", parent=repo, observations=normalize(payload), evidence=ev,
                         handle=f"gh pr view {number} --repo {repo} --json {FIELDS}", as_of=payload.get("updatedAt"), clock=clock)


