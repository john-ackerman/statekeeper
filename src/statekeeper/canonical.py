"""Canonical states: the same fact stored under several ids is one fact.

The store holds many facts more than once: the importer extracted "John set a pointing rule ..." from a Persistence fragment, and the decompose job
later rewrote the same fragment into "Parent tickets point the investigation effort ..." under a topic; a fact restated a few days later by another
note is a third copy. At retrieval those twins compete for the same few slots, and which id surfaces is an accident of ids.

`restates(a, b)` says, conservatively and from evidence a person can check, that two standing states say the same thing:
  - they share a source (the same Persistence fragment, message, file or parent state; `legacy:N` and `persistence:N` are one fragment), OR they are
    very close in meaning (cosine >= CROSS_COS) with even more word overlap;
  - their content words overlap heavily (IDF-weighted Jaccard >= JACCARD, and the shorter one's words are mostly in the longer one);
  - neither negates what the other states (a "not"/"never" clause about a word the other says plainly: a contradiction, never a restatement;
    a negated clause about something the other does not mention is extra detail) and they agree in the numbers they carry
    (10% vs 50% is an update, not a restatement);
  - neither is a keyed live value (a PR's CI), which has its own one-current-value rule.

`canonical(members)` picks the one to show: the newest (as-of day), then the most trusted (owner, then re-verified), then the one closest to its
evidence (extracted from the source beats rewritten from another state), then the one carrying most of the cluster's content, then the newest id.

Retrieval (`collapse`) treats each cluster as one: the canonical is shown at the best rank any member earned, the rest are dropped. Nothing is
written. `proposals` lists the same clusters (and likely updates) as review items in the format eval/UPDATER.md describes, so a person or a
later small-model updater can turn them into supersedes. This module never supersedes anything itself.
"""
from __future__ import annotations

import math
import os
import re
from typing import Iterable, Optional

from .store import Evidence, State

ON = os.environ.get("STATEKEEPER_CANONICAL", "1").strip().lower() not in ("0", "off", "false", "no")
SHARED_COS = 0.75      # two states from one source must still be this close in meaning
CROSS_COS = 0.85       # ... with no shared source, this close
JACCARD = 0.50         # IDF-weighted overlap of their content words
CROSS_JACCARD = 0.60   # ... with no shared source
CONTAIN = 0.65         # the shorter one's content words (IDF-weighted) found in the longer one
UPDATE_COS = 0.84      # proposals only: same source or same thing, this close, but numbers or polarity differ -> a likely update
UPDATE_JACCARD = 0.30

_NEG = re.compile(r"\b(not|no|never|cannot|without|none|nothing|nobody)\b|n't\b", re.I)
_NUM = re.compile(r"\d+(?:\.\d+)?")
_LEGACY = re.compile(r"^legacy:(\d+)$")


def source_keys(evidence: Iterable[Evidence]) -> set[str]:
    """Where a state came from, normalised so two states quoting one fragment share a key."""
    out = set()
    for e in evidence:
        ref = (e.ref or "").strip()
        if not ref:
            continue
        m = _LEGACY.match(ref)
        if m:
            out.add(f"persistence:{m.group(1)}")
        elif e.ref_type in ("prototype", "legacy-source") and ref.startswith("persistence:"):
            out.add(ref)
        else:
            out.add(f"{e.ref_type}:{ref}")
    return out


def _derived(evidence: Iterable[Evidence]) -> bool:
    return any(e.ref_type == "state" for e in evidence)


_CLAUSES = re.compile(r"[,;:.()]|\s(?:while|but|whereas|although)\s")


def negated_terms(text: str) -> set[str]:
    """Content words of the clauses that carry a negation ("..., while gh pr edit --add-reviewer does not" -> edit, reviewer)."""
    from .retrieve import terms
    out: set[str] = set()
    for clause in _CLAUSES.split(text):
        if clause and _NEG.search(clause):
            out |= terms(clause)
    return out


def contradicts(a: str, b: str) -> bool:
    """One says no/never/not about something the other states without a negation. A negation in a clause about something the other does not
    mention at all ("..., using local Docker only and never dev, QA or prod") is extra detail, not a contradiction."""
    from .retrieve import terms
    na, nb = negated_terms(a), negated_terms(b)
    ta, tb = terms(a), terms(b)
    return bool((na - nb) & (tb - nb)) or bool((nb - na) & (ta - na))


def numbers(text: str) -> set[str]:
    return set(_NUM.findall(text))


_IDENT = re.compile(r"[#\w][\w#<>]*(?:[-./:_][\w#<>]+)+|\b[a-z]+[A-Z]\w*|\b[A-Z][a-z0-9]+[A-Z]\w*")


_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_PLACEHOLDER = re.compile(r"<[^<>]*>")
_PLAIN_COMPOUND = re.compile(r"^[a-z]+(?:-[a-z]+)+$")     # front-loads, fan-out, sub-issues: a word, not an identifier (admin-qa.crm..., persist-fb-13 are)


def _tokens(text: str) -> set[str]:
    from .retrieve import STOP, split_identifiers, words
    return (set(words(text)) | set(words(split_identifiers(text)))) - STOP


def _foreign_idents(a: str, b: str, b_thing: str = "") -> list[str]:
    """Identifiers in `a` (a URL, a path, a CamelCase class, persist-fb-13) with a part that `b` (or the name of the thing `b` is filed under) never
    says. A date is a number (see `numbers`); a placeholder (<env>, <n>, a lone N) is a generalisation, not a value; a stop word ("out" in fan-out)
    is not a value either."""
    from .retrieve import STOP, split_identifiers, words
    have = _tokens(f"{b} {b_thing}")
    out = []
    for i in _IDENT.findall(a):
        if _DATE.match(i) or _PLAIN_COMPOUND.match(i):
            continue
        parts = {p for p in words(split_identifiers(_PLACEHOLDER.sub(" ", i))) if len(p) > 1 and p not in STOP}
        if parts - have:
            out.append(i)
    return out


def substitutes(a: str, b: str, a_thing: str = "", b_thing: str = "") -> bool:
    """The same sentence about a different value: one names an identifier with a part the other never says, and the other says something of its
    own (admin-qa... vs "the prod Admin app ... admin.crm...", ReceiptProcessorBase vs ExportManager, persist-fb-13 vs persist-fb-<n>). Siblings,
    not one fact. Short words like "qa" and "prod" are not content terms, so word overlap alone cannot see this. A rewrite that only adds an
    identifier to the same words (one side's words all in the other) is not a substitution."""
    fa, fb = _foreign_idents(a, b, b_thing), _foreign_idents(b, a, a_thing)
    if fa and fb:
        return True
    ta, tb = _tokens(f"{a} {a_thing}"), _tokens(f"{b} {b_thing}")
    return bool(fa and (tb - ta)) or bool(fb and (ta - tb))


class Index:
    """What `restates` needs about the pool, built once per retrieval: content words, their document frequency, sources and vectors."""

    def __init__(self, pool: list[State], evidence: dict[int, list[Evidence]], vectors: dict[int, list], df: Optional[dict[str, int]] = None):
        from .retrieve import terms
        self.by_id = {s.id: s for s in pool}
        self.ev = evidence
        self.vec = vectors
        self._terms: dict[int, set[str]] = {}
        self._terms_fn = terms
        if df is None:
            df = {}
            for s in pool:
                for w in self.terms(s.id):
                    df[w] = df.get(w, 0) + 1
        self.df = df
        self.n = max(1, len(pool))
        self.sources: dict[int, set[str]] = {s.id: source_keys(evidence.get(s.id, [])) for s in pool}
        self.by_source: dict[str, list[int]] = {}
        for sid, keys in self.sources.items():
            for k in keys:
                self.by_source.setdefault(k, []).append(sid)
        self.by_thing: dict[int, list[int]] = {}
        for s in pool:
            self.by_thing.setdefault(s.thing_id, []).append(s.id)
        self.named: frozenset = frozenset()                         # things the current input names (retrieval sets it; prefers their copy)

    def terms(self, sid: int) -> set[str]:
        if sid not in self._terms:
            s = self.by_id.get(sid)
            self._terms[sid] = self._terms_fn(s.text) if s else set()
        return self._terms[sid]

    def idf(self, w: str) -> float:
        return math.log((self.n + 1) / (self.df.get(w, 0) + 1))

    def cos(self, a: int, b: int) -> Optional[float]:
        va, vb = self.vec.get(a), self.vec.get(b)
        if va is None or vb is None:
            return None
        from operator import mul
        na = math.sqrt(sum(x * x for x in va)) or 1.0
        nb = math.sqrt(sum(x * x for x in vb)) or 1.0
        return sum(map(mul, va, vb)) / (na * nb)

    def overlap(self, a: int, b: int) -> tuple[float, float]:
        """(IDF-weighted Jaccard, share of the shorter one's weight found in the other)."""
        A, B = self.terms(a), self.terms(b)
        if not A or not B:
            return 0.0, 0.0
        wa, wb = sum(map(self.idf, A)), sum(map(self.idf, B))
        inter = sum(self.idf(w) for w in A & B)
        union = sum(self.idf(w) for w in A | B) or 1.0
        return inter / union, inter / (min(wa, wb) or 1.0)

    def compare(self, a: int, b: int) -> dict:
        """The evidence for (or against) a and b being one fact, as numbers a person can check."""
        sa, sb = self.by_id[a], self.by_id[b]
        shared = sorted(self.sources.get(a, set()) & self.sources.get(b, set()))
        c = self.cos(a, b)
        j, k = self.overlap(a, b)
        na, nb = numbers(sa.text), numbers(sb.text)
        return {"shared_sources": shared, "cosine": round(c, 3) if c is not None else None, "jaccard": round(j, 2), "containment": round(k, 2),
                "contradicts": contradicts(sa.text, sb.text), "numbers_agree": not (na and nb and na != nb),
                "same_thing": sa.thing_id == sb.thing_id}

    def restates(self, a: int, b: int) -> bool:
        sa, sb = self.by_id.get(a), self.by_id.get(b)
        if sa is None or sb is None or a == b or sa.state_key or sb.state_key:
            return False
        c = self.cos(a, b)                                   # cheap tests first: most candidates fail on meaning or word overlap
        if c is None:
            return False
        shared = bool(self.sources.get(a, set()) & self.sources.get(b, set()))
        if c < (SHARED_COS if shared else CROSS_COS):
            return False
        j, k = self.overlap(a, b)
        if j < (JACCARD if shared else CROSS_JACCARD) or k < CONTAIN:
            return False
        na, nb = numbers(sa.text), numbers(sb.text)
        if na and nb and na != nb:
            return False
        return not (contradicts(sa.text, sb.text) or substitutes(sa.text, sb.text, sa.thing_name, sb.thing_name))

    def updates(self, a: int, b: int) -> bool:
        """Proposals only: a and b are about one subject (one source or one thing, close in meaning, overlapping words) but differ in a number or in
        polarity, so the newer likely replaces the older. Never used to hide a state at retrieval."""
        sa, sb = self.by_id.get(a), self.by_id.get(b)
        if sa is None or sb is None or a == b or sa.state_key or sb.state_key:
            return False
        na, nb = numbers(sa.text), numbers(sb.text)
        differs = (na and nb and na != nb) or contradicts(sa.text, sb.text)
        if not differs:
            return False
        if not (self.sources.get(a, set()) & self.sources.get(b, set()) or sa.thing_id == sb.thing_id):
            return False
        c = self.cos(a, b)
        j, _k = self.overlap(a, b)
        return c is not None and c >= UPDATE_COS and j >= UPDATE_JACCARD

    def mates(self, sid: int) -> set[int]:
        """States that restate `sid`: looked for among the states sharing a source with it or filed under its thing (cheap; cross-source twins under
        other things are left to `proposals`, which compares everything)."""
        cand: set[int] = set()
        for k in self.sources.get(sid, set()):
            if len(self.by_source.get(k, [])) <= 60:                 # a key shared by very many states (a session label) is not a source
                cand.update(self.by_source[k])
        s = self.by_id.get(sid)
        if s is not None:
            cand.update(self.by_thing.get(s.thing_id, []))
        cand.discard(sid)
        return {m for m in cand if self.restates(sid, m)}

    def cluster(self, sid: int) -> set[int]:
        """`sid`, its canonical and the states that restate that canonical directly. Not transitive: A~B and B~C does not make A~C (a rule and an
        instance of it can each be close to a third wording and not to each other)."""
        members = {sid} | self.mates(sid)
        if len(members) == 1:
            return members
        c = canonical(members, self)
        return {c, sid} | {m for m in members if m != c and self.restates(c, m)}


def _tier(s: State) -> int:
    return 3 if s.trust == "owner" else (2 if s.confirmed_utc else 1)


def rank_key(s: State, idx: Index, members: Iterable[int]) -> tuple:
    union = set().union(*[idx.terms(m) for m in members]) or {""}
    carried = sum(idx.idf(w) for w in idx.terms(s.id) & union) / (sum(idx.idf(w) for w in union) or 1.0)
    # newest = learned most recently: for two wordings of one fact the as-of date says when the fact held, which a rewrite often drops
    day, at = (s.recorded_utc or "")[:10], (s.recorded_utc or "")[11:16]
    return (s.status == "active", s.thing_id in idx.named, day, at, _tier(s), 0 if _derived(idx.ev.get(s.id, [])) else 1, round(carried, 2), s.id)


def canonical(members: Iterable[int], idx: Index) -> int:
    ms = [m for m in members if m in idx.by_id]
    return max(ms, key=lambda m: rank_key(idx.by_id[m], idx, ms))


def why_canonical(keep: int, others: Iterable[int], idx: Index) -> str:
    k = idx.by_id[keep]
    ka = rank_key(k, idx, [keep, *others])
    reasons = []
    for o in others:
        oa = rank_key(idx.by_id[o], idx, [keep, *others])
        if ka[0] != oa[0]:
            reasons.append(f"accepted, #{o} is only proposed")
        elif ka[1] != oa[1]:
            reasons.append(f"filed under the thing the input names, #{o} is not")
        elif ka[2:4] != oa[2:4]:
            reasons.append(f"newer than #{o} ({' '.join(ka[2:4]).strip()} vs {' '.join(oa[2:4]).strip()})")
        elif ka[4] != oa[4]:
            reasons.append(f"better verified than #{o}")
        elif ka[5] != oa[5]:
            reasons.append(f"closer to the source than #{o} (#{o} was rewritten from another state)")
        elif ka[6] != oa[6]:
            reasons.append(f"says more than #{o} ({ka[6]:.2f} vs {oa[6]:.2f} of the cluster's content)")
        else:
            reasons.append(f"newer id than #{o}")
    return "; ".join(reasons)


def collapse(qualifying: list[State], idx: Index, *, eligible) -> tuple[dict[int, int], set[int], dict[int, tuple]]:
    """For the qualifying states of one retrieval: {member id -> canonical id} for each member whose cluster's canonical is another state, the
    member ids to drop (their cluster is already represented, or its canonical cannot be shown here, e.g. it sits in the session core), and for
    each shown canonical the recency of its newest copy (a fact is as recent as its newest restatement, for the newer-wins passes that follow).
    `eligible(state) -> 'ok' | 'core' | 'no'` says whether a canonical may be shown ('core': already in front of the model, so drop the cluster)."""
    sub: dict[int, int] = {}
    drop: set[int] = set()
    done: dict[int, int] = {}            # member -> its cluster's canonical (or -1: dropped)
    for s in qualifying:
        if s.state_key or s.id in done or s.id not in idx.by_id:
            continue
        members = idx.cluster(s.id)
        if len(members) == 1:
            done[s.id] = s.id
            continue
        order = sorted(members, key=lambda m: rank_key(idx.by_id[m], idx, members), reverse=True)
        keep = None
        for m in order:
            e = eligible(idx.by_id[m])
            if e == "core":
                keep = -1
                break
            if e == "ok":
                keep = m
                break
        for m in members:
            done[m] = keep if keep is not None else -1
    seen_keep: set[int] = set()
    recency: dict[int, tuple] = {}
    for m, k in done.items():
        if k != -1 and m in idx.by_id:
            recency[k] = max(recency.get(k, idx.by_id[k].recency), idx.by_id[m].recency)
    for s in qualifying:                 # in rank order: the first member of a cluster carries its canonical, the rest drop
        k = done.get(s.id, s.id)
        if k == s.id and s.id not in seen_keep:
            seen_keep.add(s.id)
            continue
        if k == -1 or k in seen_keep:
            drop.add(s.id)
            continue
        sub[s.id] = k
        seen_keep.add(k)
    return sub, drop, recency


_RETIRED = r"^(?:the\s+)?{name}\s+(?:is|was|has been|are|were|have been)\s+(?:now\s+)?(?:frozen|retired|decommissioned|deprecated|archived|obsolete|" \
           r"kept only as history|no longer (?:used|in use|maintained)|replaced by|superseded by)\b"
_RETIRED_WORD = re.compile(r"\b(frozen|retired|decommissioned|deprecated|archived|obsolete|history|no longer|replaced by|superseded by)\b", re.I)
_HISTORY_ASK = re.compile(r"\b(history|historical|used to|back then|originally|old|older|previous|previously|before the)\b", re.I)


def retired_things(pool: list[State]) -> dict[int, State]:
    """Things whose own standing state says the thing itself is frozen, retired, replaced or kept only as history ("The Persistence store is frozen
    and kept only as history."): thing id -> that declaration. Only a sentence whose subject IS the thing counts ("The standalone-service plan is
    superseded", filed under "receipting", retires the plan, not receipting)."""
    out: dict[int, State] = {}
    for s in pool:
        if s.state_key or s.status != "active" or not _RETIRED_WORD.search(s.text):
            continue
        if re.search(_RETIRED.format(name=re.escape(s.thing_name)), s.text.strip(), re.I):
            cur = out.get(s.thing_id)
            if cur is None or s.recency > cur.recency:
                out[s.thing_id] = s
    return out


def overtaken_by_retirement(states: list[State], retired: dict[int, State], text: str) -> dict[int, State]:
    """Of these qualifying states, the ones about a thing declared retired after they were recorded: state id -> the declaration to show instead.
    A rollback recipe for a store that has since been frozen is history, not the current state of that store. Not when the input asks about history."""
    if not retired or _HISTORY_ASK.search(text):
        return {}
    out: dict[int, State] = {}
    for s in states:
        d = retired.get(s.thing_id)
        if d is None or s.state_key or s.id == d.id:
            continue
        if (s.as_of_utc or s.recorded_utc or "")[:19] < (d.as_of_utc or d.recorded_utc or "")[:19]:
            out[s.id] = d
    return out


def proposals(store, *, limit: int = 200, thing: Optional[str] = None, model: Optional[str] = None) -> list[dict]:
    """Every cluster of restatements in the store (and likely updates), as review items. Compares all standing unkeyed states pairwise with numpy
    when available (seconds), else within shared sources and things only."""
    pool = [s for s in store.searchable_states() if not s.state_key]
    ev = store.evidence_of([s.id for s in pool])
    if model is None:
        row = store.db.execute("SELECT model FROM embeddings WHERE owner_type = 'state' GROUP BY model ORDER BY COUNT(*) DESC LIMIT 1").fetchone()
        model = row[0] if row else ""
    vectors = {v["owner_id"]: v["vector"] for v in store.vectors("state", model)}
    idx = Index(pool, ev, vectors)
    focus = None
    if thing:
        t = store.find(thing)
        focus = {s.id for s in pool if t and (s.thing_id == t.id or s.object_id == t.id)}
    pairs: set[tuple[int, int]] = set()
    try:
        import numpy as np
        ids = [s.id for s in pool if s.id in vectors]
        m = np.array([vectors[i] for i in ids], dtype=np.float32)
        m /= np.linalg.norm(m, axis=1, keepdims=True) + 1e-9
        sims = m @ m.T
        hi = np.argwhere(np.triu(sims, 1) >= min(SHARED_COS, UPDATE_COS))
        for x, y in hi:
            pairs.add((ids[x], ids[y]))
    except Exception:
        for s in pool:
            for k in idx.sources.get(s.id, set()):
                for o in idx.by_source.get(k, []):
                    if o > s.id:
                        pairs.add((s.id, o))
            for o in idx.by_thing.get(s.thing_id, []):
                if o > s.id:
                    pairs.add((s.id, o))
    if focus is not None:
        pairs = {p for p in pairs if p[0] in focus or p[1] in focus}
    mates: dict[int, set[int]] = {}
    updates: list[tuple[int, int]] = []
    for a, b in sorted(pairs):
        if idx.restates(a, b):
            mates.setdefault(a, set()).add(b)
            mates.setdefault(b, set()).add(a)
        elif idx.updates(a, b):
            updates.append((a, b))
    # star clusters, as at retrieval: a canonical and the states that restate IT (not a chain); biggest stars first, each state in one proposal
    out: list[dict] = []
    taken: set[int] = set()
    for sid in sorted(mates, key=lambda x: (-len(mates[x]), x)):
        if sid in taken:
            continue
        group = ({sid} | mates[sid]) - taken
        if len(group) < 2:
            continue
        keep = canonical(group, idx)
        members = {keep} | {m for m in group if m != keep and idx.restates(keep, m)}
        if len(members) < 2:
            continue
        taken |= members
        others = sorted(members - {keep})
        out.append({"op": "supersede", "relation": "restates", "keep": keep, "retire": others,
                    "evidence": {str(o): idx.compare(keep, o) for o in others},
                    "why_keep": why_canonical(keep, others, idx), "confidence": "high" if all(idx.compare(keep, o)["shared_sources"] and (idx.cos(keep, o) or 0) >= CROSS_COS for o in others) else "medium",
                    "texts": {str(m): f"[{idx.by_id[m].thing_name}] {idx.by_id[m].text[:200]}" for m in [keep, *others]},
                    "proposer": "rule:canonical"})
    for a, b in updates:
        sa, sb = idx.by_id[a], idx.by_id[b]
        new, old = (a, b) if (sa.as_of_utc or sa.recorded_utc)[:10] > (sb.as_of_utc or sb.recorded_utc)[:10] else (b, a)
        if (idx.by_id[new].as_of_utc or idx.by_id[new].recorded_utc)[:10] == (idx.by_id[old].as_of_utc or idx.by_id[old].recorded_utc)[:10]:
            continue                                     # same day: no way to tell which is the update from dates alone; leave it to a reader
        out.append({"op": "supersede", "relation": "updates", "keep": new, "retire": [old], "evidence": {str(old): idx.compare(new, old)},
                    "why_keep": f"newer ({(idx.by_id[new].as_of_utc or idx.by_id[new].recorded_utc)[:10]}) and differs in a number or in polarity",
                    "confidence": "low", "texts": {str(m): f"[{idx.by_id[m].thing_name}] {idx.by_id[m].text[:200]}" for m in (new, old)},
                    "proposer": "rule:canonical"})
    order = {"high": 0, "medium": 1, "low": 2}
    out.sort(key=lambda p: (order[p["confidence"]], -len(p["retire"]), p["keep"]))
    return out[:limit]
