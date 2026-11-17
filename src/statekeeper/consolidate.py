"""Ongoing consolidation: one bounded, logged, reversible pass that keeps the store from filling with the same fact many times.

A pass (`run_pass`) embeds anything new, merges duplicate proposals (dedupe.py), links near-duplicates as `related`, and records every merge as a
`same` link. It is safe to run repeatedly (idempotent), capped per pass, and never touches owner/verified/derived states. `validate` reads the outcome
log to say whether past merges look right: merges later reopened by a person, and misses logged against survivors. `dedupe_threshold` is a
parameter (param_history) so a change is a logged, revertible row, not a code edit. Not scheduled by anything yet (see docs/B section 12).
"""
from __future__ import annotations

from .dedupe import _unit, find_duplicates, merge_duplicates
from .embed import ensure_vectors
from .store import Store

DEFAULT_DUP = 0.95
DEFAULT_RELATED = 0.85
MAX_MERGES = 50
MAX_LINKS = 200


def run_pass(store: Store, embedder, *, apply: bool = True, actor: str = "rule:consolidate") -> dict:
    dup_t = store.get_param("dedupe_threshold", DEFAULT_DUP)
    rel_t = store.get_param("related_threshold", DEFAULT_RELATED)
    embedded = ensure_vectors(store, embedder)
    pairs = find_duplicates(store, embedder.model, dup_t)[:MAX_MERGES]
    merged = 0
    if apply:
        for pair in pairs:
            n = merge_duplicates(store, [pair], actor=actor)
            if n:
                store.link_states(pair[0], pair[1], "same", pair[2], actor=actor)
                merged += n
    linked = 0
    if apply:
        vectors = {r["owner_id"]: r["vector"] for r in store.vectors("state", embedder.model)}
        norm = {i: _unit(v) for i, v in vectors.items()}
        states = {s.id: s for s in store.searchable_states() if s.id in norm}
        ids = sorted(states)
        for x, a in enumerate(ids):
            for b in ids[x + 1:]:
                if linked >= MAX_LINKS:
                    break
                if states[a].thing_id == states[b].thing_id or states[a].status == "rejected" or states[b].status == "rejected":
                    continue
                sim = sum(p * q for p, q in zip(norm[a], norm[b]))
                if rel_t <= sim < dup_t and store.link_states(a, b, "related", round(sim, 4), actor=actor):
                    linked += 1
    return {"embedded": embedded, "duplicate_pairs_found": len(pairs), "merged": merged, "related_linked": linked, "dedupe_threshold": dup_t, "related_threshold": rel_t}


def validate(store: Store) -> dict:
    """What the outcome log and the event log say about past merges: how many, how many a person reopened, how many survivors later drew a miss."""
    merged = [r for r in store.db.execute("SELECT id, resolution FROM states WHERE resolution LIKE '%duplicate of #%'")]
    reopened = [r[0] for r in store.db.execute("SELECT DISTINCT state_id FROM events WHERE event_type = 'state-status' AND json_extract(payload, '$.from') = 'rejected' AND json_extract(payload, '$.to') = 'proposed'")]
    survivors = {int(x[1].split("duplicate of #")[1].split()[0].rstrip(".,:")) for x in merged if x[1] and "duplicate of #" in x[1]}
    missed_on_survivor = sum(1 for o in store.outcomes(kind="missed") if o.state_id in survivors)
    return {"merged_total": len(merged) + len(reopened), "currently_merged": len(merged), "reopened_by_a_person": len(reopened), "survivors": len(survivors), "misses_logged_on_survivors": missed_on_survivor}
