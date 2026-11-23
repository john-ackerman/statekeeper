"""Identifier references: a PR, ticket or build number said bare ("3342", "#3342", "CRM-3150", "build 13801") resolves to every thing whose name
or alias carries that number, before selection, so the thing's current keyed values and specific states can surface even when the words of the
input are far from them in meaning.

Also: a thing named "<words> #<n>" or "<words> PR <n>" ("SendGrid webhook #3342", "Lob webhook #3343") gives "<words>" as another way to say that
PR, but only when those words point at ONE number across the store and are rare in the store's states ("datepicker" or "backfill" are said about many
things, so they resolve to nothing; a pipeline name shared by many run numbers resolves to nothing either).
"""
from __future__ import annotations

import re
from typing import Iterable, Optional

_TOKEN = re.compile(r"[^\W_]+", re.UNICODE)
_DATE = re.compile(r"\b(19|20)\d\d-\d\d(-\d\d)?\b|\b\d{1,2}[:/]\d\d\b")
# words that say "the next number is an identifier": a bare 3-digit number or a year-like number counts only after one of these (or '#')
CUES = frozenset({"pr", "prs", "pull", "crm", "build", "builds", "run", "ticket", "issue", "pipeline", "v2", "vw", "virtuousv2", "virtuousworkspace"})
PR_CUES = frozenset({"pr", "prs", "pull", "v2", "vw", "virtuousv2", "virtuousworkspace"})
# words that never carry an identifier's subject on their own (the residue of "PR 3342", "Build 7.15.95.13801")
FILLER = frozenset({"pr", "prs", "pull", "request", "crm", "build", "builds", "ticket", "issue", "run", "and", "the", "of", "for", "v2", "vw",
                    "virtuousv2", "virtuousworkspace", "repo", "branch", "pipeline", "release", "version", "fix", "followup", "follow", "up"})
COUNT_NOUNS = frozenset("""rows row states records gifts contacts receipts orgs organizations lines tokens chars characters seconds secs ms minutes
mins hours days weeks files tests items users people emails messages renders errors requests calls bytes kb mb gb words points cases prompts vectors
threads comments cores replicas batches times percent dollars usd k commits checks""".split())
MIN_RESIDUE = 5         # a residue phrase needs a word at least this long ("sendgrid") to stand for a number
RARE_PHRASE = 8          # ... and may appear in at most this many standing states' text


def _tokens_with_cue(text: str) -> list[tuple[str, bool, str]]:
    """(token, preceded by '#', the previous token) for each token of the text, dates and clock times blanked out."""
    text = _DATE.sub(" ", text)
    out: list[tuple[str, bool, str]] = []
    prev = ""
    for m in _TOKEN.finditer(text):
        w = m.group(0).lower()
        hashed = m.start() > 0 and text[m.start() - 1] == "#"
        out.append((w, hashed, prev))
        prev = w
    return out


def numbers_in_text(text: str) -> list[tuple[str, bool]]:
    """Identifier numbers a text says, with whether 'crm' precedes each ("CRM-3150" is a ticket, "PR 3150" is not). A bare number needs 4+ digits
    and must not look like a year (1990-2039); with '#' or a cue word ("PR 149", "build 13801") 3+ digits are enough."""
    out: list[tuple[str, bool]] = []
    toks = _tokens_with_cue(text)
    for i, (w, hashed, prev) in enumerate(toks):
        if not w.isdigit() or len(w) < 3 or len(w) > 7:
            continue
        if not (hashed or prev in CUES):
            if len(w) < 4 or 1990 <= int(w) <= 2039:
                continue
            if i + 1 < len(toks) and toks[i + 1][0] in COUNT_NOUNS:
                continue                                      # "3000 rows" is a count, not PR 3000
        out.append((w, prev == "crm"))
    return out


def _pr_number(label: str) -> Optional[str]:
    """The one PR number in a name written '#<n>' or 'PR <n>', if it has exactly one number."""
    nums = [(w, hashed, prev) for w, hashed, prev in _tokens_with_cue(label) if w.isdigit()]
    if len(nums) != 1:
        return None
    w, hashed, prev = nums[0]
    return w if len(w) >= 3 and (hashed or prev in PR_CUES) and prev != "crm" else None


def residue(label: str) -> str:
    """The words of a numbered name other than its numbers and filler: 'SendGrid webhook #3342' -> 'sendgrid webhook'."""
    return " ".join(w for w, _h, _p in _tokens_with_cue(label) if not w.isdigit() and w not in FILLER)


class IdentifierIndex:
    """number -> things carrying it (split by ticket vs not), and residue phrase -> the one PR number it stands for."""

    def __init__(self, labels: Iterable[tuple[int, str]], state_texts: Optional[list[str]] = None,
                 live_values: Iterable[tuple[int, str]] = ()):
        """`live_values`: (thing id, text) of current keyed live values (an ask's title, an agent's card). A number they carry names that thing too,
        so "CRM-3148" reaches the open ask "board ask ask-2026..." whose title says it. Ordinary states' numbers never name their thing."""
        self.by_number: dict[str, dict[bool, set[int]]] = {}
        phrase_numbers: dict[str, set[str]] = {}
        for tid, text in live_values:
            for n, crm in numbers_in_text(text):
                self.by_number.setdefault(n, {True: set(), False: set()})[crm].add(tid)
        for tid, label in labels:
            for n, crm in numbers_in_text(label):
                self.by_number.setdefault(n, {True: set(), False: set()})[crm].add(tid)
            if any(ch in label for ch in "/\\") or label.lower().endswith(".md"):
                continue                                      # a file path is not a way of saying a PR
            n = _pr_number(label)
            r = residue(label) if n else ""
            if r and any(len(w) >= MIN_RESIDUE for w in r.split()):
                phrase_numbers.setdefault(r, set()).add(n)
        self.phrases = {p: next(iter(ns)) for p, ns in phrase_numbers.items() if len(ns) == 1}
        self.all_phrases = dict(self.phrases)            # common ones too: used when the input asks whether something shipped
        if state_texts is not None:
            padded = [f" {t} " for t in state_texts]
            self.phrases = {p: n for p, n in self.phrases.items() if sum(1 for t in padded if f" {p} " in t) <= RARE_PHRASE}

    def things_for(self, number: str, crm: bool) -> set[int]:
        """Things carrying `number`. Said as a ticket ("CRM-3150"): the ticket's things. Said bare or as a PR: the PR/build things, falling back to the
        ticket's when nothing else carries the number."""
        got = self.by_number.get(number)
        if not got:
            return set()
        if crm:
            return set(got[True])
        return set(got[False]) or set(got[True])

    def resolve(self, text: str, seq: list[str], common: bool = False) -> dict[int, str]:
        """thing id -> the identifier the input used for it ("3342", or 'sendgrid webhook -> 3342'). `seq` is the input's words, lowercased.
        `common`: also phrases said about many things ("did the datepicker ship": the question is about the one PR named that way)."""
        found: dict[int, str] = {}
        for n, crm in numbers_in_text(text):
            for tid in self.things_for(n, crm):
                found.setdefault(tid, n)
        joined = f" {' '.join(seq)} "
        for phrase, n in (self.all_phrases if common else self.phrases).items():
            if f" {phrase} " in joined:
                for tid in self.things_for(n, False):
                    found.setdefault(tid, f"{phrase} -> {n}")
        return found


TERMINAL = ("merged", "closed")
ENTITY_WORK_KEEP = 1       # of one numbered subject's 'work' notes that qualify for an input, only this many (the newest) are shown


def _when(s) -> str:
    return (s.as_of_utc or s.recorded_utc or "").replace("T", " ")[:19]


def entity_replaced(store, ids: IdentifierIndex, states: list, pool: Optional[list] = None) -> set[int]:
    """States to leave out because a newer fact about the same numbered subject (every thing carrying one PR/build number) exists:
      - a 'work' note recorded before the subject's keyed pr.state became merged or closed (it describes a PR that no longer exists in that form);
      - all but the newest ENTITY_WORK_KEEP of the subject's qualifying 'work' notes.
    Standing rules and stable facts (volatility rule/stable) and keyed values are never dropped here."""
    things_numbers: dict[int, Optional[str]] = {}

    def number_of(tid: int, name: str) -> Optional[str]:
        if tid not in things_numbers:
            nums = {n for n, crm in numbers_in_text(name) if not crm}
            things_numbers[tid] = next(iter(nums)) if len(nums) == 1 else None
        return things_numbers[tid]

    groups: dict[str, list] = {}
    for s in states:
        if s.state_key or s.volatility != "work":
            continue
        n = number_of(s.thing_id, s.thing_name)
        if n is not None:
            groups.setdefault(n, []).append(s)
    out: set[int] = set()
    for n, sts in groups.items():
        ended = ""
        for tid in ids.things_for(n, False):
            for k in store.states_of(tid):
                if k.state_key == "pr.state" and k.text.strip().lower().startswith(TERMINAL):
                    ended = max(ended, _when(k))
        live = []
        for s in sts:
            if ended and (s.recorded_utc or "").replace("T", " ")[:19] < ended:
                out.add(s.id)
            else:
                live.append(s)
        live.sort(key=lambda s: ((s.as_of_utc or s.recorded_utc or "")[:19].replace("T", " "), s.id), reverse=True)
        out |= {s.id for s in live[ENTITY_WORK_KEEP:]}
    if pool is not None:
        # a work note about PRs by number ("the plan puts #3325, #3337 and #3340 in the release"), written before every one of them merged or closed,
        # is a plan that has since been overtaken, wherever it is filed
        ended_at: dict[str, str] = {}
        for k in pool:
            if k.state_key == "pr.state" and k.text.strip().lower().startswith(TERMINAL):
                for n, crm in numbers_in_text(k.thing_name):
                    if not crm:
                        ended_at[n] = max(ended_at.get(n, ""), _when(k))
        for s in states:
            if s.state_key or s.volatility != "work" or s.id in out:
                continue
            nums = {n for n, crm in numbers_in_text(s.text) if not crm}
            if nums and all(n in ended_at for n in nums) and (s.recorded_utc or "").replace("T", " ")[:19] < min(ended_at[n] for n in nums):
                out.add(s.id)
    return out
