"""PreToolUse hook: reminders at the moment they can change what happens.

  cues         a cue is {"moment": "tool"|"prompt", "name", "when": <regex>, "text"} in <STATEKEEPER_HOME>/cues.json. A tool cue is matched against
               "<ToolName>: <tool input as JSON>"; a prompt cue against the person's prompt (shown by the inject hook). Each cue is shown at most once
               per session in CUE_AGAIN seconds.
  definitions  for a Write or Edit that declares a function, method or type, what the repository already has under that name or one sharing most
               of its words (reuse or extend it, or say why not).

Synchronous; prints a PreToolUse additionalContext JSON or nothing; never fails a call (errors print nothing and exit 0). Bounded: git grep with a
3 s timeout. Off with STATEKEEPER_SHADOW=0 or STATEKEEPER_TOOL_HOOK=0.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import shadow_hook  # noqa: E402

CAP = 3000
CUE_AGAIN = 30 * 60
DEFINITIONS = {
    ".py": re.compile(r"^[ \t]*(?:async[ \t]+)?(?:def|class)[ \t]+(\w+)", re.M),
    ".cs": re.compile(r"^[ \t]*(?:(?:public|private|internal|protected|static|abstract|sealed|partial|readonly|unsafe|new|async|override|virtual|extern)[ \t]+)*"
                      r"(?:(?:class|interface|struct|enum|record(?:[ \t]+(?:class|struct))?)[ \t]+(\w+)"
                      r"|(?<=[ \t])(?:[\w<>\[\],?.]+[ \t]+)(\w+)(?:<[^>]*>)?[ \t]*\((?!.*;))", re.M),
    ".js": re.compile(r"^[ \t]*(?:export[ \t]+)?(?:default[ \t]+)?(?:async[ \t]+)?function[ \t]+(\w+)"
                      r"|^[ \t]*(?:export[ \t]+)?(?:const|let)[ \t]+(\w+)[ \t]*=[ \t]*(?:async[ \t]*)?(?:\(|\w+[ \t]*=>)"
                      r"|^[ \t]*(?:export[ \t]+)?(?:default[ \t]+)?(?:abstract[ \t]+)?(?:class|interface|enum|type)[ \t]+(\w+)", re.M),
    ".ps1": re.compile(r"^[ \t]*function[ \t]+([\w-]+)", re.M),
}
DEFINITIONS[".ts"] = DEFINITIONS[".tsx"] = DEFINITIONS[".jsx"] = DEFINITIONS[".mjs"] = DEFINITIONS[".js"]
GENERIC = {"get", "set", "async", "the", "new", "init", "main", "test", "tests", "run", "and", "for", "with", "from", "when", "should", "returns"}
_CAMEL = re.compile(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])|[_\-]+")


def name_words(name: str) -> list[str]:
    return [w.lower() for w in _CAMEL.split(name) if w and w.lower() not in GENERIC and len(w) >= 3]


def declared(path: str, text: str) -> list[str]:
    rx = DEFINITIONS.get(Path(path).suffix.lower())
    if rx is None or not text:
        return []
    out = []
    for m in rx.finditer(text):
        name = next((g for g in m.groups() if g), None)
        if name and name not in out and not name.startswith("__") and name.lower() not in GENERIC:
            out.append(name)
    return out


def new_names(event: dict) -> tuple[str, list[str]]:
    ti = event.get("tool_input") or {}
    path = ti.get("file_path") or ""
    if event.get("tool_name") == "Write":
        new = declared(path, ti.get("content") or "")
        try:
            new = [n for n in new if n not in set(declared(path, Path(path).read_text(encoding="utf-8", errors="ignore")))]
        except OSError:
            pass
    elif event.get("tool_name") in ("Edit", "MultiEdit"):
        edits = ti.get("edits") or [ti]
        new = []
        for e in edits:
            old = set(declared(path, e.get("old_string") or ""))
            new += [n for n in declared(path, e.get("new_string") or "") if n not in old]
    else:
        return path, []
    return path, new[:6]


def existing(path: str, names: list[str], timeout: float = 3.0) -> list[str]:
    """Definitions in the repository of `path` named like `names`: the same name, or one sharing all its words but one (at least two)."""
    folder = Path(path).parent
    while not folder.exists() and folder != folder.parent:
        folder = folder.parent
    try:
        root = subprocess.run(["git", "rev-parse", "--show-toplevel"], cwd=folder, capture_output=True, text=True, timeout=timeout).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return []
    if not root:
        return []
    lines: list[str] = []
    here = os.path.normcase(os.path.abspath(path))
    for name in names:
        words = name_words(name)
        probe = max(words, key=len) if words else name
        try:
            r = subprocess.run(["git", "grep", "-n", "-I", "-i", "-E", rf"(def|class|function|interface|struct|enum|record|type|const|let|\)|>|[a-z])[ \t]+\w*{re.escape(probe)}\w*"],
                               cwd=root, capture_output=True, text=True, encoding="utf-8", errors="ignore", timeout=timeout)
        except (OSError, subprocess.SubprocessError):
            continue
        same, like = [], []
        for hit in r.stdout.splitlines()[:4000]:
            try:
                rel, ln, code = hit.split(":", 2)
            except ValueError:
                continue
            if os.path.normcase(os.path.abspath(os.path.join(root, rel))) == here:
                continue
            for other in declared(rel, code + "\n"):
                if other == name:
                    same.append(f"{rel}:{ln}")
                elif len(words) >= 2 and len(set(words) & set(name_words(other))) >= max(2, len(set(words)) - 1):
                    like.append(f"{other} ({rel}:{ln})")
        if same:
            lines.append(f"- `{name}` already exists: {', '.join(same[:3])}")
        if like:
            lines.append(f"- like `{name}`: {', '.join(list(dict.fromkeys(like))[:3])}")
    return lines


def cues(home: Path, moment: str, text: str, session: str) -> list[str]:
    try:
        items = json.loads((home / "cues.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    shown_file = home / "cues-shown.json"
    try:
        shown = json.loads(shown_file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        shown = {}
    now = time.time()
    out = []
    for c in items:
        if c.get("moment") != moment:
            continue
        key = f"{session}:{c.get('name')}"
        if now - shown.get(key, 0) < CUE_AGAIN:
            continue
        try:
            if re.search(c.get("when") or "$^", text, re.I):
                out.append(f"- {c.get('text')}")
                shown[key] = now
        except re.error:
            continue
    if out:
        shown = {k: v for k, v in shown.items() if now - v < 24 * 3600}
        shown_file.write_text(json.dumps(shown), encoding="utf-8")
    return out


def context(event: dict, home: Path) -> str:
    parts = []
    text = f"{event.get('tool_name', '')}: {json.dumps(event.get('tool_input') or {}, ensure_ascii=False)}"
    found = cues(home, "tool", text, str(event.get("session_id") or ""))
    if found:
        parts.append("A cue set for calls like this one (a reminder, not a block):\n" + "\n".join(found))
    if event.get("tool_name") in ("Write", "Edit", "MultiEdit"):
        path, names = new_names(event)
        if names:
            have = existing(path, names)
            if have:
                parts.append(f"Before writing {', '.join(f'`{n}`' for n in names)}, what the repository already has (reuse or extend it, or name why not):\n"
                             + "\n".join(have))
    return "\n\n".join(parts)[:CAP]


def main() -> int:
    if any(os.environ.get(v, "1").strip().lower() in shadow_hook.OFF for v in ("STATEKEEPER_SHADOW", "STATEKEEPER_TOOL_HOOK")):
        return 0
    try:
        event = shadow_hook.read_event()
        text = context(event, shadow_hook.HOME)
        if text:
            sys.stdout.write(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "additionalContext": text}}))
    except BaseException as e:
        try:
            shadow_hook.log({"event": "inject-error", "hook": "PreToolUse", "error": f"{type(e).__name__}: {str(e)[:200]}"})
        except BaseException:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
