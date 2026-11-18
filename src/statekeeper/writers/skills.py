"""Deterministic writer: the skills installed for the agent (SKILL.md files) as states, so "you have a script/CLI for that" is something the store
can remind the agent of without being told again. One thing per skill ("<name> skill") with up to two states:

  description   the skill's own description (frontmatter `description`)
  commands      the command-line tools its instructions run (the first word of `code spans` that read like a command), e.g. "ldcli, jq"

They are ordinary (unkeyed) states on purpose: keyed values surface only when their thing is named, and these must surface by meaning and by the
requests they answer (questions.py). Read from the files (trust verified, learned read, volatility stable), re-read each run: an unchanged text only
moves last-verified, a changed one replaces the old state (closed with the new one as its replacement). A deleted skill keeps its last states.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Callable, Iterable

from ..store import Evidence, Store, now

WRITER = "rule:skills"
_SPAN = re.compile(r"`([a-z][a-z0-9_.-]*)(?: [^`\n]{1,200})?`")
COMMON = {"git", "cd", "ls", "cat", "echo", "python", "python3", "node", "npm", "npx", "dotnet", "pwsh", "powershell", "bash", "sh", "cmd", "grep", "sed",
          "awk", "find", "rm", "cp", "mv", "mkdir", "curl", "true", "false", "null", "none", "yes", "no", "the", "a", "an", "and", "or", "if", "for",
          "in", "is", "not", "with", "to", "of", "on", "at", "by", "it", "this", "that", "use", "run", "see", "set", "get", "add"}


def parse_skill(text: str) -> tuple[str, str]:
    """(name, description) from a SKILL.md's frontmatter ('' when absent). Handles `description: >` folded blocks."""
    m = re.match(r"^---\s*\n(.*?)\n---", text, re.S)
    if not m:
        return "", ""
    front = m.group(1)
    name = (re.search(r"^name:\s*(.+)$", front, re.M) or [None, ""])[1].strip().strip("'\"")
    d = re.search(r"^description:\s*(.*)$((?:\n[ \t]+.*)*)", front, re.M)
    desc = ""
    if d:
        first = d.group(1).strip()
        rest = " ".join(x.strip() for x in d.group(2).splitlines() if x.strip())
        desc = (rest if first in (">", "|", ">-", "|-", "") else f"{first} {rest}").strip().strip("'\"")
    return name, " ".join(desc.split())


def commands(text: str, max_n: int = 8) -> list[str]:
    """Command-line tools a skill's instructions run: the first word of code spans with arguments, not shell builtins or common runtimes,
    seen at least twice (or once with arguments that look like a subcommand)."""
    counts: dict[str, int] = {}
    for m in _SPAN.finditer(text):
        word = m.group(1)
        if word in COMMON or "." in word and not word.endswith((".sh", ".exe")) or len(word) < 2:
            continue
        if m.group(0).count(" ") == 0:
            continue                                     # a bare `word` is a name, not a command line
        counts[word] = counts.get(word, 0) + 1
    return [w for w, _ in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])) if counts[w] >= 2][:max_n]


def observe_skills(store: Store, dirs: Iterable[str | Path], clock: Callable[[], str] = now) -> dict:
    out = {"skills": 0, "changed": 0}
    for d in dirs:
        for path in sorted(Path(d).expanduser().glob("*/SKILL.md")):
            try:
                text = path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            name, desc = parse_skill(text)
            name = name or path.parent.name
            if not desc:
                continue
            texts = {"description": f"The {name} skill ({path.as_posix()}): {desc}"[:900]}
            cmds = commands(text)
            if cmds:
                texts["commands"] = f"The {name} skill runs these command-line tools: {', '.join(cmds)}."
            thing = f"{name} skill"
            t = store.find(thing)
            mine = [x for x in store.states_of(t.id) if x.writer == WRITER and x.thing_id == t.id] if t else []
            at = clock()
            for part, body in texts.items():
                lead = texts[part].split(":", 1)[0] if part == "description" else f"The {name} skill runs"
                have = next((x for x in mine if x.text.startswith(lead)), None)
                if have and have.text == body:
                    store.confirm(have.id, actor=WRITER, at=at)
                    continue
                new = store.add_state(thing, body, evidence=[Evidence("file", path.as_posix(), None)], writer=WRITER, trust="verified", learned_how="read",
                                      kind="belief", volatility="stable", verify_handle=f"read {path.as_posix()}", recorded=at)
                if have:
                    store.close_state(have.id, "the skill file changed", replaced_by=new.id, actor=WRITER)
                out["changed"] += 1
            out["skills"] += 1
    return out
