"""The topic tree over the existing 'thing' layer: a thing sits under another when the store says so (parent_id) or when its name contains the other's
whole name ("receipting security review" under "receipting", "Receipt Date backfill" under "Receipt Date" under "Receipt"). Nothing is written: the
tree is derived on read, so a renamed or merged thing moves with its name.

Used three ways: naming a topic lets its sub-areas' states qualify the way a named generic thing's own states do (retrieve.select); `sk topic <name>`
lists what is current under a topic; and `topic_index` is the short always-loaded list of what exists.
"""
from __future__ import annotations

import re
from collections import defaultdict
from typing import Optional

_W = re.compile(r"[^\W_]+", re.UNICODE)
MAX_RUN = 4               # an ancestor's name is at most this many words
MIN_ANCESTOR = 4          # a one-word ancestor needs at least this many letters ("pr", "qa" are too short to be a topic by name)
SKIP = frozenset({"the", "a", "an", "and", "of", "for", "to", "in", "on", "with"})


def _key(name: str) -> tuple[str, ...]:
    return tuple(w.lower() for w in _W.findall(name))


class TopicTree:
    def __init__(self, things: list):
        self.things = {t.id: t for t in things if not t.retired}
        by_key: dict[tuple[str, ...], int] = {}
        for t in self.things.values():
            k = _key(t.name)
            if k and (k not in by_key or t.id < by_key[k]):
                by_key[k] = t.id
        self.parents: dict[int, set[int]] = defaultdict(set)
        for t in self.things.values():
            if t.parent_id is not None and t.parent_id in self.things:
                self.parents[t.id].add(t.parent_id)
            k = _key(t.name)
            if any(ch in t.name for ch in "/\\") or len(k) < 2:
                continue                                       # a file path or a one-word name has no name-derived parent
            for size in range(1, min(MAX_RUN, len(k) - 1) + 1):
                for i in range(len(k) - size + 1):
                    run = k[i:i + size]
                    if size == 1 and (len(run[0]) < MIN_ANCESTOR or run[0] in SKIP or run[0].isdigit()):
                        continue
                    pid = by_key.get(run)
                    if pid is not None and pid != t.id:
                        self.parents[t.id].add(pid)
        self.children: dict[int, set[int]] = defaultdict(set)
        for c, ps in self.parents.items():
            for p in ps:
                self.children[p].add(c)

    def descendants(self, tid: int, limit: int = 2000) -> set[int]:
        out: set[int] = set()
        todo = [tid]
        while todo and len(out) < limit:
            for c in self.children.get(todo.pop(), ()):
                if c not in out and c != tid:
                    out.add(c)
                    todo.append(c)
        return out


def tree(store) -> TopicTree:
    return TopicTree(store.things())


def topic_index(store, *, top: int = 25, min_states: int = 8, budget: int = 900, tt: Optional[TopicTree] = None) -> str:
    """One short block naming the biggest topics and how much is stored under each, so the model can see what exists and ask for it
    (`sk topic <name>`). Topics are things with sub-areas; people, agents, repos' run records and files are left out."""
    tt = tt or tree(store)
    count: dict[int, int] = defaultdict(int)
    for s in store.searchable_states():
        count[s.thing_id] += 1
    rows = []
    for tid, t in tt.things.items():
        if t.kind != "thing" or any(ch in t.name for ch in "/\\#") or re.search(r"\d{3,}", t.name):
            continue
        sub = tt.descendants(tid)
        if len(sub) < 2 or sum(1 for c in sub if re.search(r"\d{3,}|[0-9a-f]{12,}", tt.things[c].name)) * 2 > len(sub):
            continue                                           # mostly run records (builds, agent ids, PR numbers): a log, not a topic
        n = count.get(tid, 0) + sum(count.get(c, 0) for c in sub)
        if n >= min_states:
            rows.append((n, len(sub), t.name))
    rows.sort(reverse=True)
    if not rows:
        return ""
    head = "Topics in the store (states under each; `sk topic <name>` lists what is current there): "
    parts: list[str] = []
    for n, _subs, name in rows[:top]:
        part = f"{name} {n}"
        if len(head) + sum(len(p) + 2 for p in parts) + len(part) > budget:
            break
        parts.append(part)
    return head + ", ".join(parts)


def topic_states(store, name: str, *, limit: int = 30, tt: Optional[TopicTree] = None) -> tuple[Optional[object], list]:
    """(the topic thing, the standing states under it, newest first): its own and its sub-areas', keyed current values included."""
    t = store.find(name)
    if t is None:
        low = name.strip().lower()
        t = next((x for x in store.things() if x.name.lower() == low and not x.retired), None)
    if t is None:
        return None, []
    tt = tt or tree(store)
    ids = {t.id} | tt.descendants(t.id)
    pool = store.searchable_states()
    states = [s for s in pool if s.thing_id in ids]
    # notes overtaken by a later merge/close of the PRs they are about, and older notes of one PR beside its newest, stay out (as in recall)
    from .identifiers import IdentifierIndex, entity_replaced
    gone = entity_replaced(store, IdentifierIndex(store.labels()), states, pool)
    return t, sorted((s for s in states if s.id not in gone), key=lambda s: s.recency, reverse=True)[:limit]
