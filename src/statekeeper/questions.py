"""Requests a state answers ("doc2query"): for each durable or how-to state, a few short things a person would say when they need it, written once
by a model and embedded. A prompt like "there is a script to do the sign in for you" is far from the fact "Jane fills .env.local for login-tool and
running node login.mjs refreshes the state files" for a small embedding model, but close to "is there a script that signs the agent into QA?".

Measured on a regression set of "you already have a tool for that" prompts (2026-11-18): the right state's best question scored 0.78 to 0.94 against
the prompt's clauses, other tools' questions mostly 0.55 to 0.73, while the states' own texts sat at 0.60 to 0.75 for right and wrong alike.

The model call is injected (`call(prompt) -> str`, default `extract.call_haiku`); nothing here runs on the per-prompt path except the vectors.
"""
from __future__ import annotations

import json
import re
from typing import Callable, Optional

from .embed import Embedder, content_hash
from .store import State, Store

WRITER = "rule:questions"
BATCH = 15
PROMPT = """For each numbered fact below, write 3 or 4 short things a person might say or ask, in their own words, at the moment they need that fact:
a request, a question, or a half-remembered "there is a script for that" remark. Write them as the person who has forgotten the details would:
often WITHOUT the tool's exact name, using everyday words for the task (sign in, look up, check the logs, run the tests, deploy, get the data).
Keep each under 15 words. Do not add facts. Answer with ONLY a JSON object mapping each fact's number to a list of strings.

{facts}"""


def wants_questions(s: State) -> bool:
    """Durable or how-to states: what someone may need again weeks later without remembering it exists."""
    from .retrieve import is_howto
    return not s.state_key and s.thing_name != "working note" and is_howto(s)


def pending(store: Store) -> list[State]:
    have = {sid for _q, sid, _t in store.questions()}
    return [s for s in store.searchable_states() if s.id not in have and wants_questions(s)]


def parse(answer: str) -> dict[int, list[str]]:
    m = re.search(r"\{.*\}", answer, re.S)
    if not m:
        return {}
    try:
        got = json.loads(m.group(0))
    except ValueError:
        return {}
    out: dict[int, list[str]] = {}
    for k, v in got.items() if isinstance(got, dict) else []:
        try:
            out[int(str(k).strip("#"))] = [str(x) for x in v if isinstance(x, str)][:5] if isinstance(v, list) else []
        except ValueError:
            continue
    return out


def generate(store: Store, call: Optional[Callable[[str], str]] = None, *, limit: int = 300, batch: int = BATCH) -> dict:
    """Writes questions for up to `limit` states that have none. Returns counts. A failed batch is skipped (tried again next run)."""
    if call is None:
        from .extract import call_haiku
        call = call_haiku
    todo = pending(store)[:limit]
    counts = {"states": 0, "questions": 0, "failed_batches": 0}
    for i in range(0, len(todo), batch):
        part = todo[i:i + batch]
        facts = "\n".join(f"{n + 1}. [{s.thing_name}{' / ' + s.object_name if s.object_name else ''}] {' '.join(s.text.split())[:400]}" for n, s in enumerate(part))
        try:
            got = parse(call(PROMPT.format(facts=facts)))
        except Exception:
            counts["failed_batches"] += 1
            continue
        for n, s in enumerate(part):
            qs = got.get(n + 1) or []
            if qs:
                counts["questions"] += store.add_questions(s.id, qs, writer=WRITER)
                counts["states"] += 1
    return counts


def ensure_question_vectors(store: Store, embedder: Embedder, *, limit: int = 100000) -> int:
    have = {r["owner_id"]: r["content_hash"] for r in store.vectors("question", embedder.model)}
    todo = [(qid, text) for qid, _sid, text in store.questions() if have.get(qid) != content_hash(text)][:limit]
    for i in range(0, len(todo), 32):
        part = todo[i:i + 32]
        for (qid, text), v in zip(part, embedder.embed([t for _, t in part])):
            store.put_vector("question", qid, embedder.model, v, content_hash(text))
    return len(todo)
