"""Automated detection of misses: signals that the store failed the user, found without anyone judging anything (feeds the outcome log as `missed`).

- restated: the user says the same thing again within a window. They had to repeat something the system should have carried.
- restated-unstored: a restated input matches no stored state: an extraction miss (the fact was said and never kept).
- restated-stored-not-surfaced: a restated input matches a stored state that was not surfaced in that turn: a surfacing miss.
- changed-not-surfaced: a state changed after its thing last surfaced and then was only omitted (dropped by cap or budget) when the thing came up again.

All offline over `turn_texts` and the outcome log; idempotent (a miss is logged once per source and turn).
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta

from .embed import cosine
from .store import Store

MIN_CHARS = 40


def _t(s: str) -> datetime:
    return datetime.fromisoformat(s)


def find_restatements(prompts: list[tuple[str, str, str]], embedder, threshold: float = 0.93, *, hours: float = 24) -> list[tuple[str, str, float]]:
    """(earlier_ref, later_ref, cosine) for prompts [(ref, created, text)] whose meaning repeats an earlier prompt within `hours`."""
    usable = [p for p in prompts if len(p[2]) >= MIN_CHARS and not p[2].lstrip().startswith("<")]      # automated prompts (<scheduled-task ...>) are not restatements
    if len(usable) < 2:
        return []
    vectors = embedder.embed([p[2][:600] for p in usable])
    out = []
    for j in range(1, len(usable)):
        best = None
        for i in range(j):
            if _t(usable[j][1]) - _t(usable[i][1]) > timedelta(hours=hours):
                continue
            sim = cosine(vectors[i], vectors[j])
            if sim >= threshold and (best is None or sim > best[1]):
                best = (i, sim)
        if best:
            out.append((usable[best[0]][0], usable[j][0], round(best[1], 3)))
    return out


def find_changed_not_surfaced(store: Store) -> list[tuple[int, str]]:
    """(state_id, turn_ref) for omitted outcomes whose state changed after its thing last surfaced (or that never surfaced)."""
    outs = store.outcomes()
    last_surfaced: dict[int, str] = {}
    for o in outs:
        if o.kind == "surfaced" and o.thing_id is not None:
            last_surfaced[o.thing_id] = o.recorded_utc
    found = []
    for o in outs:
        if o.kind != "omitted" or o.state_id is None:
            continue
        st = store.get_state(o.state_id)
        if st and st.thing_id in last_surfaced and st.recorded_utc >= last_surfaced[st.thing_id]:      # it surfaced before and changed since; >= : same-ms ties count as not yet seen
            found.append((o.state_id, o.turn_ref))
    return found


def _logged(store: Store, source: str) -> set:
    return {(o.turn_ref, o.state_id) for o in store.outcomes(kind="missed") if o.source == source}


def log_misses(store: Store, embedder, *, threshold: float = 0.93, state_floor: float = 0.78, hours: float = 24) -> dict[str, int]:
    """Runs every detector over the stored turns and logs new `missed` outcomes. Returns how many of each source were logged this call."""
    counts = {"restated": 0, "restated-unstored": 0, "restated-stored-not-surfaced": 0, "changed-not-surfaced": 0}
    prompts = [(ref, created, text) for ref, _sess, created, text in store.turn_texts("prompt")]
    pairs = find_restatements(prompts, embedder, threshold, hours=hours)
    text_of = {p[0]: p[2] for p in prompts}
    state_vectors = {r["owner_id"]: r["vector"] for r in store.vectors("state", embedder.model)} if hasattr(embedder, "model") else {}
    done = {src: _logged(store, src) for src in counts}
    for earlier, later, sim in pairs:
        if (later, None) not in done["restated"]:
            store.log_outcome(None, "missed", source="restated", turn_ref=later, detail=json.dumps({"repeats": earlier, "cosine": sim}))
            counts["restated"] += 1
        best = None
        if state_vectors:
            qv = embedder.embed([text_of[later][:600]])[0]
            for sid, vec in state_vectors.items():
                score = cosine(qv, vec)
                if score >= state_floor and (best is None or score > best[1]):
                    best = (sid, score)
        if not best:
            if (later, None) not in done["restated-unstored"]:
                store.log_outcome(None, "missed", source="restated-unstored", turn_ref=later, detail=json.dumps({"repeats": earlier}))
                counts["restated-unstored"] += 1
        else:
            shown = {o.state_id for o in store.outcomes(kind="surfaced", turn_ref=later)}
            if best[0] not in shown and (later, best[0]) not in done["restated-stored-not-surfaced"]:
                store.log_outcome(best[0], "missed", source="restated-stored-not-surfaced", turn_ref=later, detail=json.dumps({"repeats": earlier, "cosine": round(best[1], 3)}))
                counts["restated-stored-not-surfaced"] += 1
    for sid, turn in find_changed_not_surfaced(store):
        if (turn, sid) not in done["changed-not-surfaced"]:
            store.log_outcome(sid, "missed", source="changed-not-surfaced", turn_ref=turn)
            counts["changed-not-surfaced"] += 1
    return counts
