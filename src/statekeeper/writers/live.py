"""Runs the deterministic writers against live sources, read only: the user's open PRs (GitHub), the newest ADO build of each, and recently active agents.

Every source is a function passed in (so tests need no network); the defaults call `gh` and `az` read-only and read transcript files. One source failing is
recorded and does not stop the others. Verified outcomes (right/wrong) accrue each time a value is seen again.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Callable

from .. import config
from ..proc import run
from ..store import Store
from .ado_build import fetch_latest, observe_build
from .agent_status import observe_agent, status_from_transcript
from .github_pr import fetch as fetch_pr, observe_pr

def list_open_prs(repo: str) -> list[int]:
    r = run(["gh", "pr", "list", "--repo", repo, "--author", "@me", "--state", "open", "--json", "number", "--limit", "60"],
                       capture_output=True, text=True, encoding="utf-8", timeout=60)
    if r.returncode != 0:
        raise RuntimeError(f"gh pr list failed: {r.stderr.strip()[:200]}")
    return [p["number"] for p in json.loads(r.stdout or "[]")]


RECHECK_LIMIT = 15


def held_open(store: Store, repo_name: str) -> list[int]:
    """PR numbers of `repo_name` whose current pr.state in the store is open or draft."""
    out = []
    prefix = f"{repo_name} PR "
    for t in store.things():
        if t.retired or not t.name.startswith(prefix) or not t.name[len(prefix):].isdigit():
            continue
        cur = [s for s in store.states_of(t.id) if s.state_key == "pr.state" and s.status == "active"]
        if cur and cur[0].text.startswith(("open", "draft")):
            out.append(int(t.name[len(prefix):]))
    return sorted(out)


def recent_transcripts(projects_dir: Path, hours: float = 24, limit: int = 40) -> list[Path]:
    cutoff = time.time() - hours * 3600
    found = [p for p in projects_dir.glob("*/*/subagents/agent-*.jsonl") if p.stat().st_mtime >= cutoff]
    return sorted(found, key=lambda p: -p.stat().st_mtime)[:limit]


def observe_everything(store: Store, *, repos=None, build_repos=None, list_prs: Callable[[str], list[int]] = list_open_prs, get_pr: Callable[[str, int], dict] = fetch_pr,
                       get_build: Callable[[str], dict | None] = fetch_latest, transcripts: list[Path] | None = None,
                       get_board_agents: Callable[[], list[dict]] | None = None, get_board_asks: Callable[[], list[dict]] | None = None,
                       watcher: Callable[[Store], dict] | None = None) -> dict:
    """`repos` and `build_repos` default to the configuration (see config.py); build_repos are repository names whose PRs a CI pipeline builds.
    `get_board_agents` (writers/board_agents.fetch_agents in observe_all) reads the work board's agents; None skips it. `get_board_asks`
    (board_asks.fetch_asks) reads its open asks and `watcher` (watcher_health.observe_watcher) the watcher's health; both None skip. A board that is
    not running is quiet for asks (the watcher state says so); other failures are recorded, never fatal."""
    repos = config.get_list("github_repos") if repos is None else repos
    build_repos = config.get_list("build_repos") if build_repos is None else build_repos
    result = {"prs": 0, "builds": 0, "agents": 0, "errors": []}
    for repo in repos:
        try:
            numbers = list_prs(repo)
        except Exception as e:
            result["errors"].append(f"list {repo}: {str(e)[:120]}")
            continue
        name = repo.split("/")[-1]
        # PRs the store still holds as open that the open list no longer has were merged or closed: read them once more so they stop reading "open"
        still_open = set(numbers)
        numbers = list(numbers) + [n for n in held_open(store, name) if n not in still_open][:RECHECK_LIMIT]
        for n in numbers:
            try:
                observe_pr(store, name, n, get_pr(repo, n))
                result["prs"] += 1
            except Exception as e:
                result["errors"].append(f"pr {repo}#{n}: {str(e)[:120]}")
            if name in build_repos and n in still_open:
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
    if get_board_agents is not None:
        from .board_agents import observe_board_agents
        try:
            result["board_agents"] = observe_board_agents(store, get_board_agents())["agents"]
        except Exception as e:
            result["errors"].append(f"board agents: {str(e)[:120]}")
    if get_board_asks is not None:
        from .board_asks import observe_board_asks
        try:
            result["board_asks"] = observe_board_asks(store, get_board_asks())["open"]
        except OSError:
            pass                                               # board not running: watcher.status carries that
        except Exception as e:
            result["errors"].append(f"board asks: {str(e)[:120]}")
    if watcher is not None:
        try:
            watcher(store)
            result["watcher"] = 1
        except Exception as e:
            result["errors"].append(f"watcher: {str(e)[:120]}")
    return result
