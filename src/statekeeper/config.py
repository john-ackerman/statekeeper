"""Optional per-install configuration for the live writers and the nightly job, read from a JSON file. Nothing here is required: with no file, every list is empty.

Search order, first file found wins: the path in STATEKEEPER_CONFIG, `local/config.json` in the repository checkout (git-ignored), then
`~/.claude/statekeeper/config.json`. Keys (all optional):

    github_repos           ["owner/name", ...]  repositories whose open pull requests (authored by the `gh` user) the PR writer observes
    build_repos            ["name", ...]        repository names (the part after the slash) whose pull requests a CI pipeline builds; the build writer reads those
    ado_org, ado_project   Azure DevOps organization URL and project for the build writer (environment variables STATEKEEPER_ADO_ORG / _PROJECT win)
    legacy_export_command  ["python", "script.py"]  a command that prints, to stdout, an export of an earlier fact store as JSON (see tools/import_legacy.py);
                           the nightly job re-imports it incrementally when present
    session_refresh_command ["python", "script.py"]  run (bounded, 20 s) by the live SessionStart hook before it prints the session block, e.g. to
                           re-read the core and the working note from wherever they are kept
    skill_dirs             ["~/.claude/skills", ...]  folders of <name>/SKILL.md the nightly skills writer reads (writers/skills.py)
    embed_server_command   ["path/to/start-embedding-server.cmd"]  started detached by the live SessionStart hook when nothing answers at
                           STATEKEEPER_EMBED_URL (meaning-based recall needs it)
"""
from __future__ import annotations

import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def candidates() -> list[Path]:
    given = os.environ.get("STATEKEEPER_CONFIG")
    return ([Path(given)] if given else []) + [ROOT / "local" / "config.json", Path.home() / ".claude" / "statekeeper" / "config.json"]


def load() -> dict:
    for path in candidates():
        if path.is_file():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                return {}
            return data if isinstance(data, dict) else {}
    return {}


def get_list(key: str) -> list[str]:
    value = load().get(key)
    return [str(v) for v in value] if isinstance(value, list) else []
