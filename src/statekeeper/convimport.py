"""Bulk import of old conversation fragments (the frozen Persistence store's type-3 fragments) through the extractor, as a low-trust tier.

What lands: facts filed under a thing the store already has, a numbered thing (a ticket, PR or build), or "Known > new thing" under a known parent;
written by "import:conversation:<run>" at extracted trust, so they reach an input only through an identifier it says until someone reviews or
confirms them (retrieve.name_only). Dated by the fragment when the model gives no date. What is dropped: an agent's own progress ("is running",
"will now ..."), an agent's commitments, and new things that are none of the above (the sample showed those become generic magnets: "watcher",
"rollout"). A fact the store already holds (`near` finds it) gains the fragment as evidence instead of a copy.

Idempotent: every kept or corroborated fact is recorded in import_map under SOURCE with source_id = fragment id * 100 + its index, and a fragment
with any row there is skipped (the tool keeps a progress file for fragments that yielded nothing).
"""
from __future__ import annotations

import json
import re
from typing import Callable, Optional

from .extract import Extractor, parse
from .identifiers import numbers_in_text
from .reparent import PATH_SEP
from .store import Evidence, Store, StoreError

SOURCE = "persistence-conversation"
WRITER = "import:conversation"
PER_FRAGMENT = 100
# an agent's own progress, plans and one-off moves: true when said, useless later
PROGRESS = re.compile(r"\b(was sent|is sending|sent (it|them|the)|is running|are running|running the|re-?ran|relayed|was relayed|has been relayed|"
                      r"ended with|completed its|plans? to|is planning|will (now|next|check|run|send|post|look|note|fix|ask|wait|re-?arm)|is writing|"
                      r"is building|is fixing|is waiting|was asked to|has started|started a|kicked off|dispatched|re-?armed|nothing new|no events|"
                      r"no further action|parked|is checking|checked)\b", re.I)


def done_fragments(store: Store) -> set[int]:
    return {sid // PER_FRAGMENT for sid in store.imported(SOURCE)}


def drop_reason(store: Store, f: dict, *, by_person: bool) -> Optional[str]:
    thing = str(f.get("thing") or "").strip()
    if f.get("kind") == "commitment" and not by_person:
        return "agent-commitment"
    if PROGRESS.search(str(f.get("text") or "")):
        return "agent-progress"
    head = thing.split(PATH_SEP)[0].strip() if PATH_SEP in thing else None
    if not (store.find(thing) is not None or numbers_in_text(thing) or (head and store.find(head) is not None)):
        return "new-thing"
    return None


def import_fragment(store: Store, frag: dict, answer: str, *, near: Callable[[str], Optional[int]], user_source: str = "John",
                    session: Optional[str] = None) -> dict:
    """Imports one fragment ({id, content, created, source}) from the model's raw `answer`. `near(text)` returns the id of a standing state that
    already says "<thing>: <text>", or None. Returns counts: written, corroborated, dropped by reason, rejected by the extractor."""
    fid = int(frag["id"])
    have = {sid for sid in store.imported(SOURCE) if sid // PER_FRAGMENT == fid}
    if have:
        return {"skipped": "already imported"}
    rec: dict = {"fragment": fid, "written": 0, "corroborated": 0, "dropped": {}, "rejected": {}}
    try:
        facts = parse(answer).get("facts") or []
    except (ValueError, TypeError) as e:
        rec["error"] = f"unreadable answer: {e}"[:120]
        return rec
    by_person = frag.get("source") == user_source
    date = str(frag.get("created") or "")[:10] or None
    ref = f"persistence-conv:{fid}"
    k = 0
    keep = []
    for f in facts if isinstance(facts, list) else []:
        if not isinstance(f, dict):
            continue
        why = drop_reason(store, f, by_person=by_person)
        if why:
            rec["dropped"][why] = rec["dropped"].get(why, 0) + 1
            continue
        thing = str(f.get("thing") or "").split(PATH_SEP)[-1].strip()
        same = near(f"{thing}: {str(f.get('text') or '').strip()}")
        if same is not None:
            try:
                store.add_evidence(same, Evidence("message", ref, (frag.get("content") or "")[:300]), actor=f"{WRITER}:corroborate")
                store.map_import(SOURCE, fid * PER_FRAGMENT + k, same)
                k += 1
                rec["corroborated"] += 1
                continue
            except StoreError:
                pass
        keep.append({**f, "as_of": f.get("as_of") or date})
    if keep:
        m = {"id": ref, "content": frag.get("content") or "", "created": str(frag.get("created") or "")[:19], "source": frag.get("source") or "local"}
        canned = json.dumps({"facts": keep})
        out = Extractor(store, call=lambda _p: canned, session=session, user_source=user_source, writer_prefix=WRITER).extract(m)
        rec["rejected"] = out.get("rejected", {})
        for w in out.get("written", []):
            store.map_import(SOURCE, fid * PER_FRAGMENT + k, w["id"])
            k += 1
            rec["written"] += 1
        rec["written_ids"] = [w["id"] for w in out.get("written", [])]
    return rec
