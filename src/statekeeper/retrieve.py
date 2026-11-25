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


def live_age_hours(s: State, today: datetime) -> float:
    """Hours since a state was last read (verified) or, never re-read, written; unreadable timestamps count as very old."""
    try:
        return (today - datetime.fromisoformat((s.confirmed_utc or s.recorded_utc)[:19]).replace(tzinfo=timezone.utc)).total_seconds() / 3600
    except (ValueError, TypeError):
        return 1e9


def stale_live(s: State, today: datetime) -> bool:
    """A live value (not the owner's word) that nobody has re-read within LIVE_STALE_HOURS: it must not be shown as current."""
    return s.volatility == "live" and s.trust != "owner" and live_age_hours(s, today) > LIVE_STALE_HOURS


WORK_ROUTE_DAYS = 7        # a point-in-time ('work') state nobody confirmed for this long reaches an input only through its thing's name


def as_of(s: State) -> str:
    """The date a point-in-time state was true: its as-of date, else when it was written."""
    return (s.as_of_utc or s.recorded_utc or "")[:10]


HIERARCHY = __import__("os").environ.get("STATEKEEPER_HIERARCHY", "1").strip().lower() not in ("0", "off", "false", "no")
ANCESTOR_SLACK = 0.10      # an ancestor with many states contributes only those nearly as close as the floor
NAME_ONLY_WRITERS = ("import:conversation",)   # bulk-extracted from old conversations: a low-trust tier until reviewed or confirmed


LEAF_SPECIFIC_MAX = 12     # a thing filed under a parent with nothing under it is a leaf: naming it shows its states up to this many


def _subsumed(name: str, others: list[str], text: str) -> bool:
    """Said only as part of a longer name the input also says: "pdf-converter" inside "pdf-converter-service-enabled" names the flag, not the
    converter. Said on its own as well ("pdf-converter and pdf-converter-service-enabled"), it is named."""
    seq, mine = words(text), words(name)
    if not mine:
        return False
    for o in others:
        theirs = words(o)
        if len(theirs) <= len(mine):
            continue
        inside = sum(1 for i in range(len(theirs) - len(mine) + 1) if theirs[i:i + len(mine)] == mine)
        said = occurrences(seq, o)                                     # the longer name must be said, not only resolved from a number
        if inside and said and occurrences(seq, name) <= inside * said:
            return True
    return False


def _stem_key(name: str) -> tuple[str, ...]:
    """A name's words cut to five letters: "board watcher" and "board-watch" are one thing spelled two ways, never each other's sibling."""
    return tuple(w[:5] for w in _WORDS.findall(name.lower()))


def name_only(s: State) -> bool:
    """A state that reaches an input only through its thing's name (or a number it carries): extracted in bulk from old conversations, and not yet
    reviewed (still 'extracted' trust) or confirmed by anyone."""
    return s.writer.startswith(NAME_ONLY_WRITERS) and s.trust == "extracted" and not s.confirmed_utc


ANCESTOR_FLOOR_SCORE = 0.949   # an ancestor's state scores 0.95 + closeness/20; a nudge never lifts a closeness hit above that
STANDING_NUDGE = 0.05        # per-prompt: a decision/preference/constraint ranks this far above, and a progress/status note this far below, a state of the same closeness
NUDGED_ROUTES = ("meaning", "words", "topic", "asked")     # only states that qualified by closeness are nudged; named, specific and ancestor hits keep their rank
_DECISION_CUE = re.compile(r"\b(decided|decision|standing rule|rule|prefers?|always|never|must|do not|don't|settled|policy|constraint|agreed)\b", re.I)
_PROGRESS_CUE = re.compile(r"\b(ci (is )?(green|red)|checks? passed|is (open|draft|merged)|agent\b.{0,40}\b(running|started|finished|reported|stalled)|"
                           r"(started|finished|running|stalled|waiting)\b.{0,30}\bagent)\b", re.I)


def standing_knowledge(s: State) -> bool:
    """A decision, preference or constraint. The stored type is a weak signal (most extracted decisions are volatility 'work'), so the wording counts too."""
    return s.state_key is None and (s.kind == "rule" or s.volatility in ("rule", "stable") or bool(_DECISION_CUE.search(s.text)))


def progress_note(s: State) -> bool:
    """An agent's progress or a PR's/CI's status in prose: the live probes and the keyed values cover what is current."""
    return s.state_key is None and not standing_knowledge(s) and bool(_PROGRESS_CUE.search(s.text))


def numbered(name: str) -> bool:
    """A thing whose name carries a ticket, PR or build number ("CRM-3148", "PR #3376")."""
    from .identifiers import numbers_in_text
    return bool(numbers_in_text(name))


def point_in_time(s: State) -> bool:
    """A status that was true at a date ('work', unkeyed). Facts imported from the earlier store that it marked as NOT a state (durable facts that
    only got the default volatility) are not point-in-time."""
    return s.volatility == "work" and not s.state_key and not (s.writer.startswith("import:") and not s.is_state)


def stale_work(s: State, today: datetime) -> bool:
    """A point-in-time status last confirmed, or else dated, more than WORK_ROUTE_DAYS ago."""
    if not point_in_time(s):
        return False
    when = max(s.confirmed_utc or "", s.as_of_utc or s.recorded_utc or "")[:19].replace("T", " ")
    try:
        return (today - datetime.fromisoformat(when).replace(tzinfo=timezone.utc)).days > WORK_ROUTE_DAYS
    except ValueError:
        return False


def _freshness(s: State, today: datetime) -> str:
    """ONE short marker for how far to trust a state right now (a point-in-time state also says the date it was true)."""
    mark = _trust_mark(s, today)
    return f"as of {as_of(s)} · {mark}" if point_in_time(s) and as_of(s) else mark


def _trust_mark(s: State, today: datetime) -> str:
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
        age_h = live_age_hours(s, today)
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


VERY_RARE = int(__import__("os").environ.get("STATEKEEPER_VERY_RARE", "5"))   # a word in at most this many states is nearly a name (0 turns the path off)
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
CONCEPT_SIM = 0.80       # an input this close in meaning to an unlinked concept (or carrying its words) is "about" it
WEIGHT_SPAN = 0.10       # a usefulness weight w moves a state's meaning score by (w - 1) * WEIGHT_SPAN (w is kept in 0.5..1.1, so -0.05..+0.01)


SEG_PENALTY = float(__import__("os").environ.get("STATEKEEPER_SEG_PENALTY", "0.03"))   # a clause's match counts this much less than the whole input's
SEG_MAX = 8              # at most this many clauses are embedded besides the whole input
SEG_MIN_WORDS = 5        # a clause shorter than this is joined to the next ("Oh yeah." is not a question)
_CLAUSE = re.compile(r"(?<=[.?!;])\s+|\n+")
_HOWTO_TEXT = re.compile(r"`[^`]+`|\b\w+\.(mjs|js|py|ps1|sh|bat|cmd|exe)\b|\s--[a-z]|\b[A-Z]:[/\\]|\b(run|use|via|with|through|query|queried)\b .{0,40}\b(cli|script|tool|skill|command|mcp)\b|"
                        r"\b(cli|script|tool|skill|command)\b", re.I)


Q_FLOOR = float(__import__("os").environ.get("STATEKEEPER_Q_FLOOR", "0.84"))   # a request the state answers this close to the input (or a clause) qualifies it


Q_MIN_COS = float(__import__("os").environ.get("STATEKEEPER_Q_MIN_COS", "0"))   # ... and the state's own text at least this close


def _unit(v: list) -> list:
    n = sum(x * x for x in v) ** 0.5 or 1.0
    return [x / n for x in v]


def _dot(a: list, b: list) -> float:
    from operator import mul
    return sum(map(mul, a, b))


def segments(text: str) -> list[str]:
    """The input's clauses (sentences), short ones joined to the next, longest first, at most SEG_MAX. A long dictated prompt is about several
    things at once; a state relevant to one clause is far from the whole prompt in meaning but close to that clause."""
    parts = [p.strip() for p in _CLAUSE.split(text[-QUERY_CHARS * 2:]) if p.strip()]
    out: list[str] = []
    buf = ""
    for p in parts:
        buf = f"{buf} {p}".strip()
        if len(buf.split()) >= SEG_MIN_WORDS:
            out.append(buf)
            buf = ""
    if buf and out:
        out[-1] = f"{out[-1]} {buf}"
    elif buf:
        out.append(buf)
    if len(out) <= 1:
        return []                       # one clause: the whole input already is it
    return sorted(out, key=lambda x: -len(x))[:SEG_MAX]


def is_howto(s: State) -> bool:
    """A state that says how to do something: a standing rule or durable fact, or one that names a command, script, path or tool."""
    return s.kind == "rule" or s.volatility in ("stable", "rule") or bool(_HOWTO_TEXT.search(s.text))


def embed_text(s: State, aliases: Optional[dict[int, list[str]]] = None) -> str:
    """What a state's vector is made from: its thing, what it is about (the object), the thing's other names, and its text. The object and the
    aliases matter when the subject is a person or an agent ("Jane fills .env.local for login-tool")."""
    head = s.thing_name + (f" / {s.object_name}" if s.object_name else "")
    also = (aliases or {}).get(s.thing_id, []) + (aliases or {}).get(s.object_id, []) if aliases else []
    return (f"{head}" + (f" ({', '.join(also[:4])})" if also else "") + f": {s.text}")[:400]


INTENTS = __import__("os").environ.get("STATEKEEPER_INTENTS", "1").strip().lower() not in ("0", "off", "false", "no")    # see intents.py
PEOPLE_BY_NAME = __import__("os").environ.get("STATEKEEPER_PEOPLE_BY_NAME", "1").strip().lower() not in ("0", "off", "false", "no")
IDENTIFIERS = __import__("os").environ.get("STATEKEEPER_IDENTIFIERS", "1").strip().lower() not in ("0", "off", "false", "no")   # see identifiers.py
_PR_URL = re.compile(r"https?://github\.com/[^/\s]+/([^/\s]+)/pull/(\d+)\S*")


def _named(store: Store, text: str, exclude_kinds: tuple, exclude_names: tuple, ids=None) -> dict[int, tuple[Thing, str, float]]:
    text = _PR_URL.sub(lambda m: f"{m.group(0)} {m.group(1)} PR {m.group(2)}", text)      # a pasted PR link names "<repo> PR <n>"
    seq, split = words(text), words(split_identifiers(text))
    found: dict[int, tuple[Thing, str, float]] = {}
    labels = store.labels()
    # a person or agent can be named too (their standing rules stay out: they are in the session core; what is being done with them may surface)
    passing = tuple(k for k in exclude_kinds if not (PEOPLE_BY_NAME and k in ("person", "agent")))
    first_names: dict[str, list[Thing]] = {}
    for tid, label in labels:
        thing = store.get(tid)
        if thing is None or thing.retired or thing.kind in passing or thing.name.lower() in exclude_names:
            continue
        if PEOPLE_BY_NAME and thing.kind == "person" and len(label.split()) > 1 and label == thing.name:
            first_names.setdefault(label.split()[0], []).append(thing)
        if ids is not None and all(w.isdigit() for w in words(label) or ["x"]):
            continue                                                  # a bare-number name ("#3000") is named only as an identifier (below), never by a count
        if occurrences(seq, label) > 0 or (split_identifiers(label) != label and occurrences(split, split_identifiers(label)) > 0):
            found[tid] = (thing, "named", 1.0)
        elif tid not in found and (m := fuzzy_match(seq, label)):
            found[tid] = (thing, f'spelled "{m[1]}"', m[0])
    if ids is not None:
        # a bare PR/ticket/build number ("is 3342 still a draft", "CRM-3150", "build 13801") names every thing that carries it
        from .intents import asks_ship
        ids.resolved = set(ids.resolve(text, seq, common=INTENTS and asks_ship(text)))
        for tid in ids.resolved:
            if tid in found and found[tid][1] == "named":
                continue
            thing = store.get(tid)
            if thing is None or thing.retired or thing.kind in exclude_kinds or thing.name.lower() in exclude_names:
                continue
            found[tid] = (thing, "named", 1.0)
    # "Jen" names Jen Gandy when she is the only person by that first name and it is written capitalized, as a name
    for first, people in first_names.items():
        if len(people) == 1 and people[0].id not in found and re.search(rf"(?<![\w-]){re.escape(first)}(?![\w-])", text):
            found[people[0].id] = (people[0], "named", 1.0)
    return found


_ASK_ABOUT = r"\b(who\W?s|who is|who was|what does|what did|what is|what's|tell me about|remind me (who|about)|about)\s+{name}\b|\b{name}'s\s+(role|job|work|team|area)|\b{name}\s+(again|works? on|does|owns)\b"


ROLLUP = __import__("os").environ.get("STATEKEEPER_ROLLUP", "1").strip().lower() not in ("0", "off", "false", "no")
ROLLUP_MAX = 8
_PRS = re.compile(r"\b(prs|pull requests|which pr|what pr|any pr|all (the |my |our )?pr|open pr|my pr|our pr)s?\b", re.I)
# what the input asks about -> (keyed relation, how its value starts)
_ROLLUP_ASKS = [(re.compile(r"\b(red|failing|failed|broken|failures?)\b", re.I), "pr.ci", "ci red"),
                (re.compile(r"\b(green|passing)\b", re.I), "pr.ci", "ci green"),
                (re.compile(r"\b(drafts?|still draft)\b", re.I), "pr.state", "draft"),
                (re.compile(r"\b(approved|approval)\b", re.I), "pr.review", "approved"),
                (re.compile(r"\b(changes requested)\b", re.I), "pr.review", "changes requested")]


def pr_rollup(pool: list[State], text: str) -> list[State]:
    """For "which PRs are red right now" and the like: the asked-for keyed value of every PR that is still open and still being observed (its
    pr.state confirmed within LIVE_STALE_HOURS of the newest PR observation, so a PR the observer stopped reading is not listed as current)."""
    if not _PRS.search(text):
        return []
    asks = [(key, start) for rx, key, start in _ROLLUP_ASKS if rx.search(text)]
    if not asks:
        return []
    by_thing: dict[int, dict[str, State]] = {}
    for s in pool:
        if s.state_key and s.state_key.startswith("pr."):
            by_thing.setdefault(s.thing_id, {})[s.state_key] = s
    seen = [st.confirmed_utc or st.recorded_utc for vals in by_thing.values() for k, st in vals.items() if k == "pr.state"]
    if not seen:
        return []

    def at(ts: str) -> datetime:
        return datetime.fromisoformat(ts[:19].replace(" ", "T")).replace(tzinfo=timezone.utc)
    newest = max(at(x) for x in seen)
    out = []
    for vals in by_thing.values():
        st = vals.get("pr.state")
        if st is None or st.text.strip().lower().startswith(("merged", "closed")):
            continue
        if (newest - at(st.confirmed_utc or st.recorded_utc)).total_seconds() > LIVE_STALE_HOURS * 3600:
            continue
        for key, start in asks:
            v = vals.get(key)
            if v is not None and v.text.strip().lower().startswith(start):
                out.append(v)
                break
    return sorted(out, key=lambda s: s.thing_name)


def asks_about(text: str, name: str) -> bool:
    """Whether the input asks about a person ("who's Jeremy again", "what does Jen work on", "Adam's role"), by full or first name. Only then does
    naming a person with few states show what is known about them; otherwise their states need meaning like any generic name."""
    for n in {name, name.split()[0]}:
        if re.search(_ASK_ABOUT.format(name=re.escape(n)), text, re.I):
            return True
    return False


def candidates(store: Store, text: str, embedder: Optional[Embedder], floor: float, limit: int) -> list[Item]:
    """The things `select` would show for an input, best first (for callers that want things, not states)."""
    return select(store, text, embedder, floor=floor, max_things=limit)[0]


def select(store: Store, text: str, embedder: Optional[Embedder], *, floor: float, max_things: int = 3, states_each: int = 2,
           exclude_kinds: tuple = (), exclude_names: tuple = (), skip_state_ids: frozenset = frozenset(), max_states: int = MAX_STATES,
           topic: Optional[dict[int, float]] = None, info: Optional[dict] = None, news: frozenset = frozenset(),
           today: Optional[datetime] = None, per_prompt: bool = False) -> tuple[list[Item], list[State], list[Item]]:
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
    `per_prompt` (the inject hook) shapes the block that goes in front of every prompt: an open board ask reaches it only through the words of its own
    title (the live probes already show the open asks), standing knowledge (rule/stable states: decisions, preferences, constraints) ranks above status
    lines of about the same closeness (the unreviewed tier is not demoted: most extracted decisions are unreviewed, and demoting them cost rank 1).
    Returns (items shown, all qualifying states in rank order, items dropped by the caps); `info`, when given, receives the named thing ids."""
    standing_all = store.searchable_states()
    today = today or datetime.now(timezone.utc)
    ids = None
    if IDENTIFIERS:
        from .identifiers import IdentifierIndex
        ids = IdentifierIndex(store.labels(), [" ".join(words(f"{s.thing_name} {s.text}")) for s in standing_all if not name_only(s)],
                              live_values=[(s.thing_id, s.text) for s in standing_all if s.state_key and s.volatility == "live" and s.status == "active"])
    named = _named(store, text, exclude_kinds, exclude_names, ids)
    by_number = getattr(ids, "resolved", set()) if ids is not None else set()
    topic = topic or {}
    on_topic = {tid for tid, v in topic.items() if v >= TOPIC_ON}
    weights = store.state_weights() if hasattr(store, "state_weights") else {}
    if info is not None:
        info["named"] = [tid for tid, (_t, why, _s) in named.items() if why == "named"]
        info["on_topic"] = sorted(on_topic)
    pool = [s for s in standing_all if s.id not in skip_state_ids]
    things: dict[int, Optional[Thing]] = {}

    def thing_of(tid: int) -> Optional[Thing]:
        if tid not in things:
            things[tid] = store.get(tid)
        return things[tid]

    # how many standing states are about a thing (as subject or object), and whether other things sit under it: a repo or a tool everyone mentions is
    # generic, a ticket or a customer project is specific
    standing_count: dict[int, int] = {}
    own_count: dict[int, int] = {}
    # the bulk-imported, unreviewed tier is hidden from every statistic below (counts, mentions, word rarity): thousands of old-conversation
    # states must not turn a specific thing generic or a rare word common
    tier = {s.id for s in pool if name_only(s)}
    counted = [s for s in pool if s.id not in tier]
    for s in counted:
        own_count[s.thing_id] = own_count.get(s.thing_id, 0) + 1
        for tid in {s.thing_id, s.object_id} - {None}:
            standing_count[tid] = standing_count.get(tid, 0) + 1
    all_things = store.things()
    # a container many things are filed under (a repo, a build definition, the board's asks) is generic, so naming it is not enough; a thing with a
    # few sub-things (a ticket and its PRs, "board watcher" and its Monitor) is still specific
    child_count: dict[int, int] = {}
    for t in all_things:
        if t.parent_id is not None:
            child_count[t.parent_id] = child_count.get(t.parent_id, 0) + 1
    parents = {p for p, n in child_count.items() if n > SPECIFIC_MAX} if HIERARCHY else set(child_count)
    leaves_filed = {t.id for t in all_things if t.parent_id is not None and t.id not in child_count} if HIERARCHY else set()
    # the hierarchy (topics.TopicTree: parent_id plus name containment): the most specific named things are the leaves of what the input names;
    # their ancestors' states come along, their siblings (and the siblings' sub-things) stay out unless the input names them too
    ancestor_of: set[int] = set()
    siblings: set[int] = set()
    if HIERARCHY:
        from .topics import TopicTree
        tt = TopicTree(all_things)
        # a name said only inside a longer named name that it is not above ("pdf-converter" inside the flag "pdf-converter-service-enabled")
        # is not named; one that is above any named thing ("DocRaptor" in "DocRaptor fallback") stays named
        above = {a for t2, o in named.items() if o[1] == "named" for a in tt.ancestors(t2)}
        named = {tid: v for tid, v in named.items()
                 if tid in above or not _subsumed(v[0].name, [o[0].name for t2, o in named.items() if t2 != tid and o[1] == "named"], text)}
        named_ids = {tid for tid, (_t, why, _s) in named.items() if why == "named"}
        leaves = {tid for tid in named_ids if not (tt.descendants(tid, limit=400) & named_ids)}
        filed_under: dict[int, set[int]] = {}                         # explicit filing only (parent_id): name containment is too loose to exclude by
        for t in all_things:
            if t.parent_id is not None and not t.retired:
                filed_under.setdefault(t.parent_id, set()).add(t.id)
        by_id = {t.id: t for t in all_things}
        for leaf in leaves:
            ups = tt.ancestors(leaf)
            ancestor_of |= ups
            p = by_id[leaf].parent_id if leaf in by_id else None
            while p is not None and p in by_id:
                for c in filed_under.get(p, ()):
                    if c != leaf and c not in ups and c not in named_ids and _stem_key(by_id[c].name) != _stem_key(by_id[leaf].name):
                        siblings |= {c} | tt.descendants(c, limit=400)
                p = by_id[p].parent_id
        ancestor_of -= named_ids
        siblings -= named_ids | ancestor_of
    # a name that many states mention in passing ("board", a tool, a repo) is a common word here, not a pointer to one subject
    texts = None
    mentioned_widely: set[int] = set()
    for tid, (thing, _why, _score) in named.items():
        if texts is None:
            texts = [words(split_identifiers(s.text)) for s in counted]
        label = split_identifiers(thing.name)
        if sum(1 for w in texts if occurrences(w, label) > 0) > max(GENERIC_MENTIONS, len(counted) // 50):
            mentioned_widely.add(tid)
    query = None
    cos: dict[int, float] = {}
    qcos: dict[int, float] = {}
    vec: dict[int, list] = {}
    if embedder is not None and text.strip():
        from .embed import ensure_vectors
        try:
            ensure_vectors(store, embedder, limit=64)
            segs = segments(text)
            vecs = embedder.embed([split_identifiers(text[-QUERY_CHARS:])] + [split_identifiers(x) for x in segs])
            query, seg_vecs = vecs[0], vecs[1:]
        except Exception:
            query, seg_vecs = None, []
        if query is not None:
            qu, su = _unit(query), [_unit(q) for q in seg_vecs]
            for v in store.vectors("state", embedder.model):
                vec[v["owner_id"]] = v["vector"]
                u = _unit(v["vector"])
                c0 = _dot(qu, u)
                if su:
                    c0 = max(c0, max(_dot(q, u) for q in su) - SEG_PENALTY)
                cos[v["owner_id"]] = c0
            # requests each durable state answers (questions.py): the best match of any of them to the input or one of its clauses
            if hasattr(store, "questions"):
                try:
                    from .questions import ensure_question_vectors
                    ensure_question_vectors(store, embedder, limit=32)
                    owner = {qid: sid for qid, sid, _t in store.questions()}
                    for v in store.vectors("question", embedder.model):
                        sid = owner.get(v["owner_id"])
                        if sid is not None:
                            u = _unit(v["vector"])
                            best = max(_dot(q, u) for q in [qu, *su])
                            if best > qcos.get(sid, 0.0):
                                qcos[sid] = best
                except Exception:
                    qcos.clear()
    # rare words: in at most 1% of standing states (at least 3); the words of names the input matched do not count as shared content
    df: dict[str, int] = {}
    state_terms: dict[int, set[str]] = {}
    for s in pool:
        st = terms(f"{s.thing_name} {s.text}")
        state_terms[s.id] = st
        if s.id not in tier:
            for w in st:
                df[w] = df.get(w, 0) + 1
    rare_cut = max(3, len(counted) // 100)
    name_words = {w for tid in named for w in words(split_identifiers(named[tid][0].name))}
    asked = {w for w in terms(text) if df.get(w, 0) <= rare_cut and w not in name_words}

    # negative links: a person said these are not connected
    blocked_any: dict[int, set[int]] = {}      # state -> things that, when named or on topic, keep it out
    blocked_name: dict[int, set[int]] = {}     # state -> things it must not connect through
    meaning_off: set[int] = set()              # states that surface only through their own name
    pairs: dict[int, set[int]] = {}            # thing -> things that never connect its states
    concept_off: set[int] = set()
    live = store.unlinks() if hasattr(store, "unlinks") else []
    present = set(named) | on_topic
    low_words = words(split_identifiers(text))
    concept_vec: dict[str, list] = {}
    for un in live:
        if un.state_id is None:
            pairs.setdefault(un.thing_id, set()).add(un.other_thing_id)
            pairs.setdefault(un.other_thing_id, set()).add(un.thing_id)
        elif un.concept:
            about = occurrences(low_words, un.concept) > 0
            if not about and query is not None and embedder is not None:
                try:
                    if un.concept not in concept_vec:
                        concept_vec[un.concept] = embedder.embed([un.concept])[0]
                    about = cosine(query, concept_vec[un.concept]) >= CONCEPT_SIM
                except Exception:
                    about = False
            if about:
                concept_off.add(un.state_id)
        elif un.thing_id is None:
            meaning_off.add(un.state_id)
        else:
            (blocked_any if un.match == "any" else blocked_name).setdefault(un.state_id, set()).add(un.thing_id)
    reasons: dict[int, dict] = {}
    scored: list[tuple[float, State]] = []
    for s in pool:
        t = thing_of(s.thing_id)
        if t is None or t.retired or t.name.lower() in exclude_names:
            continue
        if t.kind in exclude_kinds and s.status == "active" and s.volatility in ("rule", "stable"):
            continue
        cut = blocked_name.get(s.id, set()) | blocked_any.get(s.id, set()) | pairs.get(s.thing_id, set())
        if s.id in concept_off or (blocked_any.get(s.id, set()) | pairs.get(s.thing_id, set())) & present:
            continue                                                  # unlinked from something this input is about
        raw = cos.get(s.id)
        c = raw
        if c is not None and s.id in weights:
            c += (weights[s.id] - 1.0) * WEIGHT_SPAN
        shared = len(asked & state_terms.get(s.id, set()))
        topical = s.thing_id in on_topic and s.thing_id not in cut
        own = named.get(s.thing_id) if s.thing_id not in cut else None
        obj = named.get(s.object_id) if s.object_id and s.object_id not in cut else None
        hit = own or obj
        if (own is None or own[1] != "named") and stale_live(s, today):
            continue                                                  # a live value nobody re-read lately is not current: only naming its thing shows it (marked STALE)
        if s.thing_id in siblings and own is None:
            continue                                                  # a sibling of the thing the input names: not about this input
        if name_only(s) and not (own is not None and (s.thing_id in by_number or numbered(t.name))):
            continue                                                  # bulk-imported, unreviewed: only an identifier the input says brings it up
        if own is None and stale_work(s, today):
            continue                                                  # a week-old unconfirmed status: only its thing's name brings it back (dated)
        if s.id in meaning_off:
            c = None                                                  # surfaces only through its own name
        why_named = (lambda h: {"via": h[1].split()[0], "thing": h[0].name})
        # naming is enough on its own only for the thing's own states, spelled whole, when the thing is specific
        specific = own is not None and (own[1] == "named" or own[2] >= CLOSE_SPELLING) and (own_count.get(s.thing_id, 0) <= SPECIFIC_MAX and asks_about(text, t.name) if t.kind in ("person", "agent") else standing_count.get(s.thing_id, 0) <= (LEAF_SPECIFIC_MAX if s.thing_id in leaves_filed else SPECIFIC_MAX)) and s.thing_id not in parents \
            and s.thing_id not in mentioned_widely and not topical    # on topic: its states must bear on this input (no rotation through them)
        # a PR named by its number: its work notes count as specific too (entity_replaced then keeps only the newest one standing after a merge)
        specific = specific or (own is not None and s.thing_id in by_number and s.volatility == "work")
        if s.state_key:
            if per_prompt and s.state_key == "ask.state" and t.name.startswith("board ask auto-") and not s.text.startswith("done") \
                    and t.name[len("board ask "):].lower() not in text.lower():
                continue                                              # a generated ask (the board makes them from a PR's or an agent's state): the PR's own values and the probes cover it
            if own is not None and own[1] == "named":  # a named thing's current values; never reached by meaning alone (they are short labels)
                scored.append((2.0, s))
                reasons[s.id] = {"via": "keyed", "thing": t.name}
            elif s.state_key == "ask.state" and s.id not in meaning_off:
                # an ask is named by an opaque id, but its value carries a title: it may reach an input by its title's meaning or two of its rare words
                fold = lambda ws: {w[:-1] if w.endswith("s") and len(w) > 5 else w for w in ws}   # "rollouts" says "rollout"
                title_words = sorted(fold(terms(text)) & fold(terms(s.text.split(" · ", 3)[-1])))[:4]
                by_meaning = c is not None and c >= floor and not (per_prompt and not s.text.startswith("done"))   # an open ask: the probes show it
                if by_meaning or len(title_words) >= 2:
                    scored.append((max(c or 0.0, floor), s))
                    reasons[s.id] = {"via": "meaning" if by_meaning else "words", "thing": t.name,
                                     **({"words": title_words} if title_words else {})}
            continue
        shared_words = sorted(asked & state_terms.get(s.id, set()))[:4]
        base = {"cos": round(raw, 3) if raw is not None else None, **({"weight": weights[s.id]} if s.id in weights else {})}
        if s.id in news and own is not None:
            scored.append((1.5, s))                                  # changed since this thing was last shown, and named again: that is news
            reasons[s.id] = {"via": "news", "thing": t.name, **base}
        elif specific:
            scored.append((1.0 + (c or 0.0) / 10, s))
            reasons[s.id] = {**why_named(own), "specific": True, **base}
        elif s.thing_id in ancestor_of and own is None and (own_count.get(s.thing_id, 0) <= SPECIFIC_MAX or (c is not None and c >= floor - ANCESTOR_SLACK)):
            scored.append((0.95 + (c or 0.0) / 20, s))                # what holds for everything above the named thing (LaunchDarkly, for a flag)
            reasons[s.id] = {"via": "ancestor", "thing": t.name, **base}
        elif c is not None and c >= floor:
            scored.append((c, s))
            reasons[s.id] = {"via": "meaning", **base, **({"words": shared_words} if shared_words else {})}
        elif s.id not in meaning_off and qcos.get(s.id, 0.0) + (c - raw if c is not None and raw is not None else 0.0) >= Q_FLOOR                 and (raw or 0.0) >= Q_MIN_COS:
            qc = qcos[s.id] + (c - raw if c is not None and raw is not None else 0.0)
            scored.append((qc, s))                                  # the input asks for what this state answers
            reasons[s.id] = {"via": "asked", **base, "q": round(qcos[s.id], 3)}
        elif c is not None and own is not None and topical and c >= floor - TOPIC_SLACK:
            scored.append((c, s))                                    # the thing we are talking about, named again
            reasons[s.id] = {"via": "topic", "thing": t.name, "named": True, **base}
        elif c is not None and own is None and topical and c >= floor - TOPIC_NEAR:
            scored.append((c, s))                                    # the thing we are talking about, not named this time
            reasons[s.id] = {"via": "topic", "thing": t.name, "named": False, **base}
        elif c is not None and hit is not None and c >= floor - NAMED_SLACK and shared >= 1:
            scored.append((c, s))
            reasons[s.id] = {**why_named(hit), "generic": True, **base, "words": shared_words}
        elif c is not None and shared >= 2 and c >= floor - WORD_SLACK - WORD_STEP * min(shared - 2, 1):
            scored.append((c, s))
            reasons[s.id] = {"via": "words", **base, "words": shared_words}
        elif c is not None and VERY_RARE and any(df.get(w, 0) <= VERY_RARE for w in shared_words) and c >= floor - NAMED_SLACK:
            scored.append((c, s))                                    # one word almost nothing else says ("Eonasdan", "donor"), and nearly close enough
            reasons[s.id] = {"via": "words", **base, "words": shared_words}
        elif c is None and hit is not None and shared >= 2:          # meaning is down: a named generic thing still needs the input's own words
            scored.append((0.5 + shared / 100, s))
            reasons[s.id] = {**why_named(hit), "generic": True, **base, "words": shared_words}
    rolled: set[int] = set()
    if ROLLUP and not any(t.kind == "thing" and why == "named" and any(c.isdigit() for c in t.name) for t, why, _s in named.values()):
        for s in pr_rollup(counted, text):
            scored.append((1.9, s))                                   # "which PRs are red": the current value it asks about, for every open PR
            reasons[s.id] = {"via": "rollup", "thing": s.thing_name}
            rolled.add(s.thing_id)
    if INTENTS:
        from .intents import asks_ship, latest_release, open_asks, waiting_on_john
        for s in open_asks(counted, text):
            scored.append((1.85, s))                                  # "what is the priority order of asks": the open asks on the board
            reasons[s.id] = {"via": "rollup", "thing": s.thing_name}
            rolled.add(s.thing_id)
        if asks_ship(text):
            for s in latest_release(counted):
                scored.append((1.6, s))                               # "did X ship": what the newest release says, beside X's own state
                reasons[s.id] = {"via": "release", "thing": s.thing_name}
        for s in waiting_on_john(counted, text)[:ROLLUP_MAX]:
            scored.append((1.6, s))                                   # "is anything waiting on me": the recent notes saying so
            reasons[s.id] = {"via": "waiting", "thing": s.thing_name}
            rolled.add(s.thing_id)
    best: dict[int, tuple[float, State]] = {}
    for score, s in scored:                                           # a state reached two ways keeps its best rank, once
        if s.id not in best or score > best[s.id][0]:
            best[s.id] = (score, s)
    scored = list(best.values())
    if per_prompt:
        def nudge(sc: float, s: State) -> float:
            if reasons.get(s.id, {}).get("via") not in NUDGED_ROUTES:
                return sc
            up = STANDING_NUDGE if standing_knowledge(s) else -STANDING_NUDGE if progress_note(s) else 0.0
            return min(sc + up, max(sc, ANCESTOR_FLOOR_SCORE))       # never lifted past an ancestor's hit (0.95 and up), which it did not beat before
        scored = [(nudge(sc, s), s) for sc, s in scored]
    scored.sort(key=lambda x: (-x[0], x[1].id))
    # the unreviewed tier ranks below everything else and takes part in none of the replacements below (a newer imported note must not hide a
    # standing one): it only fills what is left after every other qualifying state
    lowest = [(sc, s) for sc, s in scored if s.id in tier]
    scored = [(sc, s) for sc, s in scored if s.id not in tier]
    # newer facts about the same subject replace older ones at injection time
    by_thing: dict[int, list[State]] = {}
    for _, s in scored:
        if s.state_key is None:
            by_thing.setdefault(s.thing_id, []).append(s)
    replaced: set[int] = set()
    if ids is not None:
        # the things that carry one PR number are one subject: notes written before it merged/closed are history, and of its work notes only the newest shows
        from .identifiers import entity_replaced
        replaced |= entity_replaced(store, ids, [s for _, s in scored], counted)
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
    from . import canonical as _canon
    scored = kept
    if _canon.ON and vec:
        # the same fact stored under several ids is one fact: show its canonical once, at the best rank any copy earned (canonical.py)
        plain_all = [s for s in standing_all if not s.state_key and not name_only(s)]
        cidx = _canon.Index(plain_all, store.evidence_of([s.id for s in plain_all]), vec, df=df)    # word rarity as select already counted it
        cidx.named = frozenset(tid for tid, (_t, why, _s) in named.items() if why == "named")

        def eligible(c: State) -> str:
            ct = thing_of(c.thing_id)
            if c.id in skip_state_ids:
                return "core"                                         # shown earlier in this session
            if ct is None or ct.retired or ct.name.lower() in exclude_names or c.id in concept_off or c.id in meaning_off:
                return "no"
            if (blocked_any.get(c.id, set()) | pairs.get(c.thing_id, set())) & present:
                return "no"
            if ct.kind in exclude_kinds and c.status == "active" and c.volatility in ("rule", "stable"):
                return "core"                                         # the session core already shows it
            return "ok"
        sub, drop, _recency = _canon.collapse([s for _, s in scored], cidx, eligible=eligible)
        if sub or drop:
            merged: list[tuple[float, State]] = []
            for score, s in scored:
                if s.id in drop:
                    continue
                if s.id in sub:
                    c = cidx.by_id[sub[s.id]]
                    reasons[c.id] = {**reasons.get(s.id, {}), "same_as": s.id}
                    merged.append((score, c))
                else:
                    merged.append((score, s))
            scored = merged
    if _canon.ON:
        # a thing whose own newest word is "frozen / retired / kept only as history": what was recorded about it before that is history (canonical.py)
        over = _canon.overtaken_by_retirement([s for _, s in scored], _canon.retired_things(standing_all), text)
        if over:
            merged, seen_decl = [], {s.id for _, s in scored}
            for score, s in scored:
                d = over.get(s.id)
                if d is None:
                    merged.append((score, s))
                elif d.id not in seen_decl and d.id not in skip_state_ids:
                    merged.append((score, d))
                    reasons[d.id] = {**reasons.get(s.id, {}), "retired_over": s.id}
                    seen_decl.add(d.id)
            scored = merged
    shown_ids = {s.id for _, s in scored}
    kept = scored + [(min(sc, 0.01), s) for sc, s in lowest if s.id not in shown_ids]
    items: dict[int, Item] = {}
    order: list[int] = []
    for score, s in kept:
        tid = s.thing_id
        if tid not in items:
            hit = named.get(tid)
            via = reasons.get(s.id, {}).get("via")
            why = hit[1] if hit else ({"rollup": "asked for this value", "release": "newest release", "waiting": "pending for John"}.get(via)
                                      or f"meaning {score:.2f}")
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
        if tid in rolled:
            if sum(1 for x in shown if x.thing.id in rolled) < ROLLUP_MAX:
                shown.append(it)                                     # a roll-up lists every match, one line each, apart from the caps
            else:
                dropped.append(it)
            continue
        if len(shown) >= max_things or shown_states >= max_states:
            dropped.append(it)
            continue
        if not any(x.state_key for x in it.states):
            it.states = it.states[:max(1, max_states - shown_states)]
        shown_states += sum(1 for x in it.states if not x.state_key) + (1 if any(x.state_key for x in it.states) else 0)   # a thing's keyed values are one line
        shown.append(it)
    if info is not None:
        info["reasons"] = {s.id: reasons.get(s.id, {}) for it in shown + dropped for s in it.states}
    return shown, [s for _, s in kept], dropped


def reason_tag(r: dict) -> str:
    """A compact "how connected" tag for the injected line, only where the connection is not obvious from the line itself (a name it carries)."""
    if r.get("same_as"):
        return f"same fact as #{r['same_as']}"                        # shown in place of a restatement that matched (canonical.py)
    if r.get("retired_over"):
        return f"newer than #{r['retired_over']}, which is history now"  # the thing #N described was since declared frozen/retired
    if r.get("via") == "asked" and r.get("q") is not None:
        return f"asked ~{r['q']:.2f}"
    if r.get("via") in ("meaning", "words", "topic") and r.get("cos") is not None:
        return f"~{r['cos']:.2f}" + (" topic" if r["via"] == "topic" else "")
    if r.get("generic"):
        return f"via {r.get('thing')}"
    return ""


def explain(r: dict) -> str:
    """The full reason as a sentence (tools/explain.py)."""
    if not r:
        return "no reason recorded (logged before reasons were kept)"
    via, thing = r.get("via"), r.get("thing")
    cos = f"meaning {r['cos']:.2f} to the input" if r.get("cos") is not None else "no meaning score (embeddings were down)"
    words_ = f"; shared rare words: {', '.join(r['words'])}" if r.get("words") else ""
    weight = f"; usefulness weight {r['weight']}" if "weight" in r else ""
    text = {"keyed": f"a live keyed value of {thing!r}, which the input names",
            "news": f"changed since {thing!r} was last shown, and the input names it again",
            "meaning": f"close in meaning ({cos})",
            "asked": f"the input asks for what it answers (best stored request {r.get('q', 0):.2f}; {cos})",
            "words": f"shares rare words with the input and is nearly close enough in meaning ({cos})",
            "topic": f"{thing!r} is the conversation's current topic" + (" and the input names it" if r.get("named") else "") + f" ({cos})"}.get(via)
    if text is None:
        kind = "a near spelling of" if via == "spelled" else "the name"
        text = (f"the input contains {kind} {thing!r}" + (", a specific thing (few states)" if r.get("specific") else "")
                + (f", a generic name, so it also needed meaning ({cos})" if r.get("generic") else ""))
    return text + words_ + weight


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
            topic: Optional[dict[int, float]] = None, news: frozenset = frozenset(), per_prompt: bool = False) -> Surfaced:
    """The state block for an input (see `select` for what qualifies): at most `max_things` things and `max_states` states, only the newest about any
    one subject, one line each with ONE trust marker and one short evidence pointer, within `budget` characters, saying what it left out. Empty when
    nothing bears on the input."""
    today = today or datetime.now(timezone.utc)
    floor = store.get_param("relevance_floor", DEFAULT_FLOOR) if floor is None else floor
    info: dict = {}
    items, _, dropped = select(store, text, embedder, floor=floor, max_things=max_things, states_each=states_each, exclude_kinds=exclude_kinds,
                               exclude_names=exclude_names, skip_state_ids=skip_state_ids, max_states=max_states, topic=topic, info=info, news=news,
                               today=today, per_prompt=per_prompt)
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
            tag = reason_tag(info.get("reasons", {}).get(s.id, {}))
            chunk.append(f"  #{s.id} {label}{_first_line(s.text, 200)}{rel} [{_freshness(s, today)}{' · ' + ptr if ptr else ''}{' · ' + tag if tag else ''}]")
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
                store.log_outcome(s.id, "surfaced", source="surface", turn_ref=log_turn,
                                  detail=json.dumps({"why": item.why, "status": s.status, "trust": s.trust, "reason": info.get("reasons", {}).get(s.id, {})}))
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
