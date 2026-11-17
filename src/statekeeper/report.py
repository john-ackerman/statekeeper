"""The outcome report: counts from the outcome log, what the suggestion rules would say (never applied), consolidation validation. Measured only."""
from __future__ import annotations

import json
from collections import Counter

from .consolidate import validate
from .learn import MIN_INTERVALS, MIN_LABELLED, MIN_VERIFIED, suggest, summarize
from .store import Store


def build_report(store: Store) -> dict:
    # Outcomes are append-only; a detector bug is corrected by a logged parameter that says "ignore this source up to outcome id N".
    ignore = int(store.get_param("outcomes.ignore_changed_not_surfaced_up_to_id", 0))
    outs = [o for o in store.outcomes() if not (o.source == "changed-not-surfaced" and o.id <= ignore)]
    surfaced = [o for o in outs if o.kind == "surfaced"]
    details = [json.loads(o.detail) for o in surfaced if o.detail]
    per_state = Counter(o.state_id for o in surfaced)
    summary = summarize(store)
    misses = Counter(o.source for o in outs if o.kind == "missed")
    return {
        "outcomes_by_kind": dict(Counter(o.kind for o in outs)), "turns_with_a_surfaced_state": len({o.turn_ref for o in surfaced}),
        "live_turns_recorded": len(store.turn_texts("prompt")),
        "surfaced_by_match_reason": dict(Counter(d["why"].split(";")[0].split(" (")[0].split()[0] for d in details)),
        "surfaced_by_trust": dict(Counter(d["trust"] for d in details)),
        "states_surfaced_in_more_than_one_turn": sum(1 for n in per_state.values() if n > 1),
        "misses_by_source": dict(misses), "verified_outcomes_by_writer": summary["writers"], "labelled_surfaced": summary["floor"],
        "consolidation": validate(store),
        "needed_before_a_suggestion": {"verified_per_writer": MIN_VERIFIED, "change_intervals_per_volatility": MIN_INTERVALS, "labelled_surfaced": MIN_LABELLED},
        "suggestions": suggest(summary),
    }
