"""Add (or remove) the statekeeper shadow-mode hooks in a Claude Code settings.json. MERGES: existing hooks are never edited or removed.

    python tools/install_shadow_hooks.py [--settings ~/.claude/settings.json] [--remove] [--dry-run]

Makes a timestamped backup first, appends one new group per event (UserPromptSubmit, Stop, SessionStart) marked async so it cannot block a turn,
re-reads the file to validate the JSON and prove only the new groups were added, and refuses to write otherwise. --remove deletes only groups whose
command mentions shadow_hook.py. Rollback is also: copy the backup over settings.json.
"""
from __future__ import annotations

import argparse
import copy
import json
import shutil
import sys
import time
from pathlib import Path

HOOK = Path(__file__).resolve().parents[1] / "adapters" / "claude_code" / "shadow_hook.py"
EVENTS = ("UserPromptSubmit", "Stop", "SessionStart")


def group() -> dict:
    cmd = f'& python "{HOOK}"'
    return {"hooks": [{"type": "command", "command": cmd, "shell": "powershell", "timeout": 10, "async": True}]}


INJECT = HOOK.with_name("inject_hook.py")


def inject_group() -> dict:
    """The synchronous live-injection hook: it exits silently unless STATEKEEPER_INJECT=1, so registering it changes nothing until the flag is flipped."""
    return {"hooks": [{"type": "command", "command": f'& python "{INJECT}"', "shell": "powershell", "timeout": 6}]}


def is_ours(g: dict) -> bool:
    return any(("shadow_hook.py" in (h.get("command") or "")) or ("inject_hook.py" in (h.get("command") or "")) for h in g.get("hooks", []))


def has_inject(settings: dict) -> bool:
    return any("inject_hook.py" in (h.get("command") or "") for g in settings.get("hooks", {}).get("UserPromptSubmit", []) for h in g.get("hooks", []))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--settings", default=str(Path.home() / ".claude" / "settings.json"))
    ap.add_argument("--remove", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--inject", action="store_true", help="also register the synchronous live-injection hook (inert until STATEKEEPER_INJECT=1)")
    args = ap.parse_args()
    path = Path(args.settings)
    raw = path.read_text(encoding="utf-8")
    before = json.loads(raw)
    after = copy.deepcopy(before)
    hooks = after.setdefault("hooks", {})
    want_inject = (args.inject or has_inject(before)) and not args.remove
    for ev in EVENTS:
        groups = hooks.setdefault(ev, [])
        groups[:] = [g for g in groups if not is_ours(g)]
        if not args.remove:
            groups.append(group())
            if ev == "UserPromptSubmit" and want_inject:
                groups.append(inject_group())
        if not groups:
            del hooks[ev]
    # prove the merge: every pre-existing group is still present, unchanged, in the same order
    for ev, groups in before.get("hooks", {}).items():
        kept = [g for g in after["hooks"].get(ev, []) if not is_ours(g)]
        if kept != [g for g in groups if not is_ours(g)]:
            print(f"refusing: existing {ev} hooks would change", file=sys.stderr)
            return 2
    if {k: v for k, v in after.items() if k != "hooks"} != {k: v for k, v in before.items() if k != "hooks"}:
        print("refusing: non-hook settings would change", file=sys.stderr)
        return 2
    text = json.dumps(after, indent=2, ensure_ascii=False) + "\n"
    json.loads(text)
    if args.dry_run:
        print(text if len(text) < 0 else f"dry run: would write {len(text)} bytes, hooks events {sorted(after['hooks'])}")
        return 0
    backup = path.with_name(f"{path.name}.bak-{time.strftime('%Y%m%d-%H%M%S')}-statekeeper")
    shutil.copy2(path, backup)
    path.write_text(text, encoding="utf-8")
    json.loads(path.read_text(encoding="utf-8"))
    print(f"backup: {backup}\nwrote: {path} (valid JSON)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
