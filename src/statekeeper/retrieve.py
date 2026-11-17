"""State-first retrieval: from an input to the current states it bears on.

Three ways a state is reached:
  named    the input spells a thing's name or alias whole;
  spelled  a near spelling (with a guard: a truncation or prefix of a name is another word, not a typo);
  meaning  the input is close in meaning to a state's text (vectors over states, proposed ones included), the matched state is shown first.
Nothing is better than noise: below the floor, nothing surfaces. Raw evidence is never injected; each state carries one evidence line and a reference.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from .embed import Embedder, cosine
from .store import State, Store, Thing, likely_replaces

DEFAULT_FLOOR = 0.72
LIVE_STALE_HOURS = 6
QUERY_CHARS = 1200
_WORDS = re.compile(r"[^\W_]+", re.UNICODE)


def words(text: str) -> list[str]:
    return [w.lower() for w in _WORDS.findall(text)]


def occurrences(seq: list[str], label: str) -> int:
    target = words(label)
    if not target:
        return 0
    n = len(target)
    return sum(1 for i in range(len(seq) - n + 1) if seq[i:i + n] == target)


def _distance(a: str, b: str, limit: int) -> int:
    prev = list(range(len(b) + 1))
    for i in range(1, len(a) + 1):
        cur = [i] + [0] * len(b)
        for j in range(1, len(b) + 1):
            cur[j] = min(cur[j - 1] + 1, prev[j] + 1, prev[j - 1] + (a[i - 1] != b[j - 1]))
        if min(cur) > limit:
            return limit + 1
        prev = cur
    return prev[-1]


def fuzzy_match(seq: list[str], label: str) -> Optional[tuple[float, str]]:
    """A near spelling of `label` in `seq`: a run of one to three words written together within an edit or two of the label written together
    ("date picker" for "datepicker"), but not the label's own words in order and never a truncation or prefix ("claud" is not Claude, "example" is
    not ExampleApp). Labels under five letters are never matched fuzzily."""
    lw = words(label)
    target = "".join(lw[1:] if len(lw) > 1 and lw[0] in ("the", "a", "an") else lw)
    if len(target) < 5 or occurrences(seq, label) > 0:
        return None
    allowed = 2 if len(target) >= 9 else 1
    best: Optional[tuple[float, str]] = None
    for size in (1, 2, 3):
        for i in range(len(seq) - size + 1):
            run = seq[i:i + size]
            if run == lw:
                continue
            joined = "".join(run)
            if abs(len(joined) - len(target)) > allowed:
                continue
            if len(joined) != len(target) and (target.startswith(joined) or joined.startswith(target)):
                continue
            d = _distance(joined, target, allowed)
            if d <= allowed:
                score = 1.0 - d / len(target)
                if best is None or score > best[0]:
                    best = (score, " ".join(run))
    return best


@dataclass
class Item:
    thing: Thing
    why: str                       # named | spelled "x" | meaning 0.78
    score: float
    states: list[State]
    pinned: Optional[int] = None   # the state that matched by meaning, shown first
    evidence: dict = field(default_factory=dict)


@dataclass
class Surfaced:
    block: str
    items: list[Item]


def _freshness(s: State, today: datetime) -> str:
    def days(ts: Optional[str]) -> Optional[int]:
        if not ts:
            return None
        try:
            return max(0, (today - datetime.fromisoformat(ts[:19]).replace(tzinfo=timezone.utc)).days)
        except ValueError:
            return None
    verified = days(s.confirmed_utc)
    if s.volatility == "live" and s.trust != "owner":
        # a live state (a build, a PR's CI, an agent's status) goes stale in hours: say so, and how to re-check, rather than state it as current
        try:
            age_h = (today - datetime.fromisoformat((s.confirmed_utc or s.recorded_utc)[:19]).replace(tzinfo=timezone.utc)).total_seconds() / 3600
        except ValueError:
            age_h = 1e9
        if age_h > LIVE_STALE_HOURS:
            how = f"; re-check: {s.verify_handle[:80]}" if s.verify_handle else ""
            return f"STALE, last verified {int(age_h)}h ago{how}" if s.confirmed_utc else f"STALE, never re-verified{how}"
        return f"verified {int(age_h * 60)}m ago" if age_h < 1 else f"verified {int(age_h)}h ago"
    if s.trust == "owner":
        return "owner-stated"
    if verified is not None:
        return "verified today" if verified == 0 else f"verified {verified}d ago"
    return {"claimed": "agent-claimed, unverified", "extracted": "extracted, unreviewed"}.get(s.trust, "unverified")


def _first_line(text: str, width: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= width else text[:width - 1] + "…"


def candidates(store: Store, text: str, embedder: Optional[Embedder], floor: float, limit: int) -> list[Item]:
    seq = words(text)
    found: dict[int, Item] = {}

    def keep(thing: Thing, why: str, score: float, pinned: Optional[int] = None) -> None:
        held = found.get(thing.id)
        if held is None or (why == "named" and not held.why.startswith("named")) or (held.why != "named" and score > held.score):
            found[thing.id] = Item(thing, why, score, [], pinned)

    for tid, label in store.labels():
        thing = store.get(tid)
        if thing is None or thing.retired:
            continue
        if occurrences(seq, label) > 0:
            keep(thing, "named", 1.0)
        elif (m := fuzzy_match(seq, label)):
            keep(thing, f'spelled "{m[1]}"', m[0])
    if embedder is not None and text.strip():
        from .embed import ensure_vectors
        try:
            ensure_vectors(store, embedder, limit=64)
            query = embedder.embed([text[-QUERY_CHARS:]])[0]
        except Exception:
            query = None
        if query is not None:
            by_state = {s.id: s for s in store.searchable_states()}
            best: dict[int, tuple[float, int]] = {}
            for v in store.vectors("state", embedder.model):
                s = by_state.get(v["owner_id"])
                if s is None:
                    continue
                c = cosine(query, v["vector"])
                if c >= floor and (s.thing_id not in best or c > best[s.thing_id][0]):
                    best[s.thing_id] = (c, s.id)
            for tid, (c, sid) in best.items():
                thing = store.get(tid)
                if thing is not None and tid not in found:
                    keep(thing, f"meaning {c:.2f}", c, sid)
    order = sorted(found.values(), key=lambda i: (i.why != "named", -i.score, i.thing.id))
    return order[:limit]


def surface(store: Store, text: str, embedder: Optional[Embedder] = None, *, floor: Optional[float] = None, max_things: int = 3,
            states_each: int = 2, budget: int = 2500, today: Optional[datetime] = None, log_turn: Optional[str] = None,
            exclude_kinds: tuple = (), exclude_names: tuple = (), skip_state_ids: frozenset = frozenset()) -> Surfaced:
    """The state block for an input: at most `max_things` things, `states_each` states each, newest first (the state that matched by meaning first),
    each with its freshness label, `[newer than #N]` where it likely replaces an older standing one, and one evidence line; within `budget` characters,
    saying what it left out. Empty when nothing bears on the input."""
    today = today or datetime.now(timezone.utc)
    floor = store.get_param("relevance_floor", DEFAULT_FLOOR) if floor is None else floor
    items = candidates(store, text, embedder, floor, max_things + 6)
    if not items:
        return Surfaced("", [])
    dropped: list[Item] = []
    lines = ["Current state for this input (each with how sure to be; `[#id]` is the state, evidence is one line; raw history is not shown):"]
    shown: list[Item] = []
    omitted: list[str] = []
    for item in items:
        if len(shown) >= max_things:
            omitted.append(item.thing.name)
            dropped.append(item)
            continue
        if item.thing.kind in exclude_kinds or item.thing.name.lower() in exclude_names:
            continue                      # e.g. the agent and people are in the session-start core, not repeated per prompt
        states = [s for s in store.states_of(item.thing.id) if s.id not in skip_state_ids]
        if not states:
            continue
        ev = store.evidence_of([s.id for s in states])
        hints = likely_replaces(states, ev)
        order = [s for s in sorted(states, key=lambda s: s.recency, reverse=True) if s.id == item.pinned] + \
                [s for s in sorted(states, key=lambda s: s.recency, reverse=True) if s.id != item.pinned]
        keyed = [s for s in order if s.state_key and s.status == "active"]
        item.states = order[:states_each] + [s for s in keyed if s not in order[:states_each]][:4]   # a thing's current keyed states all show
        item.evidence = ev
        chunk = [f"\n{item.thing.name} ({item.thing.kind}; {item.why}):"]
        seen_ev: set = set()
        for s in item.states:
            tags = [_freshness(s, today)] + (["proposed"] if s.status == "proposed" else []) + ([f"newer than #{hints[s.id]}"] if s.id in hints else [])
            rel = f" -> {s.object_name}" if s.object_name else ""
            label = f"{s.state_key}: " if s.state_key else ""
            chunk.append(f"  [#{s.id}] {label}{_first_line(s.text, 230)}{rel} ({'; '.join(tags)})")
            if ev.get(s.id):
                e = ev[s.id][0]
                if e.excerpt and (e.ref_type, e.ref) not in seen_ev:
                    seen_ev.add((e.ref_type, e.ref))
                    chunk.append(f'    {e.ref_type} {e.ref}: "{_first_line(e.excerpt, 140)}"')
        block = "\n".join(chunk)
        if sum(len(x) + 1 for x in lines) + len(block) > budget:
            omitted.append(item.thing.name)
            dropped.append(item)
            continue
        lines.append(block)
        shown.append(item)
    if log_turn:
        for item in shown:
            for s in item.states:
                store.log_outcome(s.id, "surfaced", source="surface", turn_ref=log_turn, detail=json.dumps({"why": item.why, "status": s.status, "trust": s.trust}))
        for item in dropped:
            top = sorted(store.states_of(item.thing.id), key=lambda s: s.recency, reverse=True)[:1]
            if top:
                store.log_outcome(top[0].id, "omitted", source="surface", turn_ref=log_turn, detail=json.dumps({"why": item.why}))
    if not shown:
        return Surfaced("", [])
    if omitted:
        lines.append(f"\n{len(omitted)} omitted: {', '.join(omitted[:4])}")
    return Surfaced("\n".join(lines), shown)
