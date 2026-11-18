"""What the ongoing conversation is about, and what recall turned out to be worth.

Conversation topic. Each person-typed turn, the things it names whole (+1.0) and the things recall showed for it (+0.5) gain topic score; every score
decays with time (half-life TOPIC_HALF_LIFE seconds) and with each further turn (x TOPIC_PER_TURN), and a turn that names things none of which are on
topic halves the rest (the conversation moved on). `select(topic=...)` uses the decayed scores: an on-topic thing's states need a little less meaning.

Usefulness signals, all logged as outcomes (append-only, with their source) and never acted on silently:
  used          the next answer or its tool calls carried the state (outcomes.used_proxy: its thing, a PR number, or its `#id`, plus a word of it);
  contradicted  the person's next message corrects it ("no, that's outdated ...") and points at it (its thing or two of its distinctive words),
                or explicit feedback "sk #123 wrong|stale";
  unhelpful     explicit feedback "sk #123 noise|irrelevant";
  used (feedback) explicit "sk #123 useful".
`weigh` (nightly) turns those counts into per-state weights in param_history (reasoned, revertible, 0.5..1.1, moving a meaning score by at most
-0.05..+0.01) and flags contradicted states for a person to close; it never closes or deletes anything.
"""
from __future__ import annotations

import json
import re
import time
from datetime import datetime, timedelta, timezone

from .outcomes import _stems
from .store import WEIGHT_PREFIX, Store

TOPIC_HALF_LIFE = 20 * 60
TOPIC_PER_TURN = 0.7
TOPIC_NAMED = 1.0
TOPIC_SURFACED = 0.5
TOPIC_MIN = 0.05

_FEEDBACK = re.compile(r"\b(?:statekeeper|sk)\s*[:,]?\s*#(\d+)\s+(?:is\s+|was\s+)?(useful|used|right|helpful|wrong|stale|outdated|incorrect|noise|noisy|irrelevant|unhelpful)\b", re.I)
_VERDICT = {"useful": "used", "used": "used", "right": "used", "helpful": "used", "wrong": "contradicted", "stale": "contradicted", "outdated": "contradicted",
            "incorrect": "contradicted", "noise": "unhelpful", "noisy": "unhelpful", "irrelevant": "unhelpful", "unhelpful": "unhelpful"}
_CORRECTION = re.compile(r"(^|\b)(no,|nope|that'?s (?:wrong|not right|not true|outdated|old)|that is (?:wrong|not right|not true|outdated)|not true|"
                         r"incorrect|out of date|outdated|stale|no longer|not anymore|isn'?t (?:right|true|the case)|wasn'?t (?:right|true)|already (?:merged|done|deployed|fixed))", re.I)


def topic_scores(topic: dict, now: float, turn: int) -> dict[int, float]:
    """Decayed topic scores {thing_id: score} from the stored {"<id>": [score, at, turn]}."""
    out = {}
    for tid, (score, at, t) in topic.items():
        v = score * 0.5 ** (max(0.0, now - at) / TOPIC_HALF_LIFE) * TOPIC_PER_TURN ** max(0, turn - t)
        if v >= TOPIC_MIN:
            out[int(tid)] = v
    return out


def topic_update(topic: dict, now: float, turn: int, named: list[int], surfaced: list[int], on_topic: set[int]) -> dict:
    """The stored topic after a person-typed turn: decay, a topic change halves what was not mentioned, then add what this turn named and showed."""
    cur = topic_scores(topic, now, turn)
    if named and not (set(named) & on_topic):
        cur = {k: v * 0.5 for k, v in cur.items()}
    for tid in surfaced:
        cur[tid] = cur.get(tid, 0.0) + TOPIC_SURFACED
    for tid in named:
        cur[tid] = cur.get(tid, 0.0) + TOPIC_NAMED
    return {str(k): [round(v, 4), now, turn] for k, v in sorted(cur.items(), key=lambda kv: -kv[1])[:30] if v >= TOPIC_MIN}


def parse_feedback(text: str) -> list[tuple[int, str]]:
    """Explicit feedback in a message: "sk #123 useful", "statekeeper: #45 stale", "sk #9 noise". -> [(state_id, outcome kind)]."""
    return [(int(m.group(1)), _VERDICT[m.group(2).lower()]) for m in _FEEDBACK.finditer(text)]


def log_feedback(store: Store, items: list[tuple[int, str]], *, source: str, turn_ref: str | None = None, note: str = "") -> int:
    n = 0
    for sid, kind in items:
        if store.get_state(sid) is None:
            continue
        store.log_outcome(sid, kind, source=source, turn_ref=turn_ref, detail=json.dumps({"feedback": kind, "note": note[:200]}))
        n += 1
    return n


def corrected(store: Store, text: str, shown_ids: list[int]) -> list[int]:
    """States shown on the previous turn that this message corrects: it carries a correction cue AND points at the state (its thing's name, or two of
    its distinctive words). Deliberately narrow: a missed correction costs little, a false one would down-weight a good state."""
    if not shown_ids or not _CORRECTION.search(text):
        return []
    low, stems = text.lower(), _stems(text)
    out = []
    for sid in shown_ids:
        st = store.get_state(sid)
        if st is None:
            continue
        distinctive = _stems(st.text) - _stems(st.thing_name)
        if st.thing_name.lower() in low or len(distinctive & stems) >= 2:
            out.append(sid)
    return out


def _since(days: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")


def counts(store: Store, days: float) -> dict[int, dict[str, int]]:
    """Per state over the window: distinct turns it was surfaced in, used, contradicted (explicit / by correction), unhelpful."""
    since = _since(days)
    per: dict[int, dict] = {}
    for o in store.outcomes():
        if o.state_id is None or (o.recorded_utc or "")[:19] < since:
            continue
        c = per.setdefault(o.state_id, {"surfaced": set(), "used": 0, "contradicted": 0, "contradicted_explicit": 0, "unhelpful": 0})
        if o.kind == "surfaced":
            c["surfaced"].add(o.turn_ref)
        elif o.kind == "used":
            c["used"] += 1
        elif o.kind == "contradicted":
            c["contradicted"] += 1
            c["contradicted_explicit"] += 1 if o.source.startswith("feedback") else 0
        elif o.kind == "unhelpful":
            c["unhelpful"] += 1
    return {sid: {**c, "surfaced": len(c["surfaced"])} for sid, c in per.items()}


def target_weight(c: dict) -> tuple[float, str] | None:
    """The weight the counts justify, with why; None means leave it at 1.0. Conservative: needs repeated evidence, never below 0.5."""
    if c["contradicted_explicit"] >= 1 or c["contradicted"] >= 2:
        return 0.5, f"contradicted {c['contradicted']}x ({c['contradicted_explicit']} explicit)"
    if c["unhelpful"] >= 1:
        return 0.7, f"said to be noise {c['unhelpful']}x"
    if c["surfaced"] >= 12 and c["used"] == 0:
        return 0.7, f"surfaced in {c['surfaced']} turns, never used"
    if c["surfaced"] >= 6 and c["used"] == 0:
        return 0.8, f"surfaced in {c['surfaced']} turns, never used"
    if c["used"] >= 3 and c["used"] >= 0.3 * max(1, c["surfaced"]):
        return 1.1, f"used {c['used']}x of {c['surfaced']} turns"
    return None


def weigh(store: Store, *, days: float = 21, actor: str = "nightly:usefulness", dry_run: bool = False) -> dict:
    """Sets per-state weights from the window's outcomes (only where they change; each with its reason; `Store.revert_param` undoes one) and flags
    contradicted states for review. A weight already set by a person (actor not starting with "nightly") is left alone."""
    current = store.state_weights()
    owners = {}
    if hasattr(store, "db"):
        owners = {int(n[len(WEIGHT_PREFIX):]): a for n, a in store.db.execute(
            "SELECT name, actor FROM param_history WHERE id IN (SELECT MAX(id) FROM param_history WHERE name LIKE 'weight.state.%' GROUP BY name)")}
    flagged = {sid for sid, _r, _a in store.open_flags()} if hasattr(store, "open_flags") else set()
    changed, flags = [], []
    for sid, c in counts(store, days).items():
        st = store.get_state(sid)
        if st is None or not st.standing or st.state_key:
            continue
        want = target_weight(c)
        value, why = want if want else (1.0, "no evidence either way in the window")
        if sid in owners and not str(owners[sid]).startswith("nightly"):
            continue
        if abs(current.get(sid, 1.0) - value) > 1e-9:
            changed.append({"state": sid, "from": current.get(sid, 1.0), "to": value, "why": why})
            if not dry_run:
                store.set_param(f"{WEIGHT_PREFIX}{sid}", value, f"{why} in the last {days:g} days", actor=actor)
        if c["contradicted"] and sid not in flagged:
            flags.append(sid)
            if not dry_run:
                store.flag_state(sid, f"contradicted {c['contradicted']}x in the last {days:g} days: close it if it is no longer true", actor=actor)
    return {"changed": changed, "flagged": flags}


def summary(store: Store, days: float = 1) -> dict:
    """For the daily health line: how much of what was shown got used, corrections, explicit feedback, the states shown most without use."""
    c = counts(store, days)
    shown = sum(x["surfaced"] for x in c.values())
    used = sum(min(x["used"], x["surfaced"]) for x in c.values())
    unused = sorted(((x["surfaced"], sid) for sid, x in c.items() if x["surfaced"] and not x["used"]), reverse=True)[:3]
    return {"shown": shown, "used": used, "used_pct": round(100 * used / shown) if shown else 0,
            "contradicted": sum(x["contradicted"] for x in c.values()), "explicit_feedback": sum(x["contradicted_explicit"] + x["unhelpful"] for x in c.values()),
            "shown_most_unused": [f"#{sid} x{n}" for n, sid in unused]}


_UNLINK = re.compile(r"\b(?:statekeeper|sk)\s*[:,]?\s*#(\d+)\s+(?:is\s+)?not\s+(?:about\s+|related\s+to\s+|for\s+)?[\"']?([^\n.;,!?\"']{2,60})", re.I)


def parse_unlinks(text: str) -> list[tuple[int, str]]:
    """'sk #395 not TheRepo', 'statekeeper: #12 is not about the datepicker' -> [(state_id, target)]. The target is a thing's name, or else a
    concept (a few words). "sk #12 not useful" is feedback, not an unlink."""
    out = []
    for m in _UNLINK.finditer(text):
        target = " ".join(m.group(2).split())
        if target.lower() in _VERDICT or target.lower().startswith(("useful", "helpful", "relevant", "right")):
            continue
        out.append((int(m.group(1)), target))
    return out


def apply_unlinks(store: Store, items: list[tuple[int, str]], *, actor: str, reason: str) -> list[int]:
    """Records each as a negative link: from the named thing when the target is one (trying without a leading "the"), else from the concept."""
    made = []
    for sid, target in items:
        if store.get_state(sid) is None:
            continue
        name = next((n for n in (target, re.sub(r"^(the|a|an)\s+", "", target, flags=re.I)) if store.find(n)), None)
        try:
            made.append(store.unlink(sid, thing=name, reason=reason, actor=actor) if name else store.unlink(sid, concept=target, reason=reason, actor=actor))
        except Exception:
            continue
    return made


def weak_connections(store: Store, days: float = 7, floor: float = 0.78, min_turns: int = 3) -> list[dict]:
    """The relatedness audit: states shown in at least `min_turns` turns where the recorded connection was weak (a generic name, rare words, topic
    carry-over, or meaning within 0.03 of the floor), with how often they were used. Candidates for a negative link; nothing is applied."""
    since = _since(days)
    per: dict[int, dict] = {}
    for o in store.outcomes():
        if o.state_id is None or (o.recorded_utc or "")[:19] < since or o.kind not in ("surfaced", "used"):
            continue
        c = per.setdefault(o.state_id, {"turns": set(), "weak": 0, "used": 0, "how": {}})
        if o.kind == "used":
            c["used"] += 1
            continue
        c["turns"].add(o.turn_ref)
        try:
            r = (json.loads(o.detail or "{}") or {}).get("reason") or {}
        except ValueError:
            r = {}
        weak = r.get("generic") or r.get("via") in ("words", "topic") or (r.get("via") == "meaning" and (r.get("cos") or 1) < floor + 0.03)
        if weak:
            c["weak"] += 1
            key = r.get("thing") or r.get("via")
            c["how"][key] = c["how"].get(key, 0) + 1
    rows = [{"state": sid, "turns": len(c["turns"]), "weak": c["weak"], "used": c["used"], "via": max(c["how"], key=c["how"].get) if c["how"] else None}
            for sid, c in per.items() if len(c["turns"]) >= min_turns and c["weak"] >= min_turns and c["used"] == 0]
    return sorted(rows, key=lambda r: -r["weak"])[:10]
