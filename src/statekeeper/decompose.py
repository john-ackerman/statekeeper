"""Long states become atomic ones: a note, an imported document or a long claim is split by a model into small standalone states (one concept,
belief, behaviour, rule or how-to each), filed under the thing each is about, each citing the original's sources and the parent state (`sk show <parent id>`
prints the full source text). The parent is then closed with its first child as replacement and every child named in the
resolution: superseded, never deleted.

Recall runs on states: a monolith ("PDF rollout state ...", 600 characters about six subjects) is close in meaning to many prompts and right for
few, so it adds noise; its atoms each match the prompt they answer.

Two steps so a person can look first: `plan` calls the model and returns the proposed atoms (nothing written); `apply` writes a reviewed plan.
The model call is injected (`call(prompt) -> str`, default extract.call_haiku). The nightly job runs both for new long states.
"""
from __future__ import annotations

import json
import re
from typing import Callable, Optional

from .rehome import home_of
from .store import Evidence, State, Store

WRITER = "rule:decompose"
LONG = 280                 # characters: a state this long, or with three or more sentences, is a candidate
MAX_ATOMS = 15
BATCH = 3
_SENT = re.compile(r"(?<=[.!?])\s+(?=[A-Z(`\"'])")
SECRET = re.compile(r"(password|passwd|secret|token|api[_ -]?key|totp)( is)?\s*[:=]\s*\S{6,}", re.I)
PROMPT = """Split each numbered note below into ATOMIC facts. Each fact is ONE standalone sentence that makes sense without the note: name its subject
(never "it", "this", "the above"), keep tool names, commands, paths, ticket keys, numbers and dates exactly as written, keep "may"/"planned"/"proposed".
Kinds of fact worth keeping: a concept or how something works, a belief or finding, a behaviour or preference, a standing rule or decision (with its
reason), a tool and how to use it. Leave out narration, status chatter and anything a later reader cannot use. At most {max_atoms} per note, the most
useful first. NEVER include a password, token, key, TOTP secret or the contents of a credentials file (a path to one is fine).
For each fact give:
  "thing": the tool, project, ticket, system or topic it is about (a short name; reuse names from the note). Use a person or agent ONLY when the fact
           is about them as a person (a preference, a role), never as the doer of something about a tool.
  "object": another thing it relates to, or null.
  "text": the sentence.
  "kind": "rule" for a standing rule, decision or how-to that should always hold; else "belief".
  "durable": true when it is likely still true weeks from now, false for a point-in-time status.
Answer with ONLY a JSON object mapping each note number to a list of facts.

{notes}"""


def is_long(s: State) -> bool:
    return not s.state_key and (len(s.text) > LONG or len(_SENT.split(s.text.strip())) >= 3)


def source_text(store: Store, s: State) -> str:
    """The fullest text the state stands for: the longest evidence excerpt when it extends the state's own text (an imported document), else the text."""
    ev = store.evidence_of([s.id])[s.id]
    best = max((e.excerpt or "" for e in ev), key=len, default="")
    return best if len(best) > len(s.text) else s.text


def parse(answer: str) -> dict[int, list[dict]]:
    m = re.search(r"\{.*\}", answer, re.S)
    if not m:
        return {}
    try:
        got = json.loads(m.group(0))
    except ValueError:
        return {}
    out: dict[int, list[dict]] = {}
    for k, v in (got.items() if isinstance(got, dict) else []):
        try:
            n = int(str(k).strip("#"))
        except ValueError:
            continue
        atoms = []
        for a in v if isinstance(v, list) else []:
            if isinstance(a, dict) and str(a.get("text") or "").strip() and str(a.get("thing") or "").strip() and not SECRET.search(str(a["text"])):
                atoms.append({"thing": str(a["thing"]).strip()[:80], "object": (str(a["object"]).strip()[:80] or None) if a.get("object") else None,
                              "text": " ".join(str(a["text"]).split())[:500], "kind": "rule" if a.get("kind") == "rule" else "belief",
                              "durable": bool(a.get("durable", True))})
        out[n] = atoms[:MAX_ATOMS]
    return out


def plan(store: Store, states: list[State], call: Optional[Callable[[str], str]] = None, *, batch: int = BATCH) -> dict[int, list[dict]]:
    """{parent state id: [atom, ...]} for the given states (nothing written). A failed batch is left out (try again later)."""
    if call is None:
        from .extract import call_haiku
        call = call_haiku
    out: dict[int, list[dict]] = {}
    for i in range(0, len(states), batch):
        part = states[i:i + batch]
        notes = "\n\n".join(f"NOTE {n + 1} (filed under: {s.thing_name}{' / ' + s.object_name if s.object_name else ''}; recorded {s.recorded_utc[:10]}):\n"
                            f"{source_text(store, s)[:6000]}" for n, s in enumerate(part))
        try:
            got = parse(call(PROMPT.format(max_atoms=MAX_ATOMS, notes=notes)))
        except Exception:
            continue
        for n, s in enumerate(part):
            if got.get(n + 1):
                out[s.id] = got[n + 1]
    return out


def apply(store: Store, planned: dict[int, list[dict]], *, actor: str = WRITER) -> dict:
    """Writes each parent's atoms (same trust, learned-how and status as the parent; volatility stable for durable atoms, work otherwise) and closes
    the parent. A parent that is no longer standing, or with fewer than two atoms, is left as it is."""
    counts = {"parents": 0, "atoms": 0, "skipped": 0}
    for pid, atoms in planned.items():
        p = store.get_state(int(pid))
        if p is None or not p.standing or len(atoms) < 2:
            counts["skipped"] += 1
            continue
        # the source's references (not its text again: `sk show <parent>` prints the full source), plus the parent itself
        ev = [Evidence(e.ref_type, e.ref, None, e.sha256) for e in store.evidence_of([p.id])[p.id]] + [Evidence("state", f"#{p.id}", None)]
        made = []
        for a in atoms:
            thing, obj = home_of(store, a["thing"], a.get("object"))
            st = store.add_state(thing, a["text"], evidence=ev, writer=actor, trust=p.trust, learned_how=p.learned_how, session_id=p.session_id,
                                 object=obj, kind=a["kind"], volatility=("rule" if a["kind"] == "rule" else "stable") if a["durable"] else "work",
                                 as_of=p.as_of_utc, recorded=p.recorded_utc, status=p.status)
            made.append(st.id)
        store.close_state(p.id, f"decomposed into {', '.join(f'#{m}' for m in made)}", replaced_by=made[0], actor=actor)
        counts["parents"] += 1
        counts["atoms"] += len(made)
    return counts
