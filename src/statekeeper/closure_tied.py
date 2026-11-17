"""Artifact-tied commitment closure: a commitment that names a PR or a build/run closes when the store's writer-observed state for that artifact
reaches a terminal value AFTER the commitment was made. The truth is the deterministic writer's read (writers/github_pr.py, writers/ado_build.py),
never similarity of wording. A commitment that names no artifact, or one the store has not observed, is "untied": left to the open-commitments
list, not guessed. Offline; nothing here is wired to a hook.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .store import Store

PR_REF = re.compile(r"(?:\bPR\s+#?|\bpull request\s+#?|(?<![\w&])#)(\d{3,6})\b", re.I)
BUILD_REF = re.compile(r"\b(?:CI\s+run|run|build|pipeline run)\s+#?(\d{4,7})\b", re.I)
TERMINAL = {
    "build.state": ("succeeded", "failed", "partially succeeded", "canceled"),
    "pr.ci": ("CI green", "CI red"),
    "pr.state": ("merged", "closed"),
    "pr.review": ("approved", "changes requested"),
}


@dataclass
class Tie:
    sentence: str
    artifacts: list[tuple[str, str]]
    status: str                                   # closed | open | untied
    closing_states: list[int] = field(default_factory=list)


def artifacts_in(sentence: str) -> list[tuple[str, str]]:
    found = [("pr", m) for m in PR_REF.findall(sentence)] + [("build", m) for m in BUILD_REF.findall(sentence)]
    return list(dict.fromkeys(found))


def _things(store: Store, kind: str, ident: str):
    suffix = f" PR {ident}" if kind == "pr" else f" #{ident}"
    return [t for t in store.things() if not t.retired and t.name.endswith(suffix)]


def tie_commitments(store: Store, commitments: list[tuple[str, str]]) -> list[Tie]:
    """`commitments` are (sentence, made_utc) with made_utc in the store's timestamp format. Returns one Tie per commitment."""
    out = []
    for sentence, made in commitments:
        refs = artifacts_in(sentence)
        things = [t for kind, ident in refs for t in _things(store, kind, ident)]
        if not things:
            out.append(Tie(sentence, refs, "untied"))
            continue
        closing = []
        for t in things:
            for s in store.states_of(t.id, history=True):
                if s.state_key in TERMINAL and s.status == "active" and s.recorded_utc > made and any(s.text.startswith(w) for w in TERMINAL[s.state_key]):
                    closing.append(s.id)
        out.append(Tie(sentence, refs, "closed" if closing else "open", closing))
    return out
