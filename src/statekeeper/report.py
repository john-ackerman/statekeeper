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
        "surfaced_by_match_reason": dict(Counter(str(d.get("why") or "?").split(";")[0].split(" (")[0].split()[0] for d in details)),
        "surfaced_by_trust": dict(Counter(d.get("trust") or "?" for d in details)),
        "states_surfaced_in_more_than_one_turn": sum(1 for n in per_state.values() if n > 1),
        "misses_by_source": dict(misses), "verified_outcomes_by_writer": summary["writers"], "labelled_surfaced": summary["floor"],
        "consolidation": validate(store),
        "needed_before_a_suggestion": {"verified_per_writer": MIN_VERIFIED, "change_intervals_per_volatility": MIN_INTERVALS, "labelled_surfaced": MIN_LABELLED},
        "suggestions": suggest(summary),
    }


def injection_health(log_lines, since_iso: str) -> dict:
    """What the live hooks did since `since_iso` (UTC, "YYYY-MM-DDTHH:MM:SSZ"), from the adapter log (one JSON object per line): prompts seen, how
    many got an injection, its mean size, the slowest call, session starts and failed refreshes, and every adapter error. Measured only."""
    prompts, injected, chars, slowest, starts, refresh_failed, errors = 0, 0, [], 0, 0, 0, []
    for line in log_lines:
        try:
            e = json.loads(line)
        except ValueError:
            continue
        if str(e.get("at", "")) < since_iso:
            continue
        kind = e.get("event")
        if kind == "UserPromptSubmit" and e.get("live"):
            prompts += 1
            slowest = max(slowest, int(e.get("ms") or 0))
            if e.get("block_chars"):
                injected += 1
                chars.append(int(e["block_chars"]))
        elif kind == "inject-session-start":
            starts += 1
            slowest = max(slowest, int(e.get("ms") or 0))
            if e.get("refresh") not in ("none", "exit 0"):
                refresh_failed += 1
        elif kind in ("inject-error", "adapter-error"):
            errors.append(f"{kind}: {e.get('error', '')}"[:200])
    return {"since": since_iso, "live_prompts": prompts, "injected": injected, "quiet": prompts - injected,
            "mean_injected_chars": round(sum(chars) / len(chars)) if chars else 0, "slowest_ms": slowest, "session_starts": starts,
            "refresh_failed": refresh_failed, "errors": errors[-10:], "error_count": len(errors)}


def health_text(h: dict) -> str:
    """The same, as a few lines a person reads in ten seconds."""
    flag = "CHECK" if h["error_count"] or h["refresh_failed"] or h["slowest_ms"] > 5000 or (h["live_prompts"] and not h["injected"]) else "ok"
    lines = [f"statekeeper live injection since {h['since']}: {flag}",
             f"prompts {h['live_prompts']}: injected {h['injected']}, quiet {h['quiet']}, mean {h['mean_injected_chars']} chars; slowest call {h['slowest_ms']} ms",
             f"session starts {h['session_starts']}, refresh failures {h['refresh_failed']}; adapter errors {h['error_count']}"]
    if "usefulness" in h:
        us = h["usefulness"]
        lines.append(f"shown {us['shown']} state-turns, used {us['used']} ({us['used_pct']}%); corrections {us['contradicted']}, explicit feedback {us['explicit_feedback']}; "
                     f"open flags {h.get('open_flags', 0)}" + (f"; shown most, never used: {', '.join(us['shown_most_unused'])}" if us["shown_most_unused"] else ""))
    if h.get("weak_connections"):
        lines.append("relatedness audit (shown 3+ turns through a weak connection, never used; candidates for `curate.py unlink`): "
                     + ", ".join(f"#{w['state']} x{w['weak']} via {w['via']}" for w in h["weak_connections"][:5]))
    lines += [f"  {e}" for e in h["errors"]]
    return "\n".join(lines) + "\n"
