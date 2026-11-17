"""Deterministic writer: an Azure DevOps build/pipeline run, observed through `az devops invoke` (read only), written as a verified keyed state.

One key, build.state: queued / running / succeeded / failed / partially succeeded / canceled, with the commit and branch it is about. The build is a
thing under its pipeline definition. Same mechanics as the PR writer (writers/common.py): an unchanged value moves last-verified only; a change
supersedes by key and scores the old state verified_wrong. Per-job or coverage detail would be further keys; they need the build log, not read here.
"""
from __future__ import annotations

import json
import os
from typing import Callable

from ..proc import run
from ..store import Evidence, Store, now
from .common import observe_keyed

WRITER = "rule:ado-build"


def normalize(b: dict) -> dict[str, str]:
    status, result = str(b.get("status") or "").lower(), str(b.get("result") or "").lower()
    branch = str(b.get("sourceBranch") or "").removeprefix("refs/heads/")
    if branch.startswith("refs/pull/"):
        branch = "PR " + branch.split("/")[2]
    what = {"succeeded": "succeeded", "failed": "failed", "partiallysucceeded": "partially succeeded", "canceled": "canceled"}.get(result, result)
    if status == "completed":
        word = what or "completed"
    elif status in ("notstarted", "postponed"):
        word = "queued"
    elif status == "cancelling":
        word = "canceling"
    else:
        word = "running"
    return {"build.state": f"{word} on {str(b.get('sourceVersion') or '')[:7]} ({branch})"}


def fetch(build_id: int, organization: str | None = None, project: str | None = None) -> dict:
    """Read-only `az devops invoke` for one build. organization/project default to STATEKEEPER_ADO_ORG / STATEKEEPER_ADO_PROJECT."""
    org, proj = organization or os.environ.get("STATEKEEPER_ADO_ORG"), project or os.environ.get("STATEKEEPER_ADO_PROJECT")
    if not org or not proj:
        raise RuntimeError("set STATEKEEPER_ADO_ORG and STATEKEEPER_ADO_PROJECT (or pass them)")
    r = run(["az", "devops", "invoke", "--area", "build", "--resource", "builds", "--route-parameters", f"buildId={build_id}", f"project={proj}",
                        "--org", org, "--api-version", "7.1", "--output", "json"], capture_output=True, text=True, encoding="utf-8", timeout=90, shell=os.name == "nt")
    if r.returncode != 0:
        raise RuntimeError(f"az devops invoke failed: {r.stderr.strip()[:200]}")
    return json.loads(r.stdout)


def fetch_latest(branch: str, organization: str | None = None, project: str | None = None) -> dict | None:
    """The newest build for a branch ref (e.g. refs/pull/3341/merge), or None. Read only."""
    org, proj = organization or os.environ.get("STATEKEEPER_ADO_ORG"), project or os.environ.get("STATEKEEPER_ADO_PROJECT")
    if not org or not proj:
        raise RuntimeError("set STATEKEEPER_ADO_ORG and STATEKEEPER_ADO_PROJECT (or pass them)")
    r = run(["az", "devops", "invoke", "--area", "build", "--resource", "builds", "--route-parameters", f"project={proj}", "--org", org,
                        "--query-parameters", f"branchName={branch}", "queryOrder=queueTimeDescending", "$top=1", "--api-version", "7.1", "--output", "json"],
                       capture_output=True, text=True, encoding="utf-8", timeout=90, shell=os.name == "nt")
    if r.returncode != 0:
        raise RuntimeError(f"az devops invoke failed: {r.stderr.strip()[:200]}")
    value = json.loads(r.stdout).get("value") or []
    return value[0] if value else None


def observe_build(store: Store, b: dict, clock: Callable[[], str] = now) -> dict:
    definition = (b.get("definition") or {}).get("name") or "build"
    ev = Evidence("tool", f"az devops build {b.get('id')}", json.dumps({k: b.get(k) for k in ("status", "result", "sourceBranch", "sourceVersion", "startTime", "finishTime")})[:300])
    handle = f"az devops invoke --area build --resource builds --route-parameters buildId={b.get('id')} project=<project> --org <org>"
    return observe_keyed(store, writer=WRITER, thing=f"{definition} #{b.get('id')}", parent=definition, observations=normalize(b), evidence=ev, handle=handle,
                         as_of=b.get("finishTime") or b.get("startTime"), clock=clock)
