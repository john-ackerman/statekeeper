"""Haiku writer: one turn (user message + assistant reply, or a transcript slice) -> one Haiku call that sees the fragment, the thing tree and the
current states of the likely things -> add / confirm / supersede, applied directly (no review gate), every proposal and change logged to a ledger.

Deterministic checks (eval/UPDATER.md): a confirm/supersede id must be in the candidate list; every quote must appear in the fragment (whitespace and
case folded); a new thing must be "Parent > thing" under a known parent (or an existing name); the agent's own status lines are dropped.
Reversible: an add is a new proposed state (`undo` rejects it); a confirm only moves confirmed_utc (the old value is in the ledger); a supersede keeps
the old state as history (`undo` reopens it and rejects the new one).

    python experiments/haiku_interface/writer.py --db <COPY> --ledger out/haiku-exp/ledger.jsonl --undo <run>
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1] / "src"))
sys.path.insert(0, str(HERE))

from statekeeper.claims import ClaimError, submit_claim  # noqa: E402
from statekeeper.extract import is_status_line  # noqa: E402
from statekeeper.store import Evidence, StoreError  # noqa: E402

import haiku  # noqa: E402
from tree import HIDE_WRITERS, ThingTree  # noqa: E402

MAX_CANDIDATE_THINGS = 10
STATES_PER_CANDIDATE = 6
MAX_FACTS = 8

SYSTEM = ("You maintain a memory store that holds the CURRENT picture of each named thing (not a log of events). You read one conversation turn "
          "and say which things' current facts it changes. Answer with JSON only.")

PROMPT = """Read the TURN and update the store's current picture.

For each lasting fact the turn states, choose ONE operation:
- "confirm": a CURRENT STATE below already says the same thing. Give its state_id.
- "supersede": the turn gives a newer value for, corrects, or overrides a CURRENT STATE below (same subject, different value). Give its state_id and the new text that replaces it.
- "add": a new fact. Give the thing it is about.

Rules:
- Split the turn into facts by the thing each affects: "John said do X from now on" updates his ways of working; "John asked about ticket Y" updates Y.
- thing: the MOST SPECIFIC existing thing in the tree (copy its name exactly), or "Parent > new thing" to make a new one under an existing parent. Never a new top-level thing, never a bare generic word.
- text: one plain sentence that will stay true as the current state, standing alone (name the subject; no "this", "it"). Keep the turn's hedges (planned, proposed, may). Dates as YYYY-MM-DD when the turn gives them.
- quote: the exact words from the TURN (copied verbatim, 3-25 words) that justify the fact.
- Leave out: the assistant's own progress or status lines (running, waiting, started, will report), one-off steps, chit-chat, and anything not stated in the turn. Use only state_ids listed below.
- kind: "rule" (how something should always be done), "commitment" (someone will do something), else "belief".
- volatility: "rule" or "stable" for lasting decisions and facts, "work" for current status of work, "live" never.
- At most {max_facts} facts; none if the turn states nothing lasting.

THING TREE (indented under its parent; (n) = current facts it holds):
{tree}

CURRENT STATES of the likely things (#id [thing] text):
{current}

TURN ({when}):
{turn}

Answer: {{"facts": [{{"op": "add|confirm|supersede", "thing": "...", "state_id": null, "text": "...", "quote": "...", "kind": "belief", "volatility": "work"}}]}}"""

SCHEMA = {"type": "object", "properties": {"facts": {"type": "array", "maxItems": 12, "items": {"type": "object", "properties": {
    "op": {"type": "string", "enum": ["add", "confirm", "supersede"]}, "thing": {"type": "string"}, "state_id": {"type": ["integer", "null"]},
    "text": {"type": "string"}, "quote": {"type": "string"}, "kind": {"type": "string", "enum": ["belief", "rule", "commitment"]},
    "volatility": {"type": "string", "enum": ["work", "stable", "rule", "live"]}},
    "required": ["op", "thing", "state_id", "text", "quote", "kind", "volatility"], "additionalProperties": False}}},
    "required": ["facts"], "additionalProperties": False}


def norm(s: str) -> str:
    return " ".join(re.sub(r"[‘’]", "'", re.sub(r"[“”]", '"', s or "")).lower().split())


class Writer:
    def __init__(self, store, ledger: Path, embedder=None, call=haiku.call):
        self.store, self.ledger, self.embedder, self.call = store, ledger, embedder, call
        self.refresh()

    def refresh(self) -> None:
        self.tree = ThingTree(self.store)
        self.tree_text = self.tree.render()

    def candidates(self, turn: str) -> list[int]:
        """Likely things, cheaply and without the model: names the turn says (longest first), then what recall surfaces for it."""
        low = turn.lower()
        named = sorted((t for t in self.tree.keep if len(self.tree.name(t)) > 3 and re.search(r"(?<![\w-])" + re.escape(self.tree.name(t).lower()) + r"(?![\w-])", low)),
                       key=lambda t: -len(self.tree.name(t)))
        out = list(dict.fromkeys(named))[:MAX_CANDIDATE_THINGS]
        try:
            from statekeeper import surface
            got = surface(self.store, turn[:4000], self.embedder, floor=0.72, max_things=6, max_states=12, budget=20000, exclude_names=("working note",))
            for it in got.items:
                if it.thing.id not in out and len(out) < MAX_CANDIDATE_THINGS:
                    out.append(it.thing.id)
        except Exception:
            pass
        return [t for t in out if self.tree.name(t) != "working note"]

    def current(self, tids: list[int]) -> tuple[str, dict[int, object]]:
        rows, ids = [], {}
        for tid in tids:
            sts = [s for s in self.store.states_of(tid) if s.thing_id == tid and not s.writer.startswith(HIDE_WRITERS)][:STATES_PER_CANDIDATE]
            for s in sts:
                ids[s.id] = s
                rows.append(f"#{s.id} [{self.tree.path(tid)}] {' '.join(s.text.split())[:300]}")
        return "\n".join(rows) or "(none)", ids

    def propose(self, turn: str, when: str):
        """The model step: (facts, candidate ids, call info). Separate from apply so the ledger records every proposal before anything is written."""
        tids = self.candidates(turn)
        cur, ids = self.current(tids)
        prompt = PROMPT.format(max_facts=MAX_FACTS, tree=self.tree_text, current=cur, turn=turn[:12000], when=when or "date unknown")
        c = self.call(prompt, SYSTEM, schema=SCHEMA, extra=["--effort", "low"])
        got = haiku.parse_json(c.text)
        facts = got.get("facts") if isinstance(got.get("facts"), list) else []
        return facts, ids, tids, c

    def write(self, turn: str, *, ref: str, when: str = "", session: str = "haiku-iface") -> dict:
        run = f"haiku-iface:{uuid.uuid4().hex[:8]}"
        writer = f"extractor:{run}"
        rec = {"run": run, "ref": ref, "when": when, "turn_chars": len(turn), "proposals": [], "applied": [], "rejected": [], "candidates": []}
        t0 = time.time()
        try:
            facts, ids, tids, c = self.propose(turn, when)
        except Exception as e:
            rec["error"] = f"{type(e).__name__}: {str(e)[:300]}"
            self._log(rec)
            return rec
        rec.update({"seconds": round(c.seconds, 2), "in_tok": c.input_tokens + c.cache_read + c.cache_write, "out_tok": c.output_tokens, "cost": c.cost_usd,
                    "candidates": [self.tree.path(t) for t in tids], "candidate_ids": sorted(ids)})
        nturn = norm(turn)
        for f in facts[:MAX_FACTS]:
            rec["proposals"].append(f)

            def reject(why: str, **extra) -> None:
                rec["rejected"].append({"why": why, "fact": f, **extra})
            op, text, quote = f.get("op"), (f.get("text") or "").strip(), (f.get("quote") or "").strip()
            if not quote or norm(quote) not in nturn:
                reject("quote-not-in-turn")
                continue
            if op in ("add", "supersede") and not text:
                reject("no-text")
                continue
            if op in ("add", "supersede") and is_status_line(text):
                reject("status-line")
                continue
            ev = [Evidence("message", ref, quote[:300])]
            if op in ("confirm", "supersede"):
                sid = f.get("state_id")
                if sid not in ids:
                    reject("id-not-candidate")
                    continue
                old = self.store.get_state(sid)
                if old is None or not old.standing:
                    reject("not-standing")
                    continue
                if op == "confirm":
                    self.store.confirm(sid, actor=writer)
                    self.store.add_evidence(sid, ev[0], actor=writer)
                    rec["applied"].append({"op": "confirm", "state": sid, "prev_confirmed": old.confirmed_utc, "thing": old.thing_name, "quote": quote,
                                           "old_text": old.text})
                    continue
                if norm(text) == norm(old.text):
                    reject("supersede-with-same-text")
                    continue
                try:
                    new = self.store.add_state(old.thing_name, text, evidence=ev, writer=writer, trust="extracted", learned_how="inferred", session_id=session,
                                               kind=f.get("kind") or "belief", volatility=f.get("volatility") if f.get("volatility") in ("work", "stable", "rule") else "work",
                                               supersedes=sid, as_of=when[:10] or None)
                    self.store.supersede(sid, new.id, reason=f"{run}: {quote[:160]}", actor=writer)
                except StoreError as e:
                    reject("store-refused", error=str(e))
                    continue
                rec["applied"].append({"op": "supersede", "old": sid, "new": new.id, "thing": old.thing_name, "old_text": old.text, "text": text, "quote": quote})
                continue
            if op != "add":
                reject("bad-op")
                continue
            thing = (f.get("thing") or "").strip()
            known = self.tree.resolve(thing)
            segs = [p.strip() for p in thing.split(">") if p.strip()]
            if known is None and len(segs) > 1:
                pid = self.tree.resolve(segs[-2])
                if pid is None:
                    reject("unknown-parent")
                    continue
                name = f"{self.tree.name(pid)} > {segs[-1]}"
            elif known is None:
                reject("new-top-level-thing")
                continue
            else:
                name = self.tree.name(known)
            dup = next((s for s in self.store.states_of(known) if norm(s.text) == norm(text)), None) if known else None
            if dup:
                reject("exact-duplicate", state=dup.id)
                continue
            claim = {"thing": name, "text": text, "writer": writer, "session_id": session, "learned_how": "inferred", "trust": "extracted",
                     "kind": f.get("kind") if f.get("kind") in ("belief", "rule", "commitment") else "belief",
                     "volatility": f.get("volatility") if f.get("volatility") in ("work", "stable", "rule") else "work", "as_of": when[:10] or None,
                     "evidence": [{"type": "message", "ref": ref, "excerpt": quote[:300]}]}
            try:
                r = submit_claim(self.store, claim)
            except ClaimError as e:
                reject("claim-refused", error=str(e))
                continue
            rec["applied"].append({"op": "add", "new": r.state.id, "thing": r.state.thing_name, "asked_thing": thing, "text": text, "quote": quote,
                                   "new_thing": known is None})
        rec["wall_s"] = round(time.time() - t0, 2)
        self._log(rec)
        if any(a["op"] == "add" and a.get("new_thing") for a in rec["applied"]):
            self.refresh()
        return rec

    def _log(self, rec: dict) -> None:
        self.ledger.parent.mkdir(parents=True, exist_ok=True)
        with open(self.ledger, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, default=str) + "\n")


def undo(store, ledger: Path, run: str | None = None) -> int:
    """Reverses the applied changes of one run (or every run) in the ledger, newest first."""
    n = 0
    recs = [json.loads(l) for l in ledger.read_text(encoding="utf-8").splitlines() if l.strip()]
    for rec in reversed(recs):
        if run and rec["run"] != run:
            continue
        for a in reversed(rec.get("applied", [])):
            try:
                if a["op"] == "add":
                    store.reject(a["new"], f"undo {rec['run']}", actor="haiku-iface:undo")
                elif a["op"] == "supersede":
                    store.reject(a["new"], f"undo {rec['run']}", actor="haiku-iface:undo")
                    store.reopen(a["old"], f"undo {rec['run']}", actor="haiku-iface:undo")
                elif a["op"] == "confirm":
                    store.db.execute("UPDATE states SET confirmed_utc = ? WHERE id = ?", (a.get("prev_confirmed"), a["state"]))
                    store.db.commit()
                n += 1
            except StoreError as e:
                print("undo failed:", a, e)
    return n


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--ledger", required=True)
    ap.add_argument("--undo", default=None, help="a run id from the ledger, or 'all'")
    args = ap.parse_args()
    if "statekeeper" in str(Path(args.db).resolve().parent).lower() and Path(args.db).name == "shadow.db":
        raise SystemExit("refusing to write the live store: pass a copy")
    from statekeeper import SqliteStore
    store = SqliteStore(args.db)
    if args.undo:
        print(undo(store, Path(args.ledger), None if args.undo == "all" else args.undo), "changes undone")
    return 0


if __name__ == "__main__":
    sys.exit(main())
