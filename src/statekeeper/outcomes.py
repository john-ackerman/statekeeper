"""Outcome labelling from signals outside the store.

`surface(..., log_turn=ref)` records what was shown (surfaced) and what a cap or budget dropped (omitted). Once the agent's own output for that turn is
known, `label_turn` compares it with those records: an output that carries a surfaced state's thing and a distinctive word of its text means the state
was used; the same for an omitted state means it was needed and missed. These proxies read the agent's behaviour, not the store's opinion of itself.
Stronger truth arrives through deterministic writers (verified_right / verified_wrong, see writers/) and through verify handles.
A handoff is just another artifact: score it with outcomes whose source is "handoff:<ref>" and compare against the store's.
"""
from __future__ import annotations

import re

from .store import State, Store

STOP = {"that", "this", "with", "from", "have", "been", "were", "will", "than", "then", "into", "when", "what", "which", "their", "there", "about", "after", "before"}


def _stems(text: str) -> set[str]:
    return {w[:5] for w in re.findall(r"[a-z0-9]{4,}", text.lower()) if w not in STOP}


_PR_NAME = re.compile(r"\bPR (\d+)$")


def used_proxy(output: str, state: State) -> bool:
    """The output (the answer and its tool calls) cites the state by `#id`, or names the state's thing (for a pull request, its number written as
    #N, "PR N" or pull/N also counts) and carries at least one distinctive word of the state's text beyond the thing's own name."""
    if re.search(rf"(?<![\w#])#{state.id}\b", output):
        return True
    low = output.lower()
    named = state.thing_name.lower() in low
    if not named and (m := _PR_NAME.search(state.thing_name)):
        named = bool(re.search(rf"(#|\bPR\s?|/pull/){m.group(1)}\b", output, re.I))
    if not named:
        return False
    distinctive = _stems(state.text) - _stems(state.thing_name)
    return bool(distinctive & _stems(output))


def label_turn(store: Store, turn_ref: str, output: str, *, source: str = "output-overlap") -> dict[str, int]:
    """Logs used (for surfaced states) and missed (for omitted ones) for a turn, from the agent's output. Returns the counts."""
    counts = {"used": 0, "missed": 0}
    already = {(o.kind, o.state_id) for o in store.outcomes(turn_ref=turn_ref)}
    for o in store.outcomes(turn_ref=turn_ref):
        if o.kind not in ("surfaced", "omitted") or o.state_id is None:
            continue
        target = "used" if o.kind == "surfaced" else "missed"
        st = store.get_state(o.state_id)
        if st and (target, o.state_id) not in already and used_proxy(output, st):
            store.log_outcome(o.state_id, target, source=source, turn_ref=turn_ref)
            already.add((target, o.state_id))
            counts[target] += 1
    return counts
