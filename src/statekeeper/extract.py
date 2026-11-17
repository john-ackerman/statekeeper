"""Free-text extraction: one message in, proposed states out (never standing: an extractor's reading is a proposal).

Two known failure modes are handled:
an invalid-JSON answer is retried once, and an agent's own status lines ("still running", "waiting for", "re-armed") are dropped before writing.
The model call is injected (`call(prompt) -> str`); `call_haiku` runs `claude -p` headless with no settings, hooks or tools.
"""
from __future__ import annotations

import json
import re
import subprocess
import tempfile
import time
import uuid
from typing import Callable

from .claims import ClaimError, submit_claim
from .store import Store

MODEL = "claude-haiku-5-5"
MAX_FACTS = 6

PROMPT = """You read one message from an agent's working history and write down the current-state facts it states, so a later reader can see what is known about each named thing and where it was said.

Rules:
- A thing is a named person, agent, project, piece of code, ticket, machine or concept that someone would name again. Reuse a name from KNOWN THINGS exactly when the message means the same one.
- A fact is ONE plain sentence the message states about a subject thing, optionally naming an object thing. Only what the message says, never an inference, no stronger than it says it: keep "may", "planned", "proposed".
- claim: "fact" (something said to be so or to have happened), "proposal" (a suggestion, offer, request or plan not said to be carried out; the text must say so), or "instruction" (a one-off order for one session or run; instructions are dropped).
- kind: "belief" (something that is so), "rule" (how something should always be done), "commitment" (someone said they will do something).
- is_state: true for a condition that can stop being true (open, deployed, awaiting review); false for an event or lasting trait.
- as_of: the date it became true (YYYY-MM-DD) when the message gives it, else null. Never today's date.
- Leave out the agent's own progress and status lines (running, waiting, queued, re-armed, nothing new), passing detail, one-off steps and facts about the message itself. Fewer facts for a short message; none if it states nothing lasting. At most {max_facts}, the most useful first: decisions and why, what depends on what, what state something is in.

KNOWN THINGS (name | kind):
{known}

CURRENT STATES of things this message names:
{current}

MESSAGE #{id} ({source}, written {date}):
{content}

Answer with JSON only, no prose: {{"facts": [{{"thing": "...", "text": "...", "object": "... or null", "claim": "fact|proposal|instruction", "kind": "belief|rule|commitment", "is_state": false, "as_of": "YYYY-MM-DD or null"}}]}}"""

RETRY_NOTE = "\n\nYour previous answer was not valid JSON ({why}). Answer with the JSON object only: no prose, no code fence."

STATUS = re.compile(r"\b(still running|is running|are running|keeps? running|waiting (for|on)|awaiting (ci|the|a)\b.*(finish|complete|result)|not installed yet|queued|re-?armed|polling|"
                    r"nothing new|will report|in the background|hasn'?t finished|not finished|in progress|started|spawned|dispatched)\b", re.I)
AGENT_SUBJECT = re.compile(r"^(the |this |my )?(agent|builder|shepherd|watcher|monitor|session|assistant|background \w+|build|run|job|i|me|claude)\b", re.I)


def is_status_line(text: str) -> bool:
    return bool(STATUS.search(text))


def parse(text: str) -> dict:
    text = (text or "").strip()
    m = re.search(r"\{.*\}", text, re.S)
    got = json.loads(m.group(0) if m else text)
    if not isinstance(got, dict):
        raise ValueError("the answer is not a JSON object")
    return got


def call_haiku(prompt: str) -> str:
    """No user settings, so none of the user's hooks run; an empty working directory, so no project settings either."""
    with tempfile.TemporaryDirectory() as cwd:
        r = subprocess.run(["claude", "-p", "--setting-sources", "project", "--model", MODEL, "--tools", "", "--no-session-" + "persist" + "ence", "--output-format", "json"],
                           input=prompt, capture_output=True, text=True, timeout=300, cwd=cwd, encoding="utf-8")
    if r.returncode != 0:
        said = r.stderr.strip()
        if not said:
            try:
                said = str(json.loads(r.stdout).get("result") or "")
            except ValueError:
                said = r.stdout.strip()
        raise RuntimeError(f"claude exited {r.returncode}: {said[:200]}")
    return json.loads(r.stdout).get("result") or ""


class Extractor:
    def __init__(self, store: Store, call: Callable[[str], str] = call_haiku, session: str | None = None):
        self.store, self.call, self.session = store, call, session or f"extract-{uuid.uuid4().hex[:8]}"

    def prompt(self, m: dict) -> str:
        things = self.store.things()
        content = m.get("content") or ""
        low = content.lower()
        touched = [t for t in things if not t.retired and len(t.name) > 3 and t.name.lower() in low]
        current = [f"{t.name}: {s.text}" for t in touched[:8] for s in self.store.states_of(t.id)[:4]]
        return PROMPT.format(max_facts=MAX_FACTS, known="\n".join(f"{t.name} | {t.kind}" for t in touched[:60]) or "(none yet)",
                             current="\n".join(current) or "(none)", id=m["id"], source=m.get("source"), date=(m.get("created") or "")[:10], content=content[:6000])

    def _owned_by_writer(self, name: str) -> bool:
        """A deterministic writer already holds a current keyed state for this thing (an agent's status, a build's state): a status claim from free
        text would only compete with the outside read, so it is dropped."""
        t = self.store.find(name)
        return bool(t) and any(s.state_key and s.status == "active" and s.writer.startswith("rule:") for s in self.store.states_of(t.id))

    def extract(self, m: dict) -> dict:
        run = f"extractor:{uuid.uuid4().hex[:8]}"
        rec = {"message": m["id"], "source": m.get("source"), "run": run, "retries": 0, "written": [], "rejected": {}, "facts_seen": 0}
        prompt = self.prompt(m)
        t0 = time.time()
        got, why = None, ""
        for attempt in (0, 1):
            try:
                got = parse(self.call(prompt if attempt == 0 else prompt + RETRY_NOTE.format(why=why)))
                break
            except (ValueError, TypeError) as e:
                why = str(e)[:80]
                rec["retries"] += attempt == 0
            except Exception as e:  # the model call itself failed: logged, not retried here
                rec["error"] = f"{type(e).__name__}: {e}"
                break
        if got is None:
            rec.setdefault("error", f"invalid JSON after retry: {why}")
            rec["seconds"] = round(time.time() - t0, 1)
            return rec
        agent = m.get("source") != "Alex"
        facts = got.get("facts") if isinstance(got.get("facts"), list) else []
        rec["facts_seen"] = len(facts)

        def reject(reason: str) -> None:
            rec["rejected"][reason] = rec["rejected"].get(reason, 0) + 1
        for f in facts[:MAX_FACTS * 2]:
            if not isinstance(f, dict) or not str(f.get("thing") or "").strip() or not str(f.get("text") or "").strip():
                reject("malformed")
                continue
            if f.get("claim") == "instruction":
                reject("instruction")
                continue
            if is_status_line(f["text"]) and self._owned_by_writer(f["thing"].strip()):
                reject("owned-by-writer")
                continue
            if is_status_line(f["text"]) and (agent or AGENT_SUBJECT.match(f["thing"].strip())):
                reject("status-line")
                continue
            claim = {"thing": f["thing"], "text": f["text"], "object": f.get("object") or None, "writer": run, "session_id": self.session, "learned_how": "inferred",
                     "trust": "extracted", "kind": f.get("kind") if f.get("kind") in ("belief", "rule", "commitment") else "belief", "as_of": f.get("as_of") or None,
                     "evidence": [{"type": "message", "ref": str(m["id"]), "excerpt": (m.get("content") or "")[:300]}]}
            if f.get("claim") == "proposal" and not re.search(r"\b(propos|plan|wants?|asked|suggest|would|may|might|intend)", f["text"], re.I):
                f["text"] = "Proposed: " + f["text"]                 # a proposal must never read as done; said so rather than dropped
                rec["marked_proposed"] = rec.get("marked_proposed", 0) + 1
            try:
                r = submit_claim(self.store, claim)
            except ClaimError as e:
                reject(f"claim-refused")
                rec.setdefault("refusals", []).append(str(e))
                continue
            rec["written"].append({"id": r.state.id, "thing": f["thing"], "text": f["text"], "kind": claim["kind"]})
        rec["seconds"] = round(time.time() - t0, 1)
        return rec
