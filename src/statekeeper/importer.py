"""Import from an export of an earlier fact store (a JSON document with concepts, facts, fragments).

Policy: accepted facts become `derived` states that stand; proposals stay proposals (`extracted` trust);
rejected ones are imported rejected so they are not learned again; kinds carry over (the source's "self" is an agent); superseded stays superseded.
Each fact's evidence is a source fragment id, meaningless here, so the evidence carries an excerpt (the sentence that backs the fact), the
content hash and a handle ("legacy:<id>") for as long as the source exists.
"""
from __future__ import annotations

import hashlib
import re
from typing import Any

from .store import Evidence, SqliteStore, StoreError

SOURCE = "legacy-fact"
KIND_MAP ={"thing": "thing", "person": "person", "self": "agent", "value": "value"}
_SENTENCE = re.compile(r"(?<=[.;!?])\s+|\n+")
_WORD = re.compile(r"[^\W_]{4,}", re.UNICODE)


def best_sentence(content: str, fact: str, width: int = 300) -> str:
    """The sentence of `content` sharing the most words with `fact`, on one line, at most `width` characters."""
    want = set(_WORD.findall(fact.lower()))
    sentences = [s.strip() for s in _SENTENCE.split(content) if s.strip()]
    if not sentences:
        return ""
    best = max(sentences, key=lambda s: len(want & set(_WORD.findall(s.lower()))))
    best = " ".join(best.split())
    return best if len(best) <= width else best[:width - 1] + "…"


def import_export(store: SqliteStore, data: dict[str, Any], *, actor: str = "import:legacy") -> dict[str, int]:
    counts = {"things": 0, "aliases": 0, "states": 0, "superseded": 0, "rejected": 0, "skipped": 0}
    names: dict[int, str] = {}
    for c in data["concepts"]:
        if store.find(c["name"]) is not None:
            names[c["id"]] = store.find(c["name"]).name  # type: ignore[union-attr]
            continue
        kind = KIND_MAP.get(c.get("kind", "thing"), "thing")
        store.add_thing(c["name"], kind, kind_said=c.get("kind_said", True), actor=actor)
        if c.get("confirmed"):
            store.set_kind(c["name"], kind, actor=actor)
        names[c["id"]] = c["name"]
        counts["things"] += 1
    for c in data["concepts"]:
        for alias in c.get("aliases", []):
            try:
                store.add_alias(c["name"], alias, "imported", actor=actor)
                counts["aliases"] += 1
            except StoreError:
                counts["skipped"] += 1
    fragments = data.get("fragments", {})
    new_id: dict[int, int] = dict(store.imported(SOURCE))
    counts["synced"] = 0
    # states from an earlier import that predate the map: recognised by subject and text, so a re-import never duplicates them
    earlier = {(r[0].lower(), r[1]): r[2] for r in store.db.execute(
        "SELECT t.name, s.text, s.id FROM states s JOIN things t ON t.id = s.thing_id WHERE s.writer LIKE 'import:%'")}
    for f in data["facts"]:
        status = f["status"]
        if status == "pending" or f["subject"] not in names:
            counts["skipped"] += 1
            continue
        known = new_id.get(f["id"]) or earlier.get((names[f["subject"]].lower(), f["text"]))
        if known:
            new_id[f["id"]] = known
            store.map_import(SOURCE, f["id"], known)
            st = store.get_state(known)
            if st and status == "active" and st.status == "proposed" and st.trust == "extracted":
                store.accept(known, "accepted in the source", actor=actor)
                counts["synced"] += 1
            elif st and status == "rejected" and st.status in ("proposed", "active"):
                store.reject(known, f.get("resolution") or "rejected in the source", actor=actor)
                counts["synced"] += 1
            continue
        evidence = []
        for fid in f.get("sources", []):
            frag = fragments.get(str(fid)) or {}
            content = frag.get("content", "")
            evidence.append(Evidence("legacy-source", f"legacy:{fid}", best_sentence(content, f["text"]) or None,
                                     frag.get("sha256") or (hashlib.sha256(content.encode("utf-8")).hexdigest() if content else None)))
        if not evidence:
            counts["skipped"] += 1
            continue
        active = status == "active"
        state = store.add_state(
            names[f["subject"]], f["text"], evidence=evidence, writer=f"import:{f.get('extractedBy', 'unknown')}",
            trust="derived" if active else "extracted", learned_how="inferred", object=names.get(f.get("object")),
            is_state=bool(f.get("isState")), as_of=f.get("asOf"), recorded=f.get("recordedAt"),
            status="active" if active else "proposed")
        new_id[f["id"]] = state.id
        store.map_import(SOURCE, f["id"], state.id)
        counts["states"] += 1
        if status == "rejected":
            store.reject(state.id, f.get("resolution") or "rejected in the source", actor=actor)
            counts["rejected"] += 1
    for f in data["facts"]:
        old, new = new_id.get(f["id"]), new_id.get(f.get("supersededBy") or -1)
        if old and new:
            cur = store.get_state(old)
            if cur is None or cur.superseded_by is not None or cur.status in ("rejected", "pending"):
                continue
            try:
                store.supersede(old, new, f.get("resolution") or "", actor=actor)
                counts["superseded"] += 1
            except StoreError:
                counts["skipped"] += 1
    return counts
