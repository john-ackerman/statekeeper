"""Per-turn parity between what Legacy actually injected and what statekeeper WOULD have (shadow mode; the latter is never emitted).

Reads `shadow_injections`. For prompt turns it compares the things each side surfaced (names parsed from each side's block format) and the size of each
side's injection in estimated tokens (chars / 4). For session starts it compares sizes. Measured only: it says what differs, not which is better.
"""
from __future__ import annotations

import re
from collections import Counter

from .store import Store

LEGACY_NAME = re.compile(r"^(?P<n>[^\n(#][^\n(]*?) \((?:[a-z ]+)[;)]", re.M)
STATEKEEPER_NAME = re.compile(r"^(?P<n>[^\n(\[ ][^\n(]*?) \((?:thing|person|agent|value); ", re.M)


def names_legacy(text: str) -> set[str]:
    return {m.group("n").strip().lower() for m in LEGACY_NAME.finditer(text) if not m.group("n").startswith(("Concept map", "Current state"))}


def names_statekeeper(text: str) -> set[str]:
    return {m.group("n").strip().lower() for m in STATEKEEPER_NAME.finditer(text)}


def tokens(text: str) -> int:
    return len(text) // 4


def build_parity(store: Store) -> dict:
    by_turn: dict[str, dict] = {}
    for turn, source, event, kind, text, _ in store.injections():
        t = by_turn.setdefault(turn, {"legacy": {}, "statekeeper": {}, "events": set()})
        t[source][kind] = t[source].get(kind, "") + ("\n" if kind in t[source] else "") + text
        t["events"].add(event)
    rows, sessions = [], []
    only_p, only_s = Counter(), Counter()
    tracked = []
    for turn, t in sorted(by_turn.items()):
        if turn.startswith("sess:"):
            sessions.append({"session_turn": turn, "legacy_tokens": tokens("".join(t["legacy"].values())), "statekeeper_tokens": tokens("".join(t["statekeeper"].values())),
                             "legacy_kinds": sorted(t["legacy"]), "statekeeper_kinds": sorted(t["statekeeper"])})
            continue
        p_map, s_map = t["legacy"].get("concept_map", ""), t["statekeeper"].get("concept_map", "")
        pn, sn = names_legacy(p_map), names_statekeeper(s_map)
        only_p.update(pn - sn)
        only_s.update(sn - pn)
        tracked.append(tokens(t["legacy"].get("tracked", "")))
        rows.append({"turn": turn, "both": sorted(pn & sn), "only_legacy": sorted(pn - sn), "only_statekeeper": sorted(sn - pn),
                     "legacy_tokens": tokens("".join(t["legacy"].values())), "statekeeper_tokens": tokens("".join(t["statekeeper"].values()))})
    n = len(rows)
    union = [len(set(r["both"]) | set(r["only_legacy"]) | set(r["only_statekeeper"])) for r in rows]
    jac = [len(r["both"]) / u for r, u in zip(rows, union) if u]
    return {
        "prompt_turns": n, "both_empty_turns": sum(1 for r, u in zip(rows, union) if u == 0),
        "turns_with_overlap": sum(1 for r in rows if r["both"]),
        "mean_jaccard_when_either_surfaced": round(sum(jac) / len(jac), 3) if jac else None,
        "legacy_tokens_mean": round(sum(r["legacy_tokens"] for r in rows) / n, 1) if n else None,
        "statekeeper_tokens_mean": round(sum(r["statekeeper_tokens"] for r in rows) / n, 1) if n else None,
        "tracked_tokens_legacy_mean": round(sum(tracked) / len(tracked), 1) if tracked else 0,
        "top_only_legacy": only_p.most_common(8), "top_only_statekeeper": only_s.most_common(8),
        "session_start": sessions, "turns": rows,
    }
