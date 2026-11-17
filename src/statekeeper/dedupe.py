"""Merge-by-meaning: two proposals that say the same thing (the same fact extracted from two messages) become one state with both pieces of evidence.

Runs over stored vectors (after `ensure_vectors`). Only a PROPOSED, extracted/claimed state is ever merged away: it is rejected with the reason
"duplicate of #N" (kept as history, not deleted) and its evidence is added to the survivor. The survivor is the higher-trust state, then the older one.
Standing owner/verified/derived states are never rejected by this pass.
"""
from __future__ import annotations

import math

from .store import Store

MERGEABLE = ("extracted", "claimed")


def _unit(v):
    n = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / n for x in v]


def find_duplicates(store: Store, model: str, threshold: float = 0.95) -> list[tuple[int, int, float]]:
    """(survivor_id, duplicate_id, cosine) for every proposed state that duplicates another standing state, best match per duplicate."""
    vectors = {r["owner_id"]: _unit(r["vector"]) for r in store.vectors("state", model)}
    states = {s.id: s for s in store.searchable_states() if s.id in vectors}
    out = []
    for b in sorted(states.values(), key=lambda s: s.id):
        if b.status != "proposed" or b.trust not in MERGEABLE:
            continue
        best = None
        for a in states.values():
            if a.id == b.id or a.status not in ("active", "proposed"):
                continue
            if a.status == "proposed" and (a.id > b.id and a.trust in MERGEABLE):
                continue                       # between two proposals the older survives; handle the pair from the newer one
            sim = sum(x * y for x, y in zip(vectors[a.id], vectors[b.id]))
            if sim >= threshold and (best is None or sim > best[1]):
                best = (a.id, sim)
        if best:
            out.append((best[0], b.id, round(best[1], 4)))
    return out


def merge_duplicates(store: Store, pairs: list[tuple[int, int, float]], *, actor: str = "rule:dedupe") -> int:
    done = 0
    for survivor_id, dup_id, _ in pairs:
        dup, keep = store.get_state(dup_id), store.get_state(survivor_id)
        if not dup or not keep or dup.status != "proposed" or keep.status not in ("active", "proposed"):
            continue
        for e in store.evidence_of([dup_id]).get(dup_id, []):
            store.add_evidence(survivor_id, e, actor=actor)
        store.reject(dup_id, f"duplicate of #{survivor_id}", actor=actor)
        done += 1
    return done
