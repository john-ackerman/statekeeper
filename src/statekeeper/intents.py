"""Questions whose answer is a kind of state rather than a subject the input names: "did X ship / is X in prod" (the latest release, and X's PR),
"what went out in the last release", and "is anything waiting on me". Each returns the states that answer it, newest first; retrieve.select gives
them a fixed rank above meaning matches. Patterns are deliberately narrow: a false positive injects a release or a to-do list into an unrelated turn.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone

SHIP = re.compile(r"\b(did|has|have|is|are|was|were|what|which|when|whether)\b.{0,80}?"
                  r"\b(ship|ships|shipped|went out|go out|gone (from|in) prod|in prod|to prod|on prod|released|deployed|prod release|last release|latest release)\b",
                  re.I)
LAST_RELEASE = re.compile(r"\b(last|latest|recent|most recent|newest|current)\b.{0,20}\b(prod |production )?(release|deploy|deployment|build)\b", re.I)
RELEASE_THING = re.compile(r"^(build|release)\s+v?\d+(\.\d+){2,}$", re.I)
WAITING = re.compile(r"\b(waiting (on|for) me|blocked on me|stuck (on|waiting on) me|needs? me|on my plate|for me to (do|decide|run|answer)|"
                     r"what do i (need|have) to|anything (for|on) me)\b", re.I)
PENDING_JOHN = re.compile(r"\b(pending (for|on) John|waiting (for|on) John|awaiting John|John still needs to|needs John's (decision|sign-off|go|go-ahead|answer|approval))", re.I)
WAITING_HOURS = 72          # a "pending for John" note older than this (vs the newest note in the store) is more likely done than still pending
RELEASE_STATES = 2


def _at(ts: str) -> datetime:
    return datetime.fromisoformat(ts[:19].replace(" ", "T")).replace(tzinfo=timezone.utc)


_NOT_YET = re.compile(r"\b(not|n't|never|until|before|after|once|when|if)\s+(yet\s+|been\s+|being\s+)?$", re.I)


def asks_ship(text: str) -> bool:
    """Whether the input asks if something shipped / what shipped. "merged but not deployed yet" or "once it is deployed" describe a condition,
    not a question about a release."""
    if LAST_RELEASE.search(text):
        return True
    for m in SHIP.finditer(text):
        if not _NOT_YET.search(text[:m.start(2)]):
            return True
    return False


def latest_release(pool: list) -> list:
    """The newest release's states (a thing named 'Build 7.15.95.13801' or 'Release 7.15.93'), newest first."""
    by_thing: dict[int, list] = {}
    for s in pool:
        if not s.state_key and RELEASE_THING.match(s.thing_name.strip()):
            by_thing.setdefault(s.thing_id, []).append(s)
    if not by_thing:
        return []
    newest = max(by_thing.values(), key=lambda sts: max(x.recency for x in sts))
    return sorted(newest, key=lambda x: x.recency, reverse=True)[:RELEASE_STATES]


def waiting_on_john(pool: list, text: str) -> list:
    """For "is anything waiting on me": recent work notes that say something is pending for John, newest first."""
    if not WAITING.search(text):
        return []
    hits = [s for s in pool if s.volatility == "work" and not s.state_key and s.thing_name.lower() != "working note" and PENDING_JOHN.search(s.text)]
    if not hits:
        return []
    newest = max(_at(s.recorded_utc) for s in pool if s.recorded_utc)
    recent = [s for s in hits if (newest - _at(s.recorded_utc)).total_seconds() <= WAITING_HOURS * 3600]
    return sorted(recent, key=lambda s: s.recency, reverse=True)
