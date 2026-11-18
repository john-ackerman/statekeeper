"""State-first retrieval: from an input to the current states it bears on.

Every state shown must itself bear on the input (see `select`). Ways in:
  named    the input spells a thing's name or alias whole (enough on its own only for a specific thing, or a thing's keyed current values);
  spelled  a near spelling (with a guard: a truncation or prefix of a name is another word, not a typo);
  meaning  the input is close in meaning to a state's text (vectors over states, proposed ones included);
  words    the input shares rare words with a state that is nearly close enough in meaning.
Nothing is better than noise: below the floor, nothing surfaces. Of two states about the same subject only the newer is shown. Raw evidence is never
injected; each state carries one trust marker and one short evidence pointer.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from .embed import Embedder, cosine
from .store import Evidence, State, Store, Thing, likely_replaces

DEFAULT_FLOOR = 0.72
LIVE_STALE_HOURS = 6
QUERY_CHARS = 1200
_WORDS = re.compile(r"[^\W_]+", re.UNICODE)


def words(text: str) -> list[str]:
    return [w.lower() for w in _WORDS.findall(text)]


def occurrences(seq: list[str], label: str) -> int:
    target = words(label)
    if not target:
        return 0
    n = len(target)
    return sum(1 for i in range(len(seq) - n + 1) if seq[i:i + n] == target)


def _distance(a: str, b: str, limit: int) -> int:
    prev = list(range(len(b) + 1))
    for i in range(1, len(a) + 1):
        cur = [i] + [0] * len(b)
        for j in range(1, len(b) + 1):
            cur[j] = min(cur[j - 1] + 1, prev[j] + 1, prev[j - 1] + (a[i - 1] != b[j - 1]))
        if min(cur) > limit:
            return limit + 1
        prev = cur
    return prev[-1]


def fuzzy_match(seq: list[str], label: str) -> Optional[tuple[float, str]]:
    """A near spelling of `label` in `seq`: a run of one to three words written together within an edit or two of the label written together
    ("date picker" for "datepicker"), but not the label's own words in order and never a truncation or prefix ("claud" is not Claude, "example" is
    not ExampleApp). Labels under five letters are never matched fuzzily."""
    lw = words(label)
    target = "".join(lw[1:] if len(lw) > 1 and lw[0] in ("the", "a", "an") else lw)
    if len(target) < 5 or occurrences(seq, label) > 0 or any(ch.isdigit() for ch in target):     # a number is an identifier: 3345 is not a typo of 3351
        return None
    allowed = 2 if len(target) >= 9 else 1
    best: Optional[tuple[float, str]] = None
    for size in (1, 2, 3):
        for i in range(len(seq) - size + 1):
            run = seq[i:i + size]
            if run == lw:
                continue
            joined = "".join(run)
            if abs(len(joined) - len(target)) > allowed:
                continue
            if len(joined) != len(target) and (target.startswith(joined) or joined.startswith(target)):
                continue
            d = _distance(joined, target, allowed)
            if d <= allowed:
                score = 1.0 - d / len(target)
                if best is None or score > best[0]:
                    best = (score, " ".join(run))
    return best


@dataclass
class Item:
    thing: Thing
    why: str                       # named | spelled "x" | meaning 0.78
    score: float
    states: list[State]
    pinned: Optional[int] = None   # the first state that qualified for this thing
    evidence: dict = field(default_factory=dict)


@dataclass
class Surfaced:
    block: str
    items: list[Item]
    named: list[int] = field(default_factory=list)   # things the input named whole (feeds the conversation-topic signal)


def _freshness(s: State, today: datetime) -> str:
    """ONE short marker for how far to trust a state right now."""
    def days(ts: Optional[str]) -> Optional[int]:
        if not ts:
            return None
        try:
            return max(0, (today - datetime.fromisoformat(ts[:19]).replace(tzinfo=timezone.utc)).days)
        except ValueError:
            return None
    verified = days(s.confirmed_utc)
    if s.volatility == "live" and s.trust != "owner":
        # a live state (a build, a PR's CI, an agent's status) goes stale in hours: say so, and how to re-check, rather than state it as current
        try:
            age_h = (today - datetime.fromisoformat((s.confirmed_utc or s.recorded_utc)[:19]).replace(tzinfo=timezone.utc)).total_seconds() / 3600
        except ValueError:
            age_h = 1e9
        if age_h > LIVE_STALE_HOURS:
            how = f"; re-check: {s.verify_handle[:60]}" if s.verify_handle else ""
            return f"STALE, last verified {int(age_h)}h ago{how}" if s.confirmed_utc else f"STALE, never re-verified{how}"
        return f"verified {int(age_h * 60)}m ago" if age_h < 1 else f"verified {int(age_h)}h ago"
    if s.trust == "owner":
        return "owner-stated"
    if verified is not None:
        return "verified today" if verified == 0 else f"verified {verified}d ago"
    return {"claimed": "agent-claimed", "extracted": "unreviewed", "derived": "derived"}.get(s.trust, "unverified")


def _first_line(text: str, width: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= width else text[:width - 1] + "…"


_CAMEL = re.compile(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")
STOP = frozenset("""a about above after again all also am an and any are as at be because been before being below between both but by can could did do does
doing done down during each else even ever few for from further get gets got had has have having he her here hers him his how i if in into is it its
itself just let like make made may maybe me might more most much must my no nor not now of off on once one only or other our out over own really same
see seem she should so some still such sure than that the their them then there these they thing things think this those though through to too
under until up us very want wanted was way we well were what when where whether which while who whom why will with would yeah yes yet you your
okay ok actually something anything going gonna sweet cool thanks thank please lol oh hey""".split())


def split_identifiers(text: str) -> str:
    """`MaxSlotWaitSeconds` also read as `Max Slot Wait Seconds`, so a config key meets the words people say for it (in meaning and word matching)."""
    return _CAMEL.sub(" ", text)


def terms(text: str) -> set[str]:
    """Content words: whole words and the parts of split identifiers, at least 5 letters or carrying a digit ("2085", "e2e"), never a stop word.
    Short words ("look", "hub" out of "GitHub") are too common in talk to say two texts share a subject."""
    return {w for w in set(words(text)) | set(words(split_identifiers(text))) if (len(w) >= 5 or any(c.isdigit() for c in w)) and w not in STOP}


SAME_SUBJECT = 0.80      # two qualifying states this close in meaning are about the same subject: only the newer one is shown
NAMED_SLACK = 0.06       # a generic named thing's state may sit this far under the floor if it also shares a rare word with the input
WORD_SLACK = 0.08        # any state may sit this far under the floor if it shares two rare words with the input
WORD_STEP = 0.04         # ... and this much further if it shares three or more
GENERIC_MENTIONS = 15    # ... and a thing whose name appears in more than this many states' text (or 2% of them) is generic whatever its own count
SPECIFIC_MAX = 6         # a thing with at most this many standing states is specific: naming it is enough to show its current states
CLOSE_SPELLING = 0.9     # a near spelling this close ("date picker", "stake keeper") counts as naming the thing; a looser one ("service just") does not
MAX_STATES = 5
TOPIC_ON = 0.6           # a thing whose conversation-topic score is at least this is what we are talking about now (see select, topic=)
TOPIC_SLACK = 0.08       # an on-topic thing's state may sit this far under the floor, with no rare word needed
TOPIC_NEAR = 0.04        # an on-topic thing not named in this input: its states may sit this far under the floor
WEIGHT_SPAN = 0.10       # a usefulness weight w moves a state's meaning score by (w - 1) * WEIGHT_SPAN (w is kept in 0.5..1.1, so -0.05..+0.01)


_PR_URL = re.compile(r"https?://github\.com/[^/\s]+/([^/\s]+)/pull/(\d+)\S*")


def _named(store: Store, text: str, exclude_kinds: tuple, exclude_names: tuple) -> dict[int, tuple[Thing, str, float]]:
    text = _PR_URL.sub(lambda m: f"{m.group(0)} {m.group(1)} PR {m.group(2)}", text)      # a pasted PR link names "<repo> PR <n>"
    seq, split = words(text), words(split_identifiers(text))
    found: dict[int, tuple[Thing, str, float]] = {}
    for tid, label in store.labels():
        thing = store.get(tid)
        if thing is None or thing.retired or thing.kind in exclude_kinds or thing.name.lower() in exclude_names:
            continue
        if occurrences(seq, label) > 0 or (split_identifiers(label) != label and occurrences(split, split_identifiers(label)) > 0):
            found[tid] = (thing, "named", 1.0)
        elif tid not in found and (m := fuzzy_match(seq, label)):
            found[tid] = (thing, f'spelled "{m[1]}"', m[0])
    return found


def candidates(store: Store, text: str, embedder: Optional[Embedder], floor: float, limit: int) -> list[Item]:
    """The things `select` would show for an input, best first (for callers that want things, not states)."""
    return select(store, text, embedder, floor=floor, max_things=limit)[0]


def select(store: Store, text: str, embedder: Optional[Embedder], *, floor: float, max_things: int = 3, states_each: int = 2,
           exclude_kinds: tuple = (), exclude_names: tuple = (), skip_state_ids: frozenset = frozenset(), max_states: int = MAX_STATES,
           topic: Optional[dict[int, float]] = None, info: Optional[dict] = None, news: frozenset = frozenset()) -> tuple[list[Item], list[State], list[Item]]:
    """State-first selection: every state shown must itself bear on the input, not merely belong to a thing whose name was said.

    A state qualifies when
      - it is a keyed current value (a PR's CI, an agent's status) of a thing the input names, or any state of a SPECIFIC named thing (few states);
      - it is close in meaning to the input (cosine >= floor);
      - it belongs to a generic named thing (a repo, a tool) and is nearly that close AND shares a rare word with the input; or
      - it shares two rare words with the input and is nearly that close in meaning.
    Naming "the repo" or "the tool" alone surfaces nothing. Of two qualifying states about the same subject (close in meaning, or one likely replacing
    the other) only the newer is shown. Agents and people are never surfaced because their name was said (they are in the session core), but what was
    learned about them can surface by meaning; their standing rule/stable states are left out (the core already shows them).
    The ongoing conversation counts: `topic` maps thing ids to how much they have been named or surfaced in the last turns (decaying; see the
    adapter). A thing at or above TOPIC_ON is what is being discussed, so a common word that names it ("board") resolves to it again: naming it is
    still not enough alone, but its states need less meaning (TOPIC_SLACK below the floor, no rare word) to qualify, so the relevant ones surface.
    When the conversation moves on the score decays and the word is generic again. Usefulness weights (Store.state_weights, set by the nightly job from outcomes) nudge a state's
    meaning score by at most a few hundredths: a state shown again and again and never used, or said to be noise, needs to be a little closer.
    Returns (items shown, all qualifying states in rank order, items dropped by the caps); `info`, when given, receives the named thing ids."""
    named = _named(store, text, exclude_kinds, exclude_names)
    topic = topic or {}
    on_topic = {tid for tid, v in topic.items() if v >= TOPIC_ON}
    weights = store.state_weights() if hasattr(store, "state_weights") else {}
    if info is not None:
        info["named"] = [tid for tid, (_t, why, _s) in named.items() if why == "named"]
        info["on_topic"] = sorted(on_topic)
    pool = [s for s in store.searchable_states() if s.id not in skip_state_ids]
    things: dict[int, Optional[Thing]] = {}

    def thing_of(tid: int) -> Optional[Thing]:
        if tid not in things:
            things[tid] = store.get(tid)
        return things[tid]

    # how many standing states are about a thing (as subject or object), and whether other things sit under it: a repo or a tool everyone mentions is
    # generic, a ticket or a customer project is specific
    standing_count: dict[int, int] = {}
    for s in pool:
        for tid in {s.thing_id, s.object_id} - {None}:
            standing_count[tid] = standing_count.get(tid, 0) + 1
    parents = {t.parent_id for t in store.things() if t.parent_id is not None}
    # a name that many states mention in passing ("board", a tool, a repo) is a common word here, not a pointer to one subject
    texts = None
    mentioned_widely: set[int] = set()
    for tid, (thing, _why, _score) in named.items():
        if texts is None:
            texts = [words(split_identifiers(s.text)) for s in pool]
        label = split_identifiers(thing.name)
        if sum(1 for w in texts if occurrences(w, label) > 0) > max(GENERIC_MENTIONS, len(pool) // 50):
            mentioned_widely.add(tid)
    cos: dict[int, float] = {}
    vec: dict[int, list] = {}
    if embedder is not None and text.strip():
        from .embed import ensure_vectors
        try:
            ensure_vectors(store, embedder, limit=64)
            query = embedder.embed([split_identifiers(text[-QUERY_CHARS:])])[0]
        except Exception:
            query = None
        if query is not None:
            for v in store.vectors("state", embedder.model):
                vec[v["owner_id"]] = v["vector"]
                cos[v["owner_id"]] = cosine(query, v["vector"])
    # rare words: in at most 1% of standing states (at least 3); the words of names the input matched do not count as shared content
    df: dict[str, int] = {}
    state_terms: dict[int, set[str]] = {}
    for s in pool:
        st = terms(f"{s.thing_name} {s.text}")
        state_terms[s.id] = st
        for w in st:
            df[w] = df.get(w, 0) + 1
    rare_cut = max(3, len(pool) // 100)
    name_words = {w for tid in named for w in words(split_identifiers(named[tid][0].name))}
    asked = {w for w in terms(text) if df.get(w, 0) <= rare_cut and w not in name_words}

    scored: list[tuple[float, State]] = []
    for s in pool:
        t = thing_of(s.thing_id)
        if t is None or t.retired or t.name.lower() in exclude_names:
            continue
        if t.kind in exclude_kinds and s.status == "active" and s.volatility in ("rule", "stable"):
            continue
        c = cos.get(s.id)
        if c is not None and s.id in weights:
            c += (weights[s.id] - 1.0) * WEIGHT_SPAN
        shared = len(asked & state_terms.get(s.id, set()))
        topical = s.thing_id in on_topic
        own = named.get(s.thing_id)
        hit = own or (named.get(s.object_id) if s.object_id else None)
        # naming is enough on its own only for the thing's own states, spelled whole, when the thing is specific
        specific = own is not None and (own[1] == "named" or own[2] >= CLOSE_SPELLING) and standing_count.get(s.thing_id, 0) <= SPECIFIC_MAX and s.thing_id not in parents \
            and s.thing_id not in mentioned_widely and not topical    # on topic: its states must bear on this input (no rotation through them)
        if s.state_key:
            if own is not None and own[1] == "named":  # a named thing's current values; never reached by meaning alone (they are short labels)
                scored.append((2.0, s))
            continue
        if s.id in news and own is not None:
            scored.append((1.5, s))                                  # changed since this thing was last shown, and named again: that is news
        elif specific:
            scored.append((1.0 + (c or 0.0) / 10, s))
        elif c is not None and c >= floor:
            scored.append((c, s))
        elif c is not None and own is not None and topical and c >= floor - TOPIC_SLACK:
            scored.append((c, s))                                    # the thing we are talking about, named again
        elif c is not None and own is None and topical and c >= floor - TOPIC_NEAR:
            scored.append((c, s))                                    # the thing we are talking about, not named this time
        elif c is not None and hit is not None and c >= floor - NAMED_SLACK and shared >= 1:
            scored.append((c, s))
        elif c is not None and shared >= 2 and c >= floor - WORD_SLACK - WORD_STEP * min(shared - 2, 1):
            scored.append((c, s))
        elif c is None and hit is not None and shared >= 2:          # meaning is down: a named generic thing still needs the input's own words
            scored.append((0.5 + shared / 100, s))
    scored.sort(key=lambda x: (-x[0], x[1].id))
    # newer facts about the same subject replace older ones at injection time
    by_thing: dict[int, list[State]] = {}
    for _, s in scored:
        if s.state_key is None:
            by_thing.setdefault(s.thing_id, []).append(s)
    replaced: set[int] = set()
    for sts in by_thing.values():
        if len(sts) > 1:
            replaced |= set(likely_replaces(sts, store.evidence_of([x.id for x in sts])).values())
    plain = [s for _, s in scored if s.state_key is None and s.id in vec]
    kept: list[tuple[float, State]] = []
    for score, s in scored:
        if s.state_key is None:
            if s.id in replaced:
                continue
            if s.id in vec and any(o.id != s.id and o.recency > s.recency and cosine(vec[s.id], vec[o.id]) >= SAME_SUBJECT for o in plain):
                continue
        kept.append((score, s))
    items: dict[int, Item] = {}
    order: list[int] = []
    for score, s in kept:
        tid = s.thing_id
        if tid not in items:
            hit = named.get(tid)
            why = hit[1] if hit else f"meaning {score:.2f}"
            items[tid] = Item(thing_of(tid), why, score, [], s.id)  # type: ignore[arg-type]
            order.append(tid)
        it = items[tid]
        cap = 4 if s.state_key else states_each
        if sum(1 for x in it.states if bool(x.state_key) == bool(s.state_key)) < cap:
            it.states.append(s)
    shown: list[Item] = []
    dropped: list[Item] = []
    shown_states = 0
    for tid in order:
        it = items[tid]
        if len(shown) >= max_things or shown_states >= max_states:
            dropped.append(it)
            continue
        if not any(x.state_key for x in it.states):
            it.states = it.states[:max(1, max_states - shown_states)]
        shown_states += sum(1 for x in it.states if not x.state_key) + (1 if any(x.state_key for x in it.states) else 0)   # a thing's keyed values are one line
        shown.append(it)
    return shown, [s for _, s in kept], dropped


def _pointer(ev: list[Evidence]) -> str:
    if not ev:
        return ""
    e = ev[0]
    kind = {"message": "msg", "legacy-source": "src", "prototype": "src"}.get(e.ref_type, e.ref_type)
    return f"{kind} {_first_line(e.ref, 28)}"


AGENT_WORDS = frozenset({"agents", "shepherds", "subagents"})     # plural: "how are the agents doing", not "running a local agent"
STATUS_WORDS = frozenset({"running", "working", "stuck", "silent", "status", "alive", "activity", "progress", "doing", "finished", "done", "hung", "stalled"})


def _agents_line(store: Store, text: str, today: datetime, skip: frozenset) -> tuple[str, list[int]]:
    """When the input asks how agents are doing, one line of what the live agent.status states that changed in the last hours say now."""
    seq = words(text)
    agent_at = [i for i, w in enumerate(seq) if w in AGENT_WORDS]
    if not any(seq[j] in STATUS_WORDS for i in agent_at for j in range(max(0, i - 3), min(len(seq), i + 4))):
        return "", []                                  # "agents" and a status word close together: the input asks how agents are doing
    by_status: dict[str, list[str]] = {}
    ids: list[int] = []
    for s in store.searchable_states():
        if s.state_key != "agent.status" or s.id in skip:
            continue
        try:
            seen_h = (today - datetime.fromisoformat((s.confirmed_utc or s.recorded_utc)[:19]).replace(tzinfo=timezone.utc)).total_seconds() / 3600
            changed_h = (today - datetime.fromisoformat(s.recorded_utc[:19]).replace(tzinfo=timezone.utc)).total_seconds() / 3600
        except ValueError:
            continue
        if seen_h > LIVE_STALE_HOURS or changed_h > LIVE_STALE_HOURS:      # not re-read lately, or a status that has not changed in hours (a dead agent)
            continue
        by_status.setdefault((s.text.split() or ["?"])[0], []).append(s.thing_name)
        ids.append(s.id)
    if not ids:
        return "", []
    parts = [f"{k} {len(v)}" + (f" ({', '.join(sorted(v)[:4])})" if k in ("working", "silent") else "") for k, v in sorted(by_status.items())]
    lines = ["Agents now (agent.status read from transcripts, last 6h): " + "; ".join(parts)]
    # the standing rules about agents belong next to their status (e.g. which ones can still be resumed)
    for s in store.searchable_states():
        if s.kind == "rule" and s.status == "active" and s.id not in skip and {w.rstrip("s") + "s" for w in words(s.thing_name)} & AGENT_WORDS and len(lines) < 3:
            lines.append(f"  #{s.id} {_first_line(s.text, 200)} [{_freshness(s, today)}]")
            ids.append(s.id)
    return "\n".join(lines), ids


def surface(store: Store, text: str, embedder: Optional[Embedder] = None, *, floor: Optional[float] = None, max_things: int = 3,
            states_each: int = 2, budget: int = 2500, today: Optional[datetime] = None, log_turn: Optional[str] = None,
            exclude_kinds: tuple = (), exclude_names: tuple = (), skip_state_ids: frozenset = frozenset(), max_states: int = MAX_STATES,
            topic: Optional[dict[int, float]] = None, news: frozenset = frozenset()) -> Surfaced:
    """The state block for an input (see `select` for what qualifies): at most `max_things` things and `max_states` states, only the newest about any
    one subject, one line each with ONE trust marker and one short evidence pointer, within `budget` characters, saying what it left out. Empty when
    nothing bears on the input."""
    today = today or datetime.now(timezone.utc)
    floor = store.get_param("relevance_floor", DEFAULT_FLOOR) if floor is None else floor
    info: dict = {}
    items, _, dropped = select(store, text, embedder, floor=floor, max_things=max_things, states_each=states_each, exclude_kinds=exclude_kinds,
                               exclude_names=exclude_names, skip_state_ids=skip_state_ids, max_states=max_states, topic=topic, info=info, news=news)
    named = info.get("named", [])
    agents, agent_ids = _agents_line(store, text, today, skip_state_ids)
    if not items and not agents:
        return Surfaced("", [], named)
    lines = ["Current state bearing on this input (#id = state; [how sure · evidence]; raw history is not shown):"]
    shown: list[Item] = []
    omitted: list[str] = [i.thing.name for i in dropped]
    for item in items:
        ev = store.evidence_of([s.id for s in item.states])
        item.evidence = ev
        chunk = [f"{item.thing.name} ({item.thing.kind}; {item.why}):"]
        keyed = [s for s in item.states if s.state_key]
        if keyed:
            # a thing's current values (a PR's state, review, CI) on ONE line, with the oldest verification as its marker and one way to re-check
            oldest = min(keyed, key=lambda s: s.confirmed_utc or s.recorded_utc)
            mark = _freshness(oldest, today).split("; re-check")[0]
            how = next((s.verify_handle for s in keyed if s.verify_handle), None)
            chunk.append(f"  {' | '.join(f'#{s.id} {s.state_key.split(".")[-1]}: {_first_line(s.text, 90)}' for s in keyed)} [{mark}{' · re-check: ' + _first_line(how, 70) if how else ''}]")
        for s in item.states:
            if s.state_key:
                continue
            rel = f" -> {s.object_name}" if s.object_name else ""
            label = f"{s.state_key}: " if s.state_key else ""
            ptr = _pointer(ev.get(s.id, []))
            chunk.append(f"  #{s.id} {label}{_first_line(s.text, 200)}{rel} [{_freshness(s, today)}{' · ' + ptr if ptr else ''}]")
        block = "\n".join(chunk)
        if sum(len(x) + 1 for x in lines) + len(block) > budget:
            omitted.append(item.thing.name)
            dropped.append(item)
            continue
        lines.append(block)
        shown.append(item)
    if agents and sum(len(x) + 1 for x in lines) + len(agents) <= budget:
        lines.append(agents)
    else:
        agent_ids = []
    if log_turn:
        for item in shown:
            for s in item.states:
                store.log_outcome(s.id, "surfaced", source="surface", turn_ref=log_turn, detail=json.dumps({"why": item.why, "status": s.status, "trust": s.trust}))
        for sid in agent_ids:
            st = store.get_state(sid)
            store.log_outcome(sid, "surfaced", source="surface", turn_ref=log_turn, detail=json.dumps({"why": "agents line", "status": st.status if st else None, "trust": st.trust if st else None}))
        for item in dropped:
            top = item.states[:1] or sorted(store.states_of(item.thing.id), key=lambda s: s.recency, reverse=True)[:1]
            if top:
                store.log_outcome(top[0].id, "omitted", source="surface", turn_ref=log_turn, detail=json.dumps({"why": item.why}))
    if not shown and not agent_ids:
        return Surfaced("", [], named)
    if omitted:
        lines.append(f"{len(omitted)} omitted: {', '.join(omitted[:4])}")
    return Surfaced("\n".join(lines), shown, named)
