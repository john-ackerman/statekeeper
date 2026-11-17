"""Parameter suggestions from the outcome log (stage 0 of the learning ladder). Pure functions: they read counts and return suggestions; they never
apply anything. Applying is a separate, logged, reversible step (`Store.set_param` / `revert_param`), done by a person until the numbers prove a rule.

`summarize(store)` builds the counts from the log; `suggest(summary)` turns them into suggestions, each with the evidence behind it, and says nothing
when there is too little data (feedback is sparse and delayed).
"""
from __future__ import annotations

import json
import statistics

from .store import Store

MIN_VERIFIED = 20       # verified outcomes needed before a trust suggestion
MIN_INTERVALS = 10      # observed change intervals needed before a half-life suggestion
MIN_LABELLED = 40       # surfaced outcomes with a used/not-used label before a floor suggestion


def summarize(store: Store) -> dict:
    writers: dict[str, dict[str, int]] = {}
    for o in store.outcomes():
        if o.kind in ("verified_right", "verified_wrong"):
            w = writers.setdefault(o.source, {"verified_right": 0, "verified_wrong": 0})
            w[o.kind] += 1
    kinds: dict[str, int] = {}
    labelled_surfaced = used = 0
    outs = store.outcomes()
    used_ids = {(o.turn_ref, o.state_id) for o in outs if o.kind == "used"}
    labelled_turns = {o.turn_ref for o in outs if o.kind in ("used", "missed")}
    for o in outs:
        kinds[o.kind] = kinds.get(o.kind, 0) + 1
        if o.kind == "surfaced" and o.turn_ref in labelled_turns:
            labelled_surfaced += 1
            used += (o.turn_ref, o.state_id) in used_ids
    intervals: dict[str, list[float]] = {}
    for t in store.things():
        hist = [s for s in store.states_of(t.id, history=True) if s.state_key and s.status == "active"]
        by_key: dict[str, list] = {}
        for s in hist:
            by_key.setdefault(s.state_key, []).append(s)
        for states in by_key.values():
            states.sort(key=lambda s: s.recorded_utc)
            for a, b in zip(states, states[1:]):
                from datetime import datetime
                d = (datetime.fromisoformat(b.recorded_utc) - datetime.fromisoformat(a.recorded_utc)).total_seconds() / 86400
                intervals.setdefault(b.volatility, []).append(d)
    return {"counts": kinds, "writers": writers, "change_intervals_days": intervals, "floor": {"surfaced": labelled_surfaced, "used": used}}


def suggest(summary: dict) -> list[dict]:
    out = []
    for writer, c in summary.get("writers", {}).items():
        n = c["verified_right"] + c["verified_wrong"]
        if n >= MIN_VERIFIED:
            wrong = c["verified_wrong"] / n
            out.append({"param": f"trust.{writer}", "evidence": f"{c['verified_wrong']} of {n} verified outcomes wrong ({wrong:.0%})",
                        "suggestion": "lower the writer's trust ceiling one step" if wrong > 0.3 else "keep" if wrong > 0.1 else "raise the ceiling one step", "applied": False})
    for vol, gaps in summary.get("change_intervals_days", {}).items():
        if len(gaps) >= MIN_INTERVALS:
            med = statistics.median(gaps)
            out.append({"param": f"half_life_days.{vol}", "evidence": f"median {med:.2f} days between changes over {len(gaps)} observed changes",
                        "suggestion": f"set freshness half-life near {med * 2:.1f} days", "applied": False})
    f = summary.get("floor", {})
    if f.get("surfaced", 0) >= MIN_LABELLED:
        rate = f["used"] / f["surfaced"]
        out.append({"param": "relevance_floor", "evidence": f"{f['used']} of {f['surfaced']} surfaced states were used ({rate:.0%})",
                    "suggestion": "raise the floor by 0.02" if rate < 0.3 else "keep" if rate < 0.6 else "lower the floor by 0.02", "applied": False})
    return out
