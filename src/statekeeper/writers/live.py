"""Runs the deterministic writers against live sources, read only: The owner's open PRs (GitHub), the newest ADO build of each, and recently active agents.

Every source is a function passed in (so tests need no network); the defaults call `gh` and `az` read-only and read transcript files. One source failing is
recorded and does not stop the others. Verified outcomes (right/wrong) accrue each time a value is seen again.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Callable

from ..proc import run
from ..store import Store
from .ado_build import fetch_latest, observe_build
from .agent_status import observe_agent, status_from_transcript
from .github_pr import fetch as fetch_pr, observe_pr

REPOS = ("ExampleOrg/ExampleApp", "ExampleOrg/ExampleWorkspace")


def list_open_prs(repo: str) -> list[int]:
    r = run(["gh", "pr", "list", "--repo", repo, "--author", "@me", "--state", "open", "--json", "number", "--limit", "60"],
                       capture_output=True, text=True, encoding="utf-8", timeout=60)
    if r.returncode != 0:
        raise RuntimeError(f"gh pr list failed: {r.stderr.strip()[:200]}")
    return [p["number"] for p in json.loads(r.stdout or "[]")]


def recent_transcripts(projects_dir: Path, hours: float = 24, limit: int = 40) -> list[Path]:
    cutoff = time.time() - hours * 3600
    found = [p for p in projects_dir.glob("*/*/subagents/agent-*.jsonl") if p.stat().st_mtime >= cutoff]
    return sorted(found, key=lambda p: -p.stat().st_mtime)[:limit]


def observe_everything(store: Store, *, repos=REPOS, list_prs: Callable[[str], list[int]] = list_open_prs, get_pr: Callable[[str, int], dict] = fetch_pr,
                       get_build: Callable[[str], dict | None] = fetch_latest, transcripts: list[Path] | None = None) -> dict:
    result = {"prs": 0, "builds": 0, "agents": 0, "errors": []}
    for repo in repos:
        try:
            numbers = list_prs(repo)
        except Exception as e:
            result["errors"].append(f"list {repo}: {str(e)[:120]}")
            continue
        name = repo.split("/")[-1]
        for n in numbers:
            try:
                observe_pr(store, name, n, get_pr(repo, n))
                result["prs"] += 1
            except Exception as e:
                result["errors"].append(f"pr {repo}#{n}: {str(e)[:120]}")
            if repo.endswith("ExampleApp"):          # the ADO pipeline builds this repo's PRs
                try:
                    b = get_build(f"refs/pull/{n}/merge")
                    if b:
                        observe_build(store, b)
                        result["builds"] += 1
                except Exception as e:
                    result["errors"].append(f"build #{n}: {str(e)[:120]}")
    for t in transcripts or []:
        try:
            observe_agent(store, status_from_transcript(t))
            result["agents"] += 1
        except Exception as e:
            result["errors"].append(f"agent {t.name}: {str(e)[:120]}")
    return result
