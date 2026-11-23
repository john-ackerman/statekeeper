"""The structured-claim boundary: every writer (an agent, a rule, a person) hands the store a claim in one JSON shape; this module validates it,
clamps the trust to what the writer's name allows, and writes it through the Store. Nothing else needs to know the shape.

Claim fields: thing, text, writer, evidence[{type, ref, excerpt?, sha256?}] (required); session_id (required unless the writer is a rule or owner),
learned_how (read|inferred|told|claimed, default claimed), trust (requested; clamped), kind, state_key, object, as_of, volatility, verify_handle,
supersedes. Unknown fields are refused so a typo is never silently dropped.

Trust ceilings by writer name prefix: "owner:" owner, "rule:" verified, "derive:" derived, "extractor:" extracted, anything else claimed.
Conflict: a proposal whose state_key matches a different standing state is flagged "disputes #N" and left proposed; a standing (verified) write
replaces the current one by the store's keyed-supersession rule, so the latest verified observation wins.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field

from .store import TRUST_ORDER, Evidence, State, Store, StoreError

CEILING = (("owner:", "owner"), ("rule:", "verified"), ("derive:", "derived"), ("extractor:", "extracted"))
FIELDS = {"thing", "text", "writer", "evidence", "session_id", "learned_how", "trust", "kind", "state_key", "object", "as_of", "volatility", "verify_handle", "supersedes"}
LEARNED = ("read", "inferred", "told", "claimed")
KINDS = ("belief", "rule", "commitment")
VOLATILITY = ("live", "work", "stable", "rule")


class ClaimError(StoreError):
    """The claim is malformed; nothing was written."""


@dataclass
class ClaimResult:
    state: State
    clamped_from: str | None = None
    disputed_with: list[int] = field(default_factory=list)


def ceiling(writer: str) -> str:
    for prefix, level in CEILING:
        if writer.startswith(prefix):
            return level
    return "claimed"


def submit_claim(store: Store, claim: dict | str) -> ClaimResult:
    if isinstance(claim, str):
        try:
            claim = json.loads(claim)
        except ValueError as e:
            raise ClaimError(f"claim is not valid JSON: {e}") from None
    if not isinstance(claim, dict):
        raise ClaimError("a claim is a JSON object")
    unknown = set(claim) - FIELDS
    if unknown:
        raise ClaimError(f"unknown claim fields: {', '.join(sorted(unknown))}")
    for req in ("thing", "text", "writer"):
        if not str(claim.get(req) or "").strip():
            raise ClaimError(f"a claim needs {req}")
    writer = claim["writer"].strip()
    session = claim.get("session_id")
    if not session and ceiling(writer) in ("claimed", "extracted"):
        raise ClaimError("an agent claim needs its session_id: provenance is not optional")
    learned = claim.get("learned_how", "claimed")
    if learned not in LEARNED:
        raise ClaimError(f"learned_how must be one of {', '.join(LEARNED)}")
    if claim.get("kind", "belief") not in KINDS:
        raise ClaimError(f"kind must be one of {', '.join(KINDS)}")
    if claim.get("volatility", "work") not in VOLATILITY:
        raise ClaimError(f"volatility must be one of {', '.join(VOLATILITY)}")
    raw = claim.get("evidence")
    if not isinstance(raw, list) or not raw:
        raise ClaimError("a claim must cite evidence")
    evidence = []
    for e in raw:
        if not isinstance(e, dict) or not e.get("type") or not e.get("ref"):
            raise ClaimError("each evidence entry needs type and ref")
        excerpt = e.get("excerpt")
        sha = e.get("sha256") or (hashlib.sha256(excerpt.encode("utf-8")).hexdigest() if excerpt else None)
        evidence.append(Evidence(str(e["type"]), str(e["ref"]), excerpt, sha))
    requested = claim.get("trust") or ceiling(writer)
    if requested not in TRUST_ORDER:
        raise ClaimError(f"trust must be one of {', '.join(TRUST_ORDER)}")
    limit = ceiling(writer)
    trust = requested if TRUST_ORDER[requested] <= TRUST_ORDER[limit] else limit
    clamped = requested if trust != requested else None
    from .rehome import home_of
    from .reparent import place
    try:                                                              # "Parent > child", new things filed under the hierarchy, generic words refused
        subject = place(store, claim["thing"], text=claim["text"], actor=writer, refuse_generic=trust == "extracted")
        obj = place(store, claim["object"], text=claim["text"], actor=writer, refuse_generic=trust == "extracted") if claim.get("object") else None
    except StoreError as e:
        raise ClaimError(str(e)) from None
    thing, obj = home_of(store, subject, obj, claim.get("state_key"))   # under the tool/topic, not the speaker
    state = store.add_state(thing, claim["text"], evidence=evidence, writer=writer, trust=trust, learned_how=learned, session_id=session,
                            object=obj, kind=claim.get("kind", "belief"), state_key=claim.get("state_key"), as_of=claim.get("as_of"),
                            volatility=claim.get("volatility", "work"), verify_handle=claim.get("verify_handle"), supersedes=claim.get("supersedes"))
    disputed: list[int] = []
    if state.status == "proposed" and state.state_key:
        for other in store.states_of(state.thing_id):
            if other.id != state.id and other.state_key == state.state_key and other.status == "active" and other.superseded_by is None and other.text.strip().lower() != state.text.strip().lower():
                disputed.append(other.id)
                store.flag_state(state.id, f"disputes #{other.id}", actor=writer)
    return ClaimResult(state, clamped, disputed)
