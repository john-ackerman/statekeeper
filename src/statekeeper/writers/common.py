"""Shared mechanics of a deterministic writer: write keyed observations of one thing as verified states.

An unchanged value moves last-verified only (`confirm`, outcome verified_right); a changed value is written as a new verified state that supersedes the
old one by key (outcome verified_wrong on the old one). Nothing is written when the writer has nothing to say.
"""
from __future__ import annotations

from typing import Callable

from ..store import Evidence, Store, now

NOT_OBSERVED = "no longer observed"


def close_unobserved(store: Store, *, writer: str, seen: set[str], prefix: str | None = None, reason: str = NOT_OBSERVED) -> list[str]:
    """Closes (kept as history, with `reason`) the standing states `writer` wrote on things it did not see this run, so a value nobody re-reads stops
    standing as current. Only that writer's states, on things whose name starts with `prefix` when given. Call it only after a COMPLETE read of the
    source: a failed or partial read must close nothing. Returns the names of the things closed."""
    closed: set[str] = set()
    for s in store.searchable_states():
        if s.writer != writer or s.status != "active" or s.thing_name in seen or (prefix and not s.thing_name.startswith(prefix)):
            continue
        store.close_state(s.id, reason, actor=writer)
        closed.add(s.thing_name)
    return sorted(closed)


def observe_keyed(store: Store, *, writer: str, thing: str, parent: str | None, observations: dict[str, str], evidence: Evidence, handle: str,
                  as_of: str | None, clock: Callable[[], str] = now, volatility: str = "live") -> dict:
    """Returns {key: state, ..., "changed": [keys whose value differs from the previous one]}."""
    if parent and not store.find(parent):
        store.add_thing(parent, actor=writer)
    if not store.find(thing):
        store.add_thing(thing, parent=parent, actor=writer)
    t = store.find(thing)
    current = {s.state_key: s for s in store.states_of(t.id) if s.state_key and s.status == "active"}
    result: dict = {"changed": []}
    at = clock()
    for key, text in observations.items():
        have = current.get(key)
        if have and have.text == text:
            result[key] = store.confirm(have.id, actor=writer, at=at)
            store.log_outcome(have.id, "verified_right", source=writer, detail=f"re-read at {at}")
            continue
        if have:
            store.log_outcome(have.id, "verified_wrong", source=writer, detail=f"outside read at {at} says: {text}")
        result[key] = store.add_state(thing, text, evidence=[evidence], writer=writer, trust="verified", learned_how="read", state_key=key, is_state=True,
                                      as_of=as_of, volatility=volatility, verify_handle=handle, recorded=at)
        result["changed"].append(key)
    return result
