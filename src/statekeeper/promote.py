"""Proposals that have earned standing: an extracted or claimed state becomes active when something outside the extractor corroborates it.

Corroborated means any of:
  - repeated evidence: it cites at least two distinct sources (two messages, a message and a file, ...), or a duplicate of it was merged into it
    (a `same` link: it was learned twice);
  - use: answers carried it (`used` outcomes) in at least USED_MIN distinct turns;
  - an owner statement or a verified read that says the same (a standing owner/verified state on the same thing within SAME_TEXT in meaning).
And never when it was contradicted, flagged, said to be noise, or weighted down. Acceptance keeps the trust as it was (an extracted state stays
"unreviewed" in its marker); it only stops being a proposal, with the reason in its resolution. `Store.reopen` undoes nothing here; to undo, reject it.
"""
from __future__ import annotations

from typing import Optional

from .store import Store

USED_MIN = 2
SAME_TEXT = 0.93
ACTOR = "rule:corroborated"


def candidates(store: Store, embedder_model: Optional[str] = None) -> list[tuple[int, str]]:
    """(state id, reason) for proposals that are corroborated and not disputed."""
    props = [s for s in store.searchable_states() if s.status == "proposed" and not s.state_key]
    if not props:
        return []
    ids = [s.id for s in props]
    ev = store.evidence_of(ids)
    bad = {r[0] for r in store.db.execute("SELECT DISTINCT state_id FROM outcomes WHERE kind IN ('contradicted', 'unhelpful', 'verified_wrong')")}
    bad |= {r[0] for r in store.db.execute("SELECT state_id FROM flags WHERE resolved_utc IS NULL")}
    weights = store.state_weights() if hasattr(store, "state_weights") else {}
    used = {r[0]: r[1] for r in store.db.execute("SELECT state_id, COUNT(DISTINCT turn_ref) FROM outcomes WHERE kind = 'used' GROUP BY state_id")}
    same = {r[0] for r in store.db.execute("SELECT a_state FROM state_links WHERE relation = 'same' UNION SELECT b_state FROM state_links WHERE relation = 'same'")}
    vec = {}
    if embedder_model:
        from .retrieve import _unit
        vec = {v["owner_id"]: _unit(v["vector"]) for v in store.vectors("state", embedder_model)}
    firm: dict[int, list[int]] = {}
    for s in store.searchable_states():
        if s.status == "active" and s.trust in ("owner", "verified") and not s.state_key:
            firm.setdefault(s.thing_id, []).append(s.id)
    out = []
    for s in props:
        if s.id in bad or weights.get(s.id, 1.0) < 1.0:
            continue
        sources = {(e.ref_type, e.ref) for e in ev.get(s.id, [])}
        if len(sources) >= 2:
            out.append((s.id, f"corroborated: {len(sources)} distinct sources"))
        elif s.id in same:
            out.append((s.id, "corroborated: learned twice (a duplicate was merged into it)"))
        elif used.get(s.id, 0) >= USED_MIN:
            out.append((s.id, f"corroborated: used in {used[s.id]} turns, never contradicted"))
        elif s.id in vec:
            from .retrieve import _dot
            match = next((o for o in firm.get(s.thing_id, []) if o in vec and _dot(vec[s.id], vec[o]) >= SAME_TEXT), None)
            if match:
                out.append((s.id, f"corroborated: an owner/verified state says the same (#{match})"))
    return out


def promote(store: Store, embedder_model: Optional[str] = None, *, apply: bool = True, limit: int = 500) -> list[tuple[int, str]]:
    found = candidates(store, embedder_model)[:limit]
    if apply:
        for sid, why in found:
            store.accept(sid, why, actor=ACTOR)
    return found
