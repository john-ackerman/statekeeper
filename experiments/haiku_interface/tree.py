"""The thing tree the Haiku writer and reader both see: one line per thing that holds standing states (or sits above one), indented under its
primary parent, with the count of its standing states. Same vocabulary on both sides: the writer files under these names, the reader picks them."""
from __future__ import annotations

import re
from collections import defaultdict

from statekeeper.topics import TopicTree

HIDE_WRITERS = ("import:conversation",)   # the bulk-imported unreviewed tier: retrieval shows it only by identifier, so it stays out here too
OPAQUE = re.compile(r"^\W*((PR|VirtuousV2 PR|VirtuousWorkspace PR|SQL-Scripts PR)\s*#?\d+|[A-Z][A-Z0-9]+-\d+|#?\d{3,})\W*$", re.I)
GIST = 80


def primary_parents(store, tt: TopicTree) -> dict[int, int]:
    """One parent per thing: the explicit parent_id, else the longest-named name-derived ancestor (the most specific)."""
    out = {}
    for tid, t in tt.things.items():
        if t.parent_id is not None and t.parent_id in tt.things:
            out[tid] = t.parent_id
        elif tt.parents.get(tid):
            out[tid] = max(tt.parents[tid], key=lambda p: (len(tt.things[p].name), -p))
    # break any cycle from name containment
    for tid in list(out):
        seen, cur = {tid}, out.get(tid)
        while cur is not None:
            if cur in seen:
                out.pop(tid, None)
                break
            seen.add(cur)
            cur = out.get(cur)
    return out


class ThingTree:
    def __init__(self, store):
        self.store = store
        self.tt = TopicTree(store.things())
        self.parent = primary_parents(store, self.tt)
        self.count: dict[int, int] = defaultdict(int)
        for s in store.searchable_states():
            if not s.writer.startswith(HIDE_WRITERS):
                self.count[s.thing_id] += 1
        keep = set(t for t in self.count if t in self.tt.things)
        for t in list(keep):
            cur = self.parent.get(t)
            while cur is not None and cur not in keep:
                keep.add(cur)
                cur = self.parent.get(cur)
        self.keep = keep
        self.children: dict[int | None, list[int]] = defaultdict(list)
        for t in keep:
            p = self.parent.get(t)
            self.children[p if p in keep else None].append(t)
        for v in self.children.values():
            v.sort(key=lambda i: self.tt.things[i].name.lower())
        self.by_name = {self.tt.things[t].name.lower(): t for t in keep}
        # a name that is only an identifier ("CRM-1399", "PR 3266", "#3340") says nothing to a reader choosing by name: give it a short gist
        # (the newest standing non-keyed state's opening words), so the same vocabulary also carries what the number is about
        self.gist: dict[int, str] = {}
        newest: dict[int, object] = {}
        for s in store.searchable_states():
            if s.writer.startswith(HIDE_WRITERS) or s.state_key or s.thing_id not in keep or not OPAQUE.match(self.tt.things[s.thing_id].name):
                continue
            if s.thing_id not in newest or s.recency > newest[s.thing_id].recency:
                newest[s.thing_id] = s
        for tid, s in newest.items():
            text = " ".join(s.text.split())
            self.gist[tid] = text if len(text) <= GIST else text[:GIST - 1] + "…"

    def name(self, tid: int) -> str:
        return self.tt.things[tid].name

    def path(self, tid: int) -> str:
        parts, cur, seen = [], tid, set()
        while cur is not None and cur not in seen:
            seen.add(cur)
            parts.append(self.name(cur))
            cur = self.parent.get(cur)
        return " > ".join(reversed(parts))

    def ancestors(self, tid: int) -> list[int]:
        """Primary-parent chain, nearest first (what the reader fetches beside a picked thing)."""
        out, cur, seen = [], self.parent.get(tid), {tid}
        while cur is not None and cur not in seen:
            seen.add(cur)
            out.append(cur)
            cur = self.parent.get(cur)
        return out

    def resolve(self, name: str):
        """A name or 'A > B > C' path the model wrote back -> thing id (last segment, exact, case-insensitive; then the store's own find)."""
        name = (name or "").strip()
        if not name:
            return None
        name = re.sub(r"\s+\(\d+\)(\s+-\s+.*)?$", "", name)          # the model sometimes copies the "(n)" count or the gist with the name
        last = name.split(">")[-1].strip()
        tid = self.by_name.get(last.lower()) or self.by_name.get(name.lower())
        if tid is not None:
            return tid
        t = self.store.find(last) or self.store.find(name)
        return t.id if t and t.id in self.tt.things else None

    def render(self, *, collapse_runs: int = 0) -> str:
        """Indented tree. `collapse_runs` > 0 folds a parent's numbered run-record children (builds, agent ids) beyond that many into one line."""
        lines: list[str] = []

        def walk(tid: int, depth: int) -> None:
            n = self.count.get(tid, 0)
            g = self.gist.get(tid)
            lines.append("  " * depth + self.name(tid) + (f" ({n})" if n else "") + (f" - {g}" if g else ""))
            kids = self.children.get(tid, [])
            if collapse_runs:
                runs = [k for k in kids if re.search(r"\d{4,}|[0-9a-f]{10,}", self.name(k)) and not self.children.get(k)]
                if len(runs) > collapse_runs:
                    shown = set(sorted(runs, key=lambda k: -k)[:collapse_runs])
                    kids = [k for k in kids if k not in runs or k in shown]
                    lines.append("  " * (depth + 1) + f"... {len(runs) - collapse_runs} more numbered records")
            for k in kids:
                walk(k, depth + 1)
        for root in self.children.get(None, []):
            walk(root, 0)
        return "\n".join(lines)
