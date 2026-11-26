"""One isolated `claude -p` Haiku call for the experiment (writer and reader share it).

Isolation, so the child session never feeds the store it is reading or writing:
  --safe-mode            no CLAUDE.md, hooks, MCP servers, plugins, skills, custom agents or output styles (auth still works: OAuth, not an API key)
  --setting-sources ""   no user/project/local settings files either (hooks and env from settings.json stay out even if safe mode changes)
  --strict-mcp-config    no MCP servers from any configuration
  --tools ""             no tools at all; --disable-slash-commands; --no-session-persistence (no transcript to be re-imported later)
  --system-prompt        a short task-specific system prompt in place of the default one (fewer tokens, nothing about the user's machine)
  cwd                    an empty temporary directory, so no project files are discovered
  env guard              STATEKEEPER_SHADOW=0 / STATEKEEPER_INJECT=0 / STATEKEEPER_CHILD=1: if any statekeeper hook still ran it would exit at once
`tools/../experiments/haiku_interface/check_isolation.py` proves the hook flags with a sentinel hook.
"""
from __future__ import annotations

import json
import os
import subprocess
import tempfile
import time
from dataclasses import dataclass, field

MODEL = "claude-haiku-5-5"
ISOLATION = ["--safe-mode", "--setting-sources", "", "--strict-mcp-config", "--tools", "", "--disable-slash-commands", "--no-session-persistence"]


@dataclass
class Call:
    text: str
    seconds: float
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read: int = 0
    cache_write: int = 0
    cost_usd: float = 0.0
    session_id: str = ""
    raw: dict = field(default_factory=dict)


def child_env() -> dict:
    env = dict(os.environ)
    env.update({"STATEKEEPER_SHADOW": "0", "STATEKEEPER_INJECT": "0", "STATEKEEPER_CHILD": "1", "STATEKEEPER_PROBES": "0"})
    return env


def call(prompt: str, system: str, *, schema: dict | None = None, extra: list[str] | None = None, timeout: int = 300) -> Call:
    args = ["claude", "-p", "--model", MODEL, *ISOLATION, "--system-prompt", system, "--output-format", "json"]
    if schema is not None:
        args += ["--json-schema", json.dumps(schema)]
    args += extra or []
    t0 = time.time()
    with tempfile.TemporaryDirectory(prefix="sk-haiku-") as cwd:
        r = subprocess.run(args, input=prompt, capture_output=True, text=True, encoding="utf-8", timeout=timeout, cwd=cwd, env=child_env())
    secs = time.time() - t0
    try:
        out = json.loads(r.stdout)
    except ValueError:
        raise RuntimeError(f"claude exited {r.returncode}: {(r.stderr or r.stdout)[:300]}")
    if r.returncode != 0 or out.get("is_error"):
        raise RuntimeError(f"claude exited {r.returncode}: {str(out.get('result'))[:300]} {r.stderr[:200]}")
    u = out.get("usage") or {}
    text = out.get("result") or ""
    if schema is not None and out.get("structured_output") is not None:
        text = json.dumps(out["structured_output"])
    return Call(text, secs, int(u.get("input_tokens") or 0), int(u.get("output_tokens") or 0), int(u.get("cache_read_input_tokens") or 0),
                int(u.get("cache_creation_input_tokens") or 0), float(out.get("total_cost_usd") or 0.0), str(out.get("session_id") or ""), out)


def parse_json(text: str) -> dict:
    import re
    text = (text or "").strip()
    m = re.search(r"\{.*\}", text, re.S)
    got = json.loads(m.group(0) if m else text)
    if not isinstance(got, dict):
        raise ValueError("not a JSON object")
    return got
