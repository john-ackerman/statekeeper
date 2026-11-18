"""Where a state belongs: under the tool, project or topic it is about, not under the person or agent who said or did it.

"Jane fills .env.local for login-tool" filed under Jane is one of ~150 states about Jane, so naming the tool never finds it (Jane is generic and never
surfaced by name) and the tool's own thing looks empty. Filed under login-tool with Jane as the object, it surfaces when login-tool (or an alias) is named
and it describes the tool. `home_of` is the rule, applied at the claim boundary (claims.submit_claim) so every writer gets it; `plan` finds standing
states filed the old way, for a one-off backfill (supersede, never delete).
"""
from __future__ import annotations

from typing import Optional

from .retrieve import occurrences, split_identifiers, words
from .store import State, Store

PEOPLE = ("person", "agent")


def _kind(store: Store, name: Optional[str]) -> Optional[str]:
    t = store.find(name) if name else None
    return t.kind if t is not None else None


def home_of(store: Store, thing: str, obj: Optional[str], state_key: Optional[str] = None) -> tuple[str, Optional[str]]:
    """(thing, object) to file a claim under: a fact whose subject is a person or agent and whose object is something else is filed under that
    something, with the person or agent as its object. Keyed values (an agent's status) and facts with no object stay where they are."""
    if state_key or not obj or obj.strip().lower() == thing.strip().lower():
        return thing, obj
    if _kind(store, thing) in PEOPLE and _kind(store, obj) not in PEOPLE:
        return obj, thing
    return thing, obj


def plan(store: Store, *, max_mentions: int = 15) -> list[dict]:
    """Standing unkeyed states of people and agents that belong elsewhere: those with a non-person object, and those whose text names exactly one
    specific non-person thing (one named in at most `max_mentions` states' text, so a repo or a common tool is not taken for the topic)."""
    pool = store.searchable_states()
    texts = [words(split_identifiers(s.text)) for s in pool]
    labels = [(tid, label) for tid, label in store.labels()]
    things = {t.id: t for t in store.things()}
    mention_count: dict[int, int] = {}
    out = []
    for s in pool:
        subj = things.get(s.thing_id)
        if s.state_key or subj is None or subj.kind not in PEOPLE or s.status not in ("active", "proposed"):
            continue
        if s.object_id and things.get(s.object_id) and things[s.object_id].kind not in PEOPLE:
            out.append({"state": s.id, "from": subj.name, "to": things[s.object_id].name, "object": subj.name, "why": "object", "text": s.text})
            continue
        seq = words(split_identifiers(s.text))
        hits = set()
        for tid, label in labels:
            t = things.get(tid)
            if t is None or t.kind in PEOPLE or t.retired or len(label) < 4 or tid == s.thing_id:
                continue
            if occurrences(seq, split_identifiers(label)) > 0:
                if tid not in mention_count:
                    lab = split_identifiers(t.name)
                    mention_count[tid] = sum(1 for w in texts if occurrences(w, lab) > 0)
                if mention_count[tid] <= max_mentions:
                    hits.add(tid)
        if len(hits) == 1:
            tid = hits.pop()
            out.append({"state": s.id, "from": subj.name, "to": things[tid].name, "object": subj.name, "why": "named in text", "text": s.text})
    return out


def apply(store: Store, moves: list[dict], *, actor: str = "rule:rehome") -> list[tuple[int, int]]:
    """Re-files each planned state: a copy under its new thing (same text, kind, trust, status and evidence; the old subject as object), then the old
    one closed with the copy as its replacement. Nothing is deleted; `reopen` undoes a close."""
    done = []
    for m in moves:
        s: Optional[State] = store.get_state(m["state"])
        if s is None or not s.standing:
            continue
        ev = store.evidence_of([s.id])[s.id]
        new = store.add_state(m["to"], s.text, evidence=ev, writer=s.writer, trust=s.trust, learned_how=s.learned_how, session_id=s.session_id,
                              object=m["object"], kind=s.kind, is_state=s.is_state, as_of=s.as_of_utc, recorded=s.recorded_utc,
                              volatility=s.volatility, verify_handle=s.verify_handle, status=s.status)
        store.close_state(s.id, f"re-filed under {m['to']} ({m['why']})", replaced_by=new.id, actor=actor)
        done.append((s.id, new.id))
    return done
