"""Deterministic writer: the standing core of an earlier recall system (identity-like and relationship-like fragments) and its working note,
written as ordinary keyed states.

Identity-type fragments become states of the agent thing, relationship-type ones states of the person thing (a simplification: they mostly concern one
person and the team); the working note is one keyed state on its own thing, replaced each time it changes. Everything is read from the source and
nothing is written back. It exists so a state-first session start has the same standing material to draw on as the earlier system's, as plain state.

Names, writer and labels are parameters so the same writer serves any source; an unchanged fragment only moves its last-verified time.
"""
from __future__ import annotations

from typing import Callable

from ..store import Evidence, Store, now
from .common import observe_keyed

WRITER = "rule:core-notes"
NOTE = "working note"


def _text(f: dict) -> str:
    body = " ".join((f.get("content") or "").split())
    title = " ".join((f.get("summary") or "").split()) or body[:80]
    return f"{title}: {body}"[:520] if title and title != body[:80] else body[:520]


def observe_core(store: Store, fragments: list[dict], note: str | None, *, agent: str = "agent", person: str = "person",
                 writer: str = WRITER, source: str = "legacy", handle_prefix: str = "legacy-store",
                 clock: Callable[[], str] = now) -> dict:
    """`fragments` are dicts with `id`, `type` ("Identity" or "Relational"), `content`, optional `summary` and `created`."""
    out = {"core": 0, "changed": 0, "note": False}
    for thing, kind_type in ((agent, "Identity"), (person, "Relational")):
        mine = [f for f in fragments if f.get("type") == kind_type]
        if not mine:
            continue
        if not store.find(thing):
            store.add_thing(thing, "agent" if thing == agent else "person", actor=writer)
        obs = {f"core.{f['id']}": _text(f) for f in mine}
        r = observe_keyed(store, writer=writer, thing=thing, parent=None, observations=obs,
                          evidence=Evidence("tool", f"{source} core {kind_type}", f"{len(mine)} fragments"), handle=f"{handle_prefix} list --type {kind_type}",
                          as_of=mine[0].get("created"), clock=clock, volatility="rule")
        out["core"] += len(obs)
        out["changed"] += len(r["changed"])
    if note and note.strip():
        r = observe_keyed(store, writer=writer, thing=NOTE, parent=None, observations={"note": " ".join(note.split())[:1500]},
                          evidence=Evidence("tool", f"{source} note --newest", None), handle=f"{handle_prefix} note --newest", as_of=None, clock=clock, volatility="live")
        out["note"] = True
        out["changed"] += len(r["changed"])
    return out
