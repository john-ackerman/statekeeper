"""Offline commitment detection: which first-person promises in an agent's turn were still unfulfilled when the turn ended.

Pure functions over text and a list of the turn's actions; no model, no store, not wired to any hook or prompt block. A turn is an ordered list of
events: assistant text (str) and tool calls (Action). A commitment is a sentence where the agent says it will do something; it is fulfilled when a
LATER action in the same turn touches what it named (shared content stem) or is of the class its verb implies (a "check" is met by any read-class call).
Precision is measured by replaying stored turns (tools/commitment_replay.py), and is the agent's own judgment until the owner reviews it.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Sequence, Union

FUTURE = re.compile(r"\b(i'?ll|i will|i'?m going to|i am going to|let me|next,? i'?ll|i'?ll then)\b", re.I)
SKIP = re.compile(r"(\bif\b|\bshould i\b|\bwant me\b|\bi can\b|\bwe could\b|\bwon'?t\b|\bwill not\b|\bnot going to\b|\bi'?ll (skip|stop|leave)\b|"
                  r"\blet me know\b|\blet you know\b|\bwhether\b|\bsay the word\b|\bgo ahead\b|\buntil then\b|\beach time\b|\bevery time\b|"
                  r"^\W*(other )?option\b|\bsay (so|yes|go)\b|\bon your (ok|go|word|say)\b|\bwhen we get there\b|\blater\b|\bunless you\b|\bonce you\b|\bwhen you\b)", re.I)
SPLIT = re.compile(r"(?<=[.!?])\s+|\n+")
STOP = {"that", "this", "with", "then", "next", "again", "later", "will", "going", "let", "the", "and", "for", "from", "into", "also", "now", "just", "first", "once", "after", "before",
        "them", "they", "your", "have", "them", "there", "when", "what", "some", "more", "make", "sure"}
READ = {"read", "grep", "glob", "bash", "webfetch", "websearch", "toolsearch"}
WRITE = {"edit", "write", "bash", "notebookedit", "multiedit"}
VERB_CLASS = {"check": READ, "look": READ, "read": READ, "see": READ, "search": READ, "grep": READ, "find": READ, "verify": READ, "inspect": READ, "confirm": READ, "trace": READ,
              "run": {"bash"}, "test": {"bash"}, "build": {"bash"}, "commit": {"bash"}, "push": {"bash"}, "rerun": {"bash"}, "re-run": {"bash"},
              "write": WRITE, "add": WRITE, "edit": WRITE, "fix": WRITE, "update": WRITE, "create": WRITE, "make": WRITE, "implement": WRITE, "remove": WRITE, "change": WRITE}


@dataclass(frozen=True)
class Action:
    tool: str
    detail: str = ""


@dataclass(frozen=True)
class Commitment:
    sentence: str
    verb: str
    stems: frozenset
    position: int


Event = Union[str, Action]


def _stems(text: str) -> frozenset:
    return frozenset(w[:5] for w in re.findall(r"[a-z0-9]{4,}", text.lower()) if w not in STOP)


def find_commitments(text: str, position: int = 0) -> list[Commitment]:
    found = []
    for sentence in SPLIT.split(text):
        s = sentence.strip()
        if not s or s.endswith("?") or s.startswith((">", "`", "-", "|")) or not FUTURE.search(s) or SKIP.search(s):
            continue
        m = FUTURE.search(s)
        if m.start() > 0 and re.search(r"\b(you|we|they|he|she|agent|builder|shepherd)\b", s[:m.start()], re.I):
            continue                                                     # someone else's plan
        after = re.findall(r"[a-z-]+", s[m.end():].lower())
        found.append(Commitment(s, after[0] if after else "", _stems(s[m.end():]), position))
    return found


def _met(c: Commitment, a: Action) -> bool:
    tool = a.tool.lower()
    if c.stems & _stems(f"{a.tool} {a.detail}"):
        return True
    return tool in VERB_CLASS.get(c.verb, set()) and (c.verb in ("check", "look", "read", "see", "search", "grep", "find", "verify", "inspect", "confirm", "trace", "run", "test", "build"))


_PUNCT = re.compile(r"[{}\"]")
_FUTURE_ANY = re.compile(r"\b(will|i'?ll|going to|let me|when it|once it|until)\b", re.I)


def fulfilment_chunks(events: Sequence[Event]) -> list[str]:
    """What later events can count as fulfilment: actions, and sentences that report something done. A sentence that is itself a promise (or any
    future-tense plan) is excluded, because restating "I'll report when it finishes" does not fulfil it."""
    out = []
    for e in events:
        if isinstance(e, Action):
            out.append(e.tool + ": " + _PUNCT.sub(" ", e.detail)[:200])
            continue
        for s in SPLIT.split(e):
            s = s.strip()
            if len(s) >= 25 and not _FUTURE_ANY.search(s) and not find_commitments(s):
                out.append(s)
    return out


def closure_by_meaning(commitment: str, chunks: Sequence[str], embedder, threshold: float = 0.78) -> tuple[float, str, bool]:
    """Semantic closure: the later text or action (`chunks`) closest in meaning to the commitment sentence, its cosine, and whether it clears the
    threshold. Offline; the embedder is the same local one retrieval uses. Returns (0.0, "", False) when there is nothing later."""
    from .embed import cosine
    if not chunks:
        return 0.0, "", False
    vectors = embedder.embed([commitment, *chunks])
    best = max(range(len(chunks)), key=lambda i: cosine(vectors[0], vectors[i + 1]))
    score = cosine(vectors[0], vectors[best + 1])
    return score, chunks[best], score >= threshold


def open_commitments(events: Sequence[Event]) -> list[Commitment]:
    """The commitments made in this turn's text that no later action in the same turn fulfils."""
    out = []
    for i, e in enumerate(events):
        if isinstance(e, str):
            for c in find_commitments(e, i):
                if not any(isinstance(a, Action) and _met(c, a) for a in events[i + 1:]):
                    out.append(c)
    return out
