"""Haiku reader: the next input (+ the last few turns) -> one Haiku call that picks things from the thing tree -> a deterministic fetch of those
things' current states plus their ancestors' (never siblings, never history), in the inject hook's block format and budget.

    python experiments/haiku_interface/reader.py --db <copy> "what did we decide about the embedder"
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1] / "src"))
sys.path.insert(0, str(HERE))

from statekeeper.retrieve import Item, Surfaced, _first_line, _freshness, _pointer, terms  # noqa: E402

import haiku  # noqa: E402
from tree import HIDE_WRITERS, ThingTree  # noqa: E402

BUDGET = 1800           # shadow_hook.LIVE_BUDGET
MAX_THINGS = 3          # shadow_hook.LIVE_MAX_THINGS
MAX_STATES = 4          # shadow_hook.LIVE_MAX_STATES (picked things; a thing's keyed values count as one line, as in the hook)
STATES_EACH = 2         # surface()'s states_each default, which the hook uses
ANCESTOR_STATES = 2    # at most this many ancestor states in all, only from small ancestors
SMALL = 6               # retrieve.SPECIFIC_MAX: an ancestor with more standing states than this is a container, not context
RECENT = 40             # things changed most recently, listed for "earlier today" style inputs
EXCLUDE_THINGS = ("working note",)

SYSTEM = ("You route a person's message to the entries of a memory store. The store is a tree of named THINGS; each holds the current facts about "
          "itself. You never see the facts, only the names. Answer with JSON only.")

PROMPT = """Pick the things in the store whose current facts the assistant needs to answer or act on the NEW INPUT well.

Rules:
- Use the recent turns to resolve vague references ("that", "the thing we discussed", "earlier today", "he"). When the input points back at
  recent conversation ("earlier", "today", "we talked about", "what did we decide"), prefer a thing from RECENTLY UPDATED that fits.
- Pick the MOST SPECIFIC thing (a PR, ticket, flag, file or decision topic) rather than its parent; its parents are fetched automatically.
- At most {max_things} things, best first. Copy each name exactly as written in the tree (the last part of the path is enough).
- If the input is small talk, an instruction that needs no stored facts, or nothing in the tree bears on it, return no things.
- "terms": up to 4 short words or phrases that should appear in the wanted facts (used to rank facts inside a large thing).

THING TREE (indented under its parent; (n) = current facts it holds):
{tree}

RECENTLY UPDATED THINGS (newest first, with date):
{recent}

RECENT TURNS (oldest first):
{turns}

NEW INPUT:
{prompt}

Answer: {{"things": ["name", ...], "terms": ["...", ...]}}"""

SCHEMA = {"type": "object", "properties": {"things": {"type": "array", "items": {"type": "string"}, "maxItems": 5},
                                           "terms": {"type": "array", "items": {"type": "string"}, "maxItems": 6}},
          "required": ["things", "terms"], "additionalProperties": False}


@dataclass
class Read:
    block: str
    items: list
    picked: list[str]
    resolved: list[str]
    unresolved: list[str]
    terms: list[str]
    seconds: float
    input_tokens: int
    output_tokens: int
    cost_usd: float
    error: str = ""
    ancestors: list[str] = field(default_factory=list)


class Reader:
    def __init__(self, store, call=haiku.call):
        self.store, self.call = store, call
        self.tree = ThingTree(store)
        self.tree_text = self.tree.render()
        self.recent_text = self._recent()

    def _recent(self) -> str:
        newest: dict[int, str] = {}
        for s in self.store.searchable_states():
            if s.writer.startswith(HIDE_WRITERS) or s.thing_id not in self.tree.keep or s.thing_name in EXCLUDE_THINGS:
                continue
            if s.writer.startswith(("rule:github-pr", "rule:agent-status", "observer:", "rule:ado-build")):
                continue                                   # live probes' own records change every few minutes; they would fill the list
            when = (s.recorded_utc or "")[:16]
            if when > newest.get(s.thing_id, ""):
                newest[s.thing_id] = when
        rows = sorted(newest.items(), key=lambda kv: kv[1], reverse=True)[:RECENT]
        return "\n".join(f"{self.tree.path(t)} ({w})" for t, w in rows)

    def prompt(self, text: str, turns: list[dict]) -> str:
        tt = "\n".join(f"[{t.get('role', 'user')}] {_first_line(t.get('text', ''), 600)}" for t in turns[-4:]) or "(none)"
        return PROMPT.format(max_things=MAX_THINGS, tree=self.tree_text, recent=self.recent_text, turns=tt, prompt=text[:4000])

    def standing(self, tid: int) -> list:
        return [s for s in self.store.states_of(tid) if s.thing_id == tid and not s.writer.startswith(HIDE_WRITERS)]

    def rank(self, states: list, words: set[str]) -> list:
        def key(s):
            overlap = len(terms(s.text) & words)
            rule = 1 if (s.kind == "rule" or s.volatility in ("rule", "stable")) else 0
            return (1 if s.state_key else 0, overlap, rule, s.recency)
        return sorted(states, key=key, reverse=True)

    def fetch(self, tids: list[int], words: set[str], today: datetime) -> tuple[str, list]:
        items: list[Item] = []
        lines_used = 0
        seen_states: set[int] = set()
        for tid in tids[:MAX_THINGS]:
            sts = self.rank(self.standing(tid), words)
            keyed = [s for s in sts if s.state_key]
            plain = [s for s in sts if not s.state_key]
            room = min(STATES_EACH, MAX_STATES - lines_used - (1 if keyed else 0))
            pick = keyed + plain[:max(0, room)]
            if not pick:
                continue
            lines_used += (1 if keyed else 0) + len(plain[:max(0, room)])
            items.append(Item(self.store.get(tid), "picked", 1.0, pick))
            seen_states |= {s.id for s in pick}
        anc_left = ANCESTOR_STATES
        ancestors: list[Item] = []
        done = set(tids)
        for it in list(items):
            for a in self.tree.ancestors(it.thing.id):
                if a in done or anc_left <= 0:
                    continue
                done.add(a)
                sts = [s for s in self.standing(a) if s.id not in seen_states]
                if not sts or len(sts) > SMALL:
                    continue                                       # a big container's states are not context for one leaf
                pick = self.rank([s for s in sts if not s.state_key], words)[:anc_left]
                if pick:
                    anc_left -= len(pick)
                    ancestors.append(Item(self.store.get(a), f"ancestor of {it.thing.name}", 0.9, pick))
        items += ancestors
        if not items:
            return "", []
        lines = ["Current state bearing on this input (#id = state; [how sure · evidence]; raw history is not shown):"]
        shown = []
        for item in items:
            ev = self.store.evidence_of([s.id for s in item.states])
            chunk = [f"{item.thing.name} ({item.thing.kind}; {item.why}):"]
            keyed = [s for s in item.states if s.state_key]
            if keyed:
                oldest = min(keyed, key=lambda s: s.confirmed_utc or s.recorded_utc)
                mark = _freshness(oldest, today).split("; re-check")[0]
                chunk.append("  " + " | ".join(f"#{s.id} {s.state_key.split('.')[-1]}: {_first_line(s.text, 90)}" for s in keyed) + f" [{mark}]")
            for s in item.states:
                if s.state_key:
                    continue
                rel = f" -> {s.object_name}" if s.object_name else ""
                ptr = _pointer(ev.get(s.id, []))
                chunk.append(f"  #{s.id} {_first_line(s.text, 200)}{rel} [{_freshness(s, today)}{' · ' + ptr if ptr else ''}]")
            block = "\n".join(chunk)
            if sum(len(x) + 1 for x in lines) + len(block) > BUDGET:
                continue
            lines.append(block)
            shown.append(item)
        return ("\n".join(lines) if shown else ""), shown

    def pick(self, text: str, turns: list[dict] | None = None):
        """The model step alone (no store access, so it can run in a worker thread): (got, call) or (None, error)."""
        try:
            c = self.call(self.prompt(text, turns or []), SYSTEM, schema=SCHEMA, extra=["--effort", "low"])
            return haiku.parse_json(c.text), c
        except Exception as e:
            return None, f"{type(e).__name__}: {str(e)[:200]}"

    def read(self, text: str, turns: list[dict] | None = None, today: datetime | None = None, picked=None) -> Read:
        today = today or datetime.now(timezone.utc)
        got, c = picked if picked is not None else self.pick(text, turns)
        if got is None:
            return Read("", [], [], [], [], [], 0.0, 0, 0, 0.0, error=str(c))
        picked = [str(x) for x in (got.get("things") or []) if str(x).strip()]
        words = set()
        for t in (got.get("terms") or []):
            words |= terms(str(t)) | {w.lower() for w in re.findall(r"[\w\-]+", str(t)) if len(w) >= 4}
        tids, resolved, unresolved = [], [], []
        for p in picked:
            tid = self.tree.resolve(p)
            if tid is None or self.store.get(tid).name in EXCLUDE_THINGS:
                unresolved.append(p)
            elif tid not in tids:
                tids.append(tid)
                resolved.append(self.tree.path(tid))
        block, items = self.fetch(tids, words, today)
        return Read(block, items, picked, resolved, unresolved, list(got.get("terms") or []), round(c.seconds, 2),
                    c.input_tokens + c.cache_read + c.cache_write, c.output_tokens, c.cost_usd,
                    ancestors=[i.thing.name for i in items if i.why.startswith("ancestor")])


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("prompt")
    args = ap.parse_args()
    os.environ["STATEKEEPER_DB"] = args.db
    from statekeeper import SqliteStore
    r = Reader(SqliteStore(args.db)).read(args.prompt)
    print(json.dumps({k: v for k, v in r.__dict__.items() if k not in ("items", "block")}, indent=1))
    print(r.block)
    return 0


if __name__ == "__main__":
    sys.exit(main())
