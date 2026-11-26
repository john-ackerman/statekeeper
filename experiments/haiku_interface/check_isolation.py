"""Proves the child Haiku sessions run no hooks and load no memory.

1. Control: a `claude -p` call WITHOUT the isolation flags but with a sentinel UserPromptSubmit/SessionStart hook passed via --settings: the sentinel
   file must appear (the probe works).
2. Isolated: the same sentinel hook passed the same way, plus the experiment's isolation flags: the sentinel must NOT appear.
3. The live hook log (~/.claude/statekeeper/shadow.log.jsonl) must have no line with the isolated call's session id, and the model is asked
   whether it sees a CLAUDE.md / the user's name (it must not).

    python experiments/haiku_interface/check_isolation.py
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import haiku  # noqa: E402


def sentinel_settings(path: Path) -> str:
    cmd = f'"{sys.executable}" -c "import sys,pathlib;pathlib.Path(r\'{path}\').open(\'a\').write(sys.stdin.read()[:200]+chr(10))"'
    hook = [{"hooks": [{"type": "command", "command": cmd}]}]
    return json.dumps({"hooks": {"UserPromptSubmit": hook, "SessionStart": hook}})


def run(args: list[str], prompt: str) -> dict:
    with tempfile.TemporaryDirectory() as cwd:
        r = subprocess.run(args, input=prompt, capture_output=True, text=True, encoding="utf-8", timeout=300, cwd=cwd, env=haiku.child_env())
    return json.loads(r.stdout)


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="sk-sentinel-"))
    control, isolated = tmp / "control.txt", tmp / "isolated.txt"
    q = "Reply with JSON {\"claude_md\": true|false, \"user_name\": \"<the user's name if any instruction told you, else empty>\"}. Nothing else."
    base = ["claude", "-p", "--model", haiku.MODEL, "--output-format", "json", "--no-session-persistence", "--tools", ""]
    c = run(base + ["--settings", sentinel_settings(control)], q)
    i = run(["claude", "-p", "--model", haiku.MODEL, *haiku.ISOLATION, "--system-prompt", "Answer exactly as asked.", "--output-format", "json",
             "--settings", sentinel_settings(isolated)], q)
    log = Path.home() / ".claude" / "statekeeper" / "shadow.log.jsonl"
    time.sleep(2)
    hits = [l for l in log.read_text(encoding="utf-8", errors="replace").splitlines()[-5000:] if i.get("session_id") and i["session_id"] in l] if log.exists() else []
    out = {"control_sentinel_fired": control.exists(), "isolated_sentinel_fired": isolated.exists(), "isolated_session": i.get("session_id"),
           "live_log_lines_for_isolated_session": len(hits), "control_answer": c.get("result"), "isolated_answer": i.get("result"),
           "isolated_seconds": round((i.get("duration_ms") or 0) / 1000, 1), "isolated_usage": i.get("usage")}
    print(json.dumps(out, indent=1))
    ok = out["control_sentinel_fired"] and not out["isolated_sentinel_fired"] and not hits
    print("ISOLATION OK" if ok else "ISOLATION FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
