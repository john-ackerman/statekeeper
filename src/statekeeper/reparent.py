"""Mechanical filing: give things an explicit parent so retrieval's hierarchy (retrieve.select: a named leaf brings its ancestors' states and keeps the
things filed beside it out) has a tree to work with. Name containment alone already makes ancestors (topics.TopicTree), but sibling exclusion only
trusts an explicit parent_id, and most things had none.

Rules, first match wins, most specific names first:
  identifier  "CRM-2971 Prince parity", "#142 backfill", "PR 3376 120-day rule" go under the thing named by their leading identifier.
  pr-ticket   a PR thing ("VirtuousV2 PR 3428", "#2026") whose states (any spelling of that PR's) name exactly one ticket key that is a thing goes
              under that ticket (it may move a PR from under its repo: the ticket is the more specific home).
  flag        a lower-case kebab key with two or more hyphens ("receipting-repair-requires-rendered", "... flag") that a state calls a flag goes under
              "LaunchDarkly flag" (else "LaunchDarkly").
  name        the longest leading run of the name that names a thing ("Receipt Date backfill" under "Receipt Date"); a head noun ("QA agent")
              and a one-word lower-case word that holds nothing ("data") are not parents.
Only things with no parent are filed (bar the PR move above); people, agents and file paths are never parents or children; a move that would make
a loop is skipped. Idempotent: a second run plans nothing.

`place` is the same filing at write time: the one place a writer's thing name becomes a thing ("Parent > child" paths, new things filed by the rules
above, new one-word generic things refused).
"""
from __future__ import annotations

import re
from typing import Callable, Iterable, Optional

from .identifiers import _pr_number, residue
from .store import Store, StoreError, Thing
from .topics import MAX_RUN, MIN_ANCESTOR, SKIP, _key

PEOPLE = ("person", "agent")
_LEAD_ID = re.compile(r"^((?:CRM|VP|VS)-\d+|PR\s*#?\d+|#\d+)(?:\s+|:\s*)\S", re.I)
_FILE = re.compile(r"\.[A-Za-z]{1,4}$")
TOPIC_MIN = 3            # a one-word lower-case thing is a topic (a parent by name) only with at least this many states of its own
FLAG_NEAR = 60           # characters between a key and the word "flag" for a state to call the key a flag
_TICKET = re.compile(r"\b(?:CRM|VP|VS)-\d+\b", re.I)
_FLAG_KEY = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+){2,}$")
_FLAG_IN_TEXT = re.compile(r"(?<![\w-])[a-z0-9]+(?:-[a-z0-9]+){2,}(?![\w-])")
_FLAG_WORD = re.compile(r"(?<![-\w])flags?\b", re.I)
FLAG_PARENTS = ("LaunchDarkly flag", "LaunchDarkly")
PATH_SEP = " > "


def _path(name: str) -> bool:
    return any(ch in name for ch in "/\\")


def _identifier_id(name: str) -> Optional[str]:
    """The leading identifier of a name that is about one ticket or PR ("CRM-2971 sweeper" -> "CRM-2971"); lists and files have none."""
    m = _LEAD_ID.match(name)
    if not m or _FILE.search(name):
        return None
    rest = name[m.end(1):]
    if _TICKET.search(rest) or re.search(r"#\d+|\bPR\s*\d+", rest, re.I):
        return None
    return m.group(1)


def _is_pr(name: str) -> Optional[str]:
    """The PR number of a name that is only a PR ("VirtuousV2 PR 3428", "#2026"), else None."""
    n = _pr_number(name)
    return n if n and not residue(name) else None


def _flag_key(name: str) -> tuple[Optional[str], bool]:
    """(the kebab key a name carries, whether the name itself says flag)."""
    said = name.lower().endswith(" flag")
    key = name[:-5].strip() if said else name
    return (key, said) if _FLAG_KEY.match(key) else (None, False)


def flags_said(texts: Iterable[str]) -> set[str]:
    """Kebab keys a text says within FLAG_NEAR characters of the word "flag"."""
    out: set[str] = set()
    for text in texts:
        flags = [f.start() for f in _FLAG_WORD.finditer(text)]
        if flags:
            out |= {k.group(0) for k in _FLAG_IN_TEXT.finditer(text) if any(abs(f - k.start()) <= FLAG_NEAR for f in flags)}
    return out


def _name_parent(name: str, lookup: Callable[[tuple[str, ...]], Optional[int]], common: Callable[[int], bool], self_id: Optional[int]) -> Optional[int]:
    """The longest leading run of the name that names another thing. "PDF converter egress" is an aspect of the PDF converter, while "QA agent" is
    a kind of agent and "Azure DevOps" a product, not a part of DevOps. A one-word lower-case thing that holds nothing ("data", "tickets",
    "agent") is a common word, not a topic: filing under it would keep unrelated things out as its "siblings"."""
    if _FILE.search(name) or len(_TICKET.findall(name)) + len(re.findall(r"#\d+", name)) >= 2:
        return None
    k = _key(name)
    for size in range(min(MAX_RUN, len(k) - 1), 0, -1):
        run = k[:size]
        if size == 1 and (len(run[0]) < MIN_ANCESTOR or run[0] in SKIP or run[0].isdigit()):
            continue
        pid = lookup(run)
        if pid is None or pid == self_id or (size == 1 and common(pid)):
            continue
        return pid
    return None


def _word_only(name: str, own_states: int) -> bool:
    """A one-word name that is a word, not a topic: all lower case with fewer than TOPIC_MIN states of its own, or not a plain word at all."""
    if not re.fullmatch(r"[A-Za-z][\w.+-]*", name):
        return True
    return bool(re.fullmatch(r"[a-z]+", name)) and own_states < TOPIC_MIN


def plan(store: Store) -> list[dict]:
    """The moves to make: [{"child_id", "child", "parent_id", "parent", "rule", "was"}], most specific names first."""
    things = {t.id: t for t in store.things() if not t.retired}
    ok = {tid: t for tid, t in things.items() if t.kind not in PEOPLE and not _path(t.name)}
    parent_of = {tid: t.parent_id for tid, t in things.items()}
    by_key: dict[tuple[str, ...], int] = {}
    for t in ok.values():
        k = _key(t.name)
        if k and (k not in by_key or t.id < by_key[k]):
            by_key[k] = t.id
    pool = store.searchable_states()
    own_text: dict[int, list[str]] = {}
    for s in pool:
        own_text.setdefault(s.thing_id, []).append(s.text)
    # a PR said several ways ("PR #3376", "PR 3376", "VirtuousV2 PR 3376"): the ticket keys any spelling's own states name count for all of them
    pr_tickets: dict[str, set[str]] = {}
    for t in ok.values():
        n = _is_pr(t.name)
        if n:
            pr_tickets.setdefault(n, set()).update(k.upper() for txt in own_text.get(t.id, ()) for k in _TICKET.findall(txt))
    flag_said: Optional[set[str]] = None
    flag_parent = next((p for p in (store.find(n) for n in FLAG_PARENTS) if p is not None and p.id in ok), None)

    def loops(child: int, parent: int) -> bool:
        seen, p = set(), parent
        while p is not None and p not in seen:
            if p == child:
                return True
            seen.add(p)
            p = parent_of.get(p)
        return False

    def find(name: str) -> Optional[int]:
        t = store.find(name)
        return t.id if t is not None and t.id in ok else None

    def common(pid: int) -> bool:
        return _word_only(things[pid].name, len(own_text.get(pid, ())))

    out: list[dict] = []
    for t in sorted(ok.values(), key=lambda x: (-len(_key(x.name)), -len(x.name), x.id)):
        target, rule = None, None
        lead = _identifier_id(t.name) if t.parent_id is None else None
        if lead:
            pid = find(lead)
            if pid is not None and pid != t.id:
                target, rule = pid, "identifier"
        n = _is_pr(t.name)
        if target is None and n:
            cur = things.get(t.parent_id) if t.parent_id is not None else None
            if cur is None or _key(cur.name) and set(_key(cur.name)) <= set(_key(t.name)):
                keys = pr_tickets.get(n, set())
                if len(keys) == 1:
                    pid = find(next(iter(keys)))
                    if pid is not None and pid != t.id:
                        target, rule = pid, "pr-ticket"
        if target is None and t.parent_id is None and flag_parent is not None and t.id != flag_parent.id:
            key, said = _flag_key(t.name)
            if key and not said and flag_said is None:
                flag_said = flags_said(s.text for s in pool) | {things[s.thing_id].name for s in pool if s.thing_id in things and _FLAG_WORD.search(s.text)}
            if key and (said or key in flag_said):
                target, rule = flag_parent.id, "flag"
        if target is None and t.parent_id is None:
            pid = _name_parent(t.name, lambda run: by_key.get(run), common, t.id)
            if pid is not None:
                target, rule = pid, "name"
        if target is None or target == t.parent_id or loops(t.id, target):
            continue
        out.append({"child_id": t.id, "child": t.name, "parent_id": target, "parent": things[target].name, "rule": rule,
                    "was": things[t.parent_id].name if t.parent_id in things else None})
        parent_of[t.id] = target
    return out


def merge_plan(store: Store) -> list[tuple[str, str]]:
    """(from, into) for a flag said two ways: "<key> flag" beside "<key>" is one flag under two names, and as two things the one not named in a
    prompt never surfaces (its states are a sub-thing's, not an ancestor's)."""
    names = {t.name.lower(): t for t in store.things() if not t.retired and t.kind not in PEOPLE}
    out = []
    for low, t in names.items():
        key, said = _flag_key(t.name)
        if key and said and key.lower() in names and names[key.lower()].id != t.id:
            out.append((t.name, names[key.lower()].name))
    return sorted(out)


def apply(store: Store, moves: list[dict], *, actor: str = "tool:reparent", merges: Iterable[tuple[str, str]] = ()) -> list[dict]:
    """Merges each (from, into) pair (the old name stays as an alias), then makes the planned moves (each one event, 'thing-reparented'); a move
    the store refuses (a loop, or a thing merged away since the plan) is skipped. Returns the moves made."""
    for a, b in merges:
        try:
            store.merge(a, b, "the same flag said with and without 'flag'", actor=actor)
        except StoreError:
            continue
    done = []
    for m in moves:
        try:
            store.set_parent(m["child_id"], m["parent_id"], f"{m['rule']} rule", actor=actor)
            done.append(m)
        except StoreError:
            continue
    return done


def parent_for_new(store: Store, t: Thing, text: Optional[str] = None) -> Optional[tuple[int, str]]:
    """(parent id, rule) for one new thing by the same rules as `plan`, with `text` (what is being said about it) standing in for its states."""
    if t.kind in PEOPLE or _path(t.name):
        return None

    def find(name: str) -> Optional[int]:
        p = store.find(name)
        return p.id if p is not None and p.id != t.id and p.kind not in PEOPLE and not _path(p.name) else None
    lead = _identifier_id(t.name)
    if lead and (pid := find(lead)):
        return pid, "identifier"
    if _is_pr(t.name) and text:
        keys = {k.upper() for k in _TICKET.findall(text)}
        if len(keys) == 1 and (pid := find(next(iter(keys)))):
            return pid, "pr-ticket"
    key, said = _flag_key(t.name)
    if key and (said or (text and key in flags_said([text]))):
        for n in FLAG_PARENTS:
            if (pid := find(n)):
                return pid, "flag"
    things = None

    def lookup(run: tuple[str, ...]) -> Optional[int]:
        nonlocal things
        if things is None:
            things = {}
            for x in store.things():
                if x.kind not in PEOPLE and not _path(x.name) and x.id != t.id:
                    k = _key(x.name)
                    if k and (k not in things or x.id < things[k].id):
                        things[k] = x
        x = things.get(run)
        return x.id if x is not None else None
    pid = _name_parent(t.name, lookup, lambda p: _word_only(store.get(p).name, len(store.states_of(p))), t.id)
    return (pid, "name") if pid is not None else None


def generic_word(name: str) -> bool:
    """A one-word, all lower-case name ("watcher", "rollout", "recall"): too generic to be a new thing on its own."""
    return bool(re.fullmatch(r"[a-z]+", name.strip()))


def place(store: Store, name: str, *, text: Optional[str] = None, actor: str = "store", refuse_generic: bool = True) -> str:
    """The thing a writer's name means, made and filed if new; returns its name.
    "Parent > child" (" > " between names): the parent must exist; a missing child is made under it, an existing child with no parent is filed under
    it (one that already has a parent stays where it is). A new thing without a path is filed by the re-parent rules (`text` is what is being said
    about it, for a PR's ticket or a flag). With `refuse_generic` (the extractor's claims) a new one-word lower-case name is refused: say which
    one ("LaunchDarkly > rollout order", a ticket). A person or agent adding by hand may name a tool that way ("pup")."""
    parts = [p.strip() for p in name.split(PATH_SEP)] if PATH_SEP in name else [name.strip()]
    if any(not p for p in parts):
        raise StoreError(f"'{name}' has an empty name in its path")
    if len(parts) > 1:
        parent = store.find(parts[0])
        if parent is None:
            raise StoreError(f"there is no thing '{parts[0]}' to file '{parts[-1]}' under")
        for seg in parts[1:]:
            t = store.find(seg)
            if t is None:
                if refuse_generic and generic_word(seg):
                    raise StoreError(f"'{seg}' is too generic for a new thing: name what it is about")
                t = store.add_thing(seg, parent=parent.name, kind_said=False, actor=actor)
            elif t.parent_id is None and t.id != parent.id:
                try:
                    t = store.set_parent(t.id, parent.id, "filed by its writer", actor=actor)
                except StoreError:
                    pass
            parent = t
        return parent.name
    t = store.find(parts[0])
    if t is not None:
        return t.name
    if refuse_generic and generic_word(parts[0]):
        raise StoreError(f"'{parts[0]}' is too generic for a new thing: name what it is about, or file it as 'Parent > {parts[0]} ...'")
    t = store.add_thing(parts[0], kind_said=False, actor=actor)
    got = parent_for_new(store, t, text)
    if got is not None:
        try:
            store.set_parent(t.id, got[0], f"{got[1]} rule", actor=actor)
        except StoreError:
            pass
    return t.name
