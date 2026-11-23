"""The Store interface and its SQLite implementation.

Everything that reads or writes state goes through `Store`. Adapters (hooks, tools) depend only on this interface, never on SQL.
Rules and operations: evidence before
standing, supersession, merge and rename with retirement, a likely-replaces hint, an append-only event log written by triggers.
"""
from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Optional, Protocol, Sequence

SCHEMA = Path(__file__).with_name("schema.sql")
KINDS = ("thing", "person", "agent", "value")
TRUST_ORDER = {"owner": 4, "verified": 3, "derived": 2, "claimed": 1, "extracted": 0}


class StoreError(Exception):
    """A refused operation (unknown thing, a rule would be broken). The message says why."""


def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3] + "+00:00"


@dataclass(frozen=True)
class Thing:
    id: int
    name: str
    kind: str
    kind_confirmed: int
    parent_id: Optional[int]
    summary: Optional[str]

    @property
    def retired(self) -> bool:
        return self.kind_confirmed == -2


@dataclass(frozen=True)
class Evidence:
    ref_type: str
    ref: str
    excerpt: Optional[str] = None
    sha256: Optional[str] = None


@dataclass(frozen=True)
class State:
    id: int
    thing_id: int
    thing_name: str
    object_id: Optional[int]
    object_name: Optional[str]
    text: str
    kind: str
    state_key: Optional[str]
    is_state: bool
    as_of_utc: Optional[str]
    recorded_utc: str
    confirmed_utc: Optional[str]
    status: str
    trust: str
    learned_how: str
    writer: str
    session_id: Optional[str]
    volatility: str
    verify_handle: Optional[str]
    superseded_by: Optional[int]
    supersedes: Optional[int]
    resolution: Optional[str]

    @property
    def recency(self) -> tuple[str, int]:
        """When it is as true as: what happened, else when it was learned; a day, then id so newer sorts later."""
        return ((self.as_of_utc or self.recorded_utc or "")[:10], self.id)

    @property
    def standing(self) -> bool:
        return self.superseded_by is None and self.status in ("active", "proposed")


@dataclass
class Event:
    seq: int
    event_type: str
    thing_id: Optional[int]
    state_id: Optional[int]
    actor: str
    payload: str
    recorded_utc: str


OUTCOME_KINDS = ("surfaced", "omitted", "used", "unhelpful", "contradicted", "verified_right", "verified_wrong", "missed")
WEIGHT_PREFIX = "weight.state."   # per-state usefulness weights live in param_history (logged, reasoned, revertible), not on the state


@dataclass(frozen=True)
class Unlink:
    id: int
    state_id: Optional[int]
    thing_id: Optional[int]
    other_thing_id: Optional[int]
    concept: Optional[str]
    match: str
    actor: str
    reason: str
    created_utc: str
    removed_utc: Optional[str]


@dataclass(frozen=True)
class Outcome:
    id: int
    state_id: Optional[int]
    thing_id: Optional[int]
    kind: str
    source: str
    turn_ref: Optional[str]
    detail: Optional[str]
    params: Optional[str]
    recorded_utc: str


class Store(Protocol):
    """What adapters and retrieval may call. SqliteStore implements it; a remote store could."""

    # things
    def add_thing(self, name: str, kind: str = "thing", *, summary: str | None = None, parent: str | None = None, kind_said: bool = True, actor: str = "store") -> Thing: ...
    def find(self, name_or_alias: str) -> Optional[Thing]: ...
    def get(self, thing_id: int) -> Optional[Thing]: ...
    def things(self) -> list[Thing]: ...
    def labels(self) -> list[tuple[int, str]]: ...
    def set_kind(self, name: str, kind: str, *, actor: str = "owner") -> Thing: ...
    def add_alias(self, name: str, alias: str, note: str | None = None, *, actor: str = "owner") -> None: ...
    def rename(self, name: str, new_name: str, *, actor: str = "owner") -> Thing: ...
    def merge(self, from_name: str, into_name: str, reason: str = "", *, actor: str = "owner") -> tuple[Thing, int]: ...
    def set_parent(self, thing_id: int, parent_id: int | None, reason: str, *, actor: str = "owner") -> Thing: ...
    # states
    def add_state(self, thing: str, text: str, *, evidence: Sequence[Evidence], writer: str, trust: str, learned_how: str,
                  session_id: str | None = None, object: str | None = None, kind: str = "belief", state_key: str | None = None,
                  is_state: bool = False, as_of: str | None = None, volatility: str = "work", verify_handle: str | None = None,
                  supersedes: int | None = None, status: str | None = None, recorded: str | None = None) -> State: ...
    def get_state(self, state_id: int) -> Optional[State]: ...
    def states_of(self, thing_id: int, *, history: bool = False) -> list[State]: ...
    def searchable_states(self) -> list[State]: ...
    def evidence_of(self, state_ids: Iterable[int]) -> dict[int, list[Evidence]]: ...
    def accept(self, state_id: int, reason: str = "", *, actor: str = "owner") -> State: ...
    def reject(self, state_id: int, reason: str, *, actor: str = "owner") -> State: ...
    def supersede(self, old_id: int, new_id: int, reason: str = "", *, actor: str = "owner") -> None: ...
    def close_state(self, state_id: int, reason: str, *, replaced_by: int | None = None, actor: str = "owner") -> State: ...
    def confirm(self, state_id: int, *, actor: str, at: str | None = None) -> State: ...
    def log_outcome(self, state_id: int | None, kind: str, *, source: str, thing_id: int | None = None, turn_ref: str | None = None, detail: str | None = None) -> int: ...
    def outcomes(self, *, kind: str | None = None, state_id: int | None = None, turn_ref: str | None = None) -> list["Outcome"]: ...
    def set_param(self, name: str, value: float, reason: str, *, actor: str) -> None: ...
    def get_param(self, name: str, default: float) -> float: ...
    def state_weights(self) -> dict[int, float]: ...
    def unlink(self, state_id: int | None, *, thing: str | None = None, other_thing: str | None = None, concept: str | None = None,
               match: str = "any", reason: str, actor: str) -> int: ...
    def relink(self, unlink_id: int, reason: str, *, actor: str) -> None: ...
    def unlinks(self, *, active: bool = True, state_id: int | None = None) -> list[Unlink]: ...
    def revert_param(self, name: str, *, actor: str) -> float: ...
    def param_history(self, name: str) -> list[tuple[int, float, str, str]]: ...
    def add_evidence(self, state_id: int, evidence: Evidence, *, actor: str) -> None: ...
    def record_injection(self, turn_ref: str, source: str, hook_event: str, kind: str, text: str) -> None: ...
    def injections(self, *, turn_ref: str | None = None, source: str | None = None) -> list[tuple[str, str, str, str, str, str]]: ...
    def map_import(self, source: str, source_id: int, state_id: int) -> None: ...
    def imported(self, source: str) -> dict[int, int]: ...
    def link_states(self, a_id: int, b_id: int, relation: str, score: float, *, actor: str) -> bool: ...
    def links_of(self, state_id: int) -> list[tuple[int, int, str, float]]: ...
    def reopen(self, state_id: int, reason: str, *, actor: str) -> State: ...
    def record_turn_text(self, turn_ref: str, kind: str, text: str, *, session_id: str | None = None) -> None: ...
    def turn_texts(self, kind: str, *, after: str | None = None) -> list[tuple[str, str | None, str, str]]: ...
    def flag_state(self, state_id: int, reason: str, *, actor: str) -> int: ...
    def open_flags(self) -> list[tuple[int, str, str]]: ...
    def events(self, after: int = 0, limit: int = 1000, *, thing_id: int | None = None) -> list[Event]: ...
    # vectors
    def put_vector(self, owner_type: str, owner_id: int, model: str, vector: Sequence[float], content_hash: str) -> None: ...
    def vectors(self, owner_type: str, model: str) -> list[dict]: ...
    def add_questions(self, state_id: int, texts: Sequence[str], *, writer: str) -> int: ...
    def questions(self) -> list[tuple[int, int, str]]: ...


_WORD = re.compile(r"[^\W_]{4,}", re.UNICODE)


def _content_words(text: str) -> set[str]:
    return set(_WORD.findall(text.lower()))


def likely_replaces(states: Sequence[State], evidence: dict[int, list[Evidence]] | None = None) -> dict[int, int]:
    """For each state that likely replaces an older standing one about the same thing, the older one's id.
    A newer state that shares most of its content words (or a third of them when both point at the same object) with an older standing state of the
    same thing is likely an update of it, not a second thing true alongside. Two states citing the same evidence say what that source said, not that
    one replaced the other. A hint to look at, never a decision."""
    standing = [s for s in states if s.standing]
    words = {s.id: _content_words(s.text) for s in standing}
    found: dict[int, int] = {}
    for new in standing:
        best: tuple[int, float] | None = None
        for old in standing:
            if old.id == new.id or old.thing_id != new.thing_id or old.recency >= new.recency:
                continue
            if evidence and {(e.ref_type, e.ref) for e in evidence.get(new.id, [])} & {(e.ref_type, e.ref) for e in evidence.get(old.id, [])}:
                continue
            a, b = words[new.id], words[old.id]
            shared = len(a & b)
            overlap = shared / max(1, min(len(a), len(b)))
            needed = 0.34 if old.object_id == new.object_id else 0.5
            if shared >= 2 and overlap >= needed and (best is None or overlap > best[1]):
                best = (old.id, overlap)
        if best:
            found[new.id] = best[0]
    return found


_STATE_COLUMNS = ("s.id, s.thing_id, t.name, s.object_id, o.name, s.text, s.kind, s.state_key, s.is_state, s.as_of_utc, s.recorded_utc, "
                  "s.confirmed_utc, s.status, s.trust, s.learned_how, s.writer, s.session_id, s.volatility, s.verify_handle, "
                  "s.superseded_by, s.supersedes, s.resolution")
_STATE_FROM = "FROM states s JOIN things t ON t.id = s.thing_id LEFT JOIN things o ON o.id = s.object_id"


def _utf8(text: str) -> str:
    """Text SQLite can store: a lone surrogate (a JSON escape such as backslash-udc8f in a hook's input, or bytes decoded through the wrong code page) becomes "?"."""
    return text.encode("utf-8", "replace").decode("utf-8") if text else text


def _state(row) -> State:
    r = list(row)
    r[8] = bool(r[8])
    return State(*r)


class SqliteStore:
    """One SQLite file in WAL mode; this class is its only writer API (short transactions, a busy timeout), so one machine needs no other coordination."""

    def __init__(self, path: str | Path):
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path, timeout=15, isolation_level=None)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.execute("PRAGMA busy_timeout=15000")
        self._migrate()

    def close(self) -> None:
        self.db.close()

    def _migrate(self) -> None:
        # A database made before the earlier recall system was named "legacy" carries a CHECK on shadow_injections.source that refuses the new name, and
        # SQLite cannot alter a CHECK: move the table aside, let the schema recreate it, copy the rows across (any non-statekeeper source becomes "legacy").
        old = self.db.execute("SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'shadow_injections'").fetchone()
        if old and "'legacy'" not in old[0]:
            self.db.execute("DROP INDEX IF EXISTS idx_shadow_inj_turn")
            self.db.execute("ALTER TABLE shadow_injections RENAME TO shadow_injections_old")
        # the same for the outcomes CHECK when a new kind ("unhelpful": someone said a shown state was noise) was added
        old_out = self.db.execute("SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'outcomes'").fetchone()
        if old_out and "'unhelpful'" not in old_out[0]:
            self.db.execute("DROP INDEX IF EXISTS idx_outcomes_state")
            self.db.execute("DROP INDEX IF EXISTS idx_outcomes_turn")
            self.db.execute("ALTER TABLE outcomes RENAME TO outcomes_old")
        self.db.executescript(SCHEMA.read_text(encoding="utf-8"))
        if self.db.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'outcomes_old'").fetchone():
            self.db.execute("INSERT OR IGNORE INTO outcomes (id, state_id, thing_id, kind, source, turn_ref, detail, params, recorded_utc) "
                            "SELECT id, state_id, thing_id, kind, source, turn_ref, detail, params, recorded_utc FROM outcomes_old")
            self.db.execute("DROP TABLE IF EXISTS outcomes_old")
        if self.db.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'shadow_injections_old'").fetchone():
            self.db.execute("INSERT OR IGNORE INTO shadow_injections (id, turn_ref, source, hook_event, kind, text, created_utc) "
                            "SELECT id, turn_ref, CASE source WHEN 'statekeeper' THEN 'statekeeper' ELSE 'legacy' END, hook_event, kind, text, created_utc FROM shadow_injections_old")
            self.db.execute("DROP TABLE IF EXISTS shadow_injections_old")
        # the fact importer's source was renamed the same way. Rename only that old name: other importers (fragments) keep their own source
        self.db.execute("UPDATE OR IGNORE import_map SET source = 'legacy-fact' WHERE source = ?", ("per" + "sistence-fact",))
        self.db.execute("INSERT OR IGNORE INTO schema_version (version, applied_utc) VALUES (1, ?)", (now(),))

    def _act(self, actor: str) -> None:
        self.db.execute("INSERT INTO event_context (id, actor) VALUES (1, ?) ON CONFLICT(id) DO UPDATE SET actor = excluded.actor", (actor,))

    class _Tx:
        def __init__(self, store: "SqliteStore", actor: str):
            self.store, self.actor = store, actor

        def __enter__(self):
            self.store.db.execute("BEGIN IMMEDIATE")
            self.store._act(self.actor)
            return self.store.db

        def __exit__(self, exc_type, exc, tb):
            self.store.db.execute("DELETE FROM event_context")
            self.store.db.execute("ROLLBACK" if exc_type else "COMMIT")
            return False

    def _tx(self, actor: str) -> "SqliteStore._Tx":
        return SqliteStore._Tx(self, actor)

    # ---- things ----
    def _thing(self, row) -> Optional[Thing]:
        return Thing(*row) if row else None

    def add_thing(self, name, kind="thing", *, summary=None, parent=None, kind_said=True, actor="store") -> Thing:
        if kind not in KINDS:
            raise StoreError(f"kind must be one of {', '.join(KINDS)}")
        name = name.strip()
        if not name:
            raise StoreError("a thing needs a name")
        if self.find(name):
            raise StoreError(f"'{name}' already names a thing")
        parent_id = None
        if parent:
            p = self.find(parent)
            if not p:
                raise StoreError(f"there is no thing '{parent}' to be the parent")
            parent_id = p.id
        with self._tx(actor) as db:
            cur = db.execute("INSERT INTO things (name, kind, kind_confirmed, parent_id, summary, created_utc) VALUES (?,?,?,?,?,?)",
                             (name, kind, 0 if kind_said else -1, parent_id, summary, now()))
        return self.get(cur.lastrowid)  # type: ignore[return-value]

    def find(self, name_or_alias: str) -> Optional[Thing]:
        n = name_or_alias.strip()
        row = self.db.execute(
            "SELECT id, name, kind, kind_confirmed, parent_id, summary FROM things WHERE name = ? COLLATE NOCASE AND kind_confirmed <> -2 "
            "UNION ALL SELECT t.id, t.name, t.kind, t.kind_confirmed, t.parent_id, t.summary FROM things t JOIN aliases a ON a.thing_id = t.id "
            "WHERE a.alias = ? COLLATE NOCASE LIMIT 1", (n, n)).fetchone()
        return self._thing(row)

    def get(self, thing_id: int) -> Optional[Thing]:
        return self._thing(self.db.execute("SELECT id, name, kind, kind_confirmed, parent_id, summary FROM things WHERE id = ?", (thing_id,)).fetchone())

    def things(self) -> list[Thing]:
        return [Thing(*r) for r in self.db.execute(
            "SELECT id, name, kind, kind_confirmed, parent_id, summary FROM things WHERE kind_confirmed <> -2 ORDER BY name")]

    def labels(self) -> list[tuple[int, str]]:
        return [(r[0], r[1]) for r in self.db.execute(
            "SELECT id, name FROM things WHERE kind_confirmed <> -2 UNION ALL SELECT thing_id, alias FROM aliases")]

    def _need(self, name: str) -> Thing:
        t = self.find(name)
        if not t:
            raise StoreError(f"there is no thing '{name}'")
        return t

    def set_kind(self, name, kind, *, actor="owner") -> Thing:
        if kind not in KINDS:
            raise StoreError(f"kind must be one of {', '.join(KINDS)}")
        t = self._need(name)
        with self._tx(actor) as db:
            db.execute("UPDATE things SET kind = ?, kind_confirmed = 1 WHERE id = ?", (kind, t.id))
        return self.get(t.id)  # type: ignore[return-value]

    def add_alias(self, name, alias, note=None, *, actor="owner") -> None:
        t = self._need(name)
        if (taken := self.find(alias)):
            raise StoreError(f"'{alias}' already names {taken.name}")
        with self._tx(actor) as db:
            db.execute("INSERT INTO aliases (thing_id, alias, note) VALUES (?,?,?)", (t.id, alias.strip(), note))

    def rename(self, name, new_name, *, actor="owner") -> Thing:
        t = self._need(name)
        other = self.find(new_name)
        if other and other.id != t.id:
            raise StoreError(f"'{new_name.strip()}' already names {other.name}: merge joins two things")
        with self._tx(actor) as db:
            db.execute("UPDATE things SET name = ? WHERE id = ?", (new_name.strip(), t.id))
            db.execute("INSERT INTO events (event_type, thing_id, actor, payload, recorded_utc) VALUES ('thing-renamed', ?, ?, json_object('from', ?, 'to', ?), ?)",
                       (t.id, actor, t.name, new_name.strip(), now()))
        return self.get(t.id)  # type: ignore[return-value]

    def set_parent(self, thing_id, parent_id, reason, *, actor="owner") -> Thing:
        """Files a thing under another (or under nothing, parent_id None). Refuses a parent that sits under the thing already (a loop)."""
        t = self.get(thing_id)
        if t is None or t.retired:
            raise StoreError(f"there is no thing #{thing_id}")
        if parent_id is not None:
            p, seen = self.get(parent_id), set()
            if p is None or p.retired:
                raise StoreError(f"there is no thing #{parent_id} to be the parent")
            while p is not None and p.id not in seen:
                if p.id == t.id:
                    raise StoreError(f"{t.name} cannot go under something filed under it")
                seen.add(p.id)
                p = self.get(p.parent_id) if p.parent_id is not None else None
        with self._tx(actor) as db:
            db.execute("UPDATE things SET parent_id = ? WHERE id = ?", (parent_id, t.id))
            db.execute("INSERT INTO events (event_type, thing_id, actor, payload, recorded_utc) VALUES ('thing-reparented', ?, ?, json_object('from', ?, 'to', ?, 'reason', ?), ?)",
                       (t.id, actor, t.parent_id, parent_id, reason, now()))
        return self.get(t.id)  # type: ignore[return-value]

    def merge(self, from_name, into_name, reason="", *, actor="owner") -> tuple[Thing, int]:
        """Moves the old thing's states and aliases to the target, makes the old name an alias of the target and keeps the old row retired (not deleted)."""
        f, t = self._need(from_name), self._need(into_name)
        if f.id == t.id:
            raise StoreError("those are the same thing")
        if f.retired or t.retired:
            raise StoreError("a retired thing cannot be merged again")
        why = reason.strip() or "same thing under two names"
        with self._tx(actor) as db:
            ids = [r[0] for r in db.execute("SELECT id FROM states WHERE thing_id = ? OR object_id = ? ORDER BY id", (f.id, f.id))]
            db.execute("UPDATE states SET thing_id = ? WHERE thing_id = ?", (t.id, f.id))
            db.execute("UPDATE states SET object_id = ? WHERE object_id = ?", (t.id, f.id))
            db.execute("UPDATE aliases SET thing_id = ? WHERE thing_id = ?", (t.id, f.id))
            db.execute("UPDATE things SET parent_id = ? WHERE parent_id = ?", (t.id, f.id))
            db.execute("UPDATE things SET name = ?, summary = ?, kind_confirmed = -2 WHERE id = ?",
                       (f"{f.name} (merged into {t.name} #{t.id})", f"merged into {t.name} #{t.id}: {why}", f.id))
            db.execute("INSERT INTO aliases (thing_id, alias, note) VALUES (?,?,?)", (t.id, f.name, f"merged from #{f.id}: {why}"))
            db.execute("DELETE FROM embeddings WHERE owner_type = 'thing' AND owner_id = ?", (f.id,))
            db.execute("INSERT INTO events (event_type, thing_id, actor, payload, recorded_utc) VALUES ('thing-merged', ?, ?, json_object('from', ?, 'from_name', ?, 'reason', ?, 'states', json(?)), ?)",
                       (t.id, actor, f.id, f.name, why, "[" + ",".join(map(str, ids)) + "]", now()))
        return self.get(t.id), len(ids)  # type: ignore[return-value]

    # ---- states ----
    def add_state(self, thing, text, *, evidence, writer, trust, learned_how, session_id=None, object=None, kind="belief", state_key=None,
                  is_state=False, as_of=None, volatility="work", verify_handle=None, supersedes=None, status=None, recorded=None) -> State:
        """Writes a state with its evidence in one transaction. Without evidence nothing is written. Unless `status` is given, the trust decides:
        owner, verified and derived states stand (active); claimed and extracted ones are proposals until accepted. A standing state with a key
        replaces the current one with the same (thing, key) by rule."""
        if not text.strip():
            raise StoreError("a state needs its text")
        if not evidence:
            raise StoreError("a state must cite at least one piece of evidence: it is a view of something, never a source of truth")
        if trust not in TRUST_ORDER:
            raise StoreError(f"trust must be one of {', '.join(TRUST_ORDER)}")
        t = self.find(thing) or self.add_thing(thing, "thing", kind_said=False, actor=writer)
        o = None
        if object:
            o = self.find(object) or self.add_thing(object, "thing", kind_said=False, actor=writer)
            if o.id == t.id:
                o = None
        status = status or ("active" if TRUST_ORDER[trust] >= TRUST_ORDER["derived"] else "proposed")
        replaced_id = supersedes
        with self._tx(writer) as db:
            if status == "active" and state_key:
                row = db.execute("SELECT id FROM states WHERE thing_id = ? AND state_key = ? AND superseded_by IS NULL AND status = 'active'", (t.id, state_key)).fetchone()
                if row:
                    replaced_id = replaced_id or row[0]
            cur = db.execute(
                "INSERT INTO states (thing_id, object_id, text, kind, state_key, is_state, as_of_utc, recorded_utc, confirmed_utc, status, trust, learned_how, writer, "
                "session_id, volatility, verify_handle, supersedes) VALUES (?,?,?,?,?,?,?,?,?,'pending',?,?,?,?,?,?,?)",
                (t.id, o.id if o else None, text.strip(), kind, state_key, 1 if is_state else 0, as_of, recorded or now(), now() if trust in ("owner", "verified") else None,
                 trust, learned_how, writer, session_id, volatility, verify_handle, replaced_id))
            sid = cur.lastrowid
            for e in evidence:
                db.execute("INSERT OR IGNORE INTO evidence (state_id, ref_type, ref, excerpt, sha256, recorded_utc) VALUES (?,?,?,?,?,?)",
                           (sid, e.ref_type, e.ref, e.excerpt, e.sha256, now()))
            if status == "active" and replaced_id:
                old = db.execute("SELECT superseded_by FROM states WHERE id = ?", (replaced_id,)).fetchone()
                if old and old[0] is None:
                    db.execute("UPDATE states SET superseded_by = ?, resolution = ? WHERE id = ?", (sid, f"superseded by #{sid}", replaced_id))
            db.execute("UPDATE states SET status = ? WHERE id = ?", (status, sid))
        return self.get_state(sid)  # type: ignore[return-value]

    def get_state(self, state_id: int) -> Optional[State]:
        row = self.db.execute(f"SELECT {_STATE_COLUMNS} {_STATE_FROM} WHERE s.id = ?", (state_id,)).fetchone()
        return _state(row) if row else None

    def states_of(self, thing_id: int, *, history: bool = False) -> list[State]:
        standing = "" if history else "AND s.superseded_by IS NULL AND s.status IN ('active', 'proposed')"
        rows = self.db.execute(f"SELECT {_STATE_COLUMNS} {_STATE_FROM} WHERE (s.thing_id = ? OR s.object_id = ?) AND s.status <> 'pending' {standing}",
                               (thing_id, thing_id)).fetchall()
        return sorted((_state(r) for r in rows), key=lambda s: s.recency, reverse=True)

    def searchable_states(self) -> list[State]:
        rows = self.db.execute(f"SELECT {_STATE_COLUMNS} {_STATE_FROM} WHERE s.superseded_by IS NULL AND s.status IN ('active', 'proposed') "
                               "AND t.kind_confirmed <> -2 ORDER BY s.id")
        return [_state(r) for r in rows]

    def evidence_of(self, state_ids) -> dict[int, list[Evidence]]:
        ids = list(state_ids)
        out: dict[int, list[Evidence]] = {i: [] for i in ids}
        for chunk in range(0, len(ids), 500):
            part = ids[chunk:chunk + 500]
            for r in self.db.execute(f"SELECT state_id, ref_type, ref, excerpt, sha256 FROM evidence WHERE state_id IN ({','.join('?' * len(part))}) ORDER BY rowid", part):
                out[r[0]].append(Evidence(*r[1:]))
        return out

    def _standing_or_refuse(self, state_id: int) -> State:
        s = self.get_state(state_id)
        if not s:
            raise StoreError(f"there is no state #{state_id}")
        return s

    def accept(self, state_id, reason="", *, actor="owner") -> State:
        s = self._standing_or_refuse(state_id)
        if s.status != "proposed":
            raise StoreError(f"#{state_id} is {s.status}, not a proposal")
        with self._tx(actor) as db:
            db.execute("UPDATE states SET status = 'active', resolution = ? WHERE id = ?", (f"accepted {now()[:10]}" + (f": {reason}" if reason else ""), state_id))
            if s.supersedes:
                old = db.execute("SELECT superseded_by FROM states WHERE id = ?", (s.supersedes,)).fetchone()
                if old and old[0] is None:
                    db.execute("UPDATE states SET superseded_by = ?, resolution = ? WHERE id = ?", (state_id, f"superseded by #{state_id}", s.supersedes))
        return self.get_state(state_id)  # type: ignore[return-value]

    def reject(self, state_id, reason, *, actor="owner") -> State:
        if not reason.strip():
            raise StoreError("a rejection needs its reason: it is kept so the state is not learned again")
        self._standing_or_refuse(state_id)
        with self._tx(actor) as db:
            db.execute("UPDATE states SET status = 'rejected', resolution = ? WHERE id = ?", (f"rejected {now()[:10]}: {reason.strip()}", state_id))
        return self.get_state(state_id)  # type: ignore[return-value]

    def supersede(self, old_id, new_id, reason="", *, actor="owner") -> None:
        old, new = self._standing_or_refuse(old_id), self._standing_or_refuse(new_id)
        if old.thing_id != new.thing_id:
            raise StoreError("a state replaces one about the same thing")
        if not old.standing:
            raise StoreError(f"#{old_id} no longer stands")
        with self._tx(actor) as db:
            db.execute("UPDATE states SET superseded_by = ?, resolution = ? WHERE id = ?", (new_id, reason or f"superseded by #{new_id}", old_id))

    def close_state(self, state_id, reason, *, replaced_by=None, actor="owner") -> State:
        """Retires a standing state that is no longer true, so it stops being surfaced, embedded and counted; it stays in the history with its reason.
        With `replaced_by`, the standing state that now says what is true (it may be about another thing: a fact often moves, e.g. from a project to the
        person who changed it). Without one nothing replaces it, and the state points at itself: superseded_by = its own id reads as "closed". Every
        query that asks for standing states already excludes both. `reopen` undoes it."""
        if not reason.strip():
            raise StoreError("closing a state needs its reason: it is kept so the state is not learned again")
        s = self._standing_or_refuse(state_id)
        if not s.standing:
            raise StoreError(f"#{state_id} no longer stands")
        if replaced_by is not None:
            new = self._standing_or_refuse(replaced_by)
            if new.id == s.id or not new.standing:
                raise StoreError(f"#{replaced_by} cannot replace #{state_id}: it must be another standing state")
        with self._tx(actor) as db:
            db.execute("UPDATE states SET superseded_by = ?, resolution = ? WHERE id = ?",
                       (replaced_by or state_id, f"closed {now()[:10]}" + (f" (replaced by #{replaced_by})" if replaced_by else "") + f": {reason.strip()}", state_id))
        return self.get_state(state_id)  # type: ignore[return-value]

    def confirm(self, state_id, *, actor, at=None) -> State:
        self._standing_or_refuse(state_id)
        with self._tx(actor) as db:
            db.execute("UPDATE states SET confirmed_utc = ? WHERE id = ?", (at or now(), state_id))
        return self.get_state(state_id)  # type: ignore[return-value]

    # ---- outcomes and parameters (learning from reality; see docs/A section 15) ----
    def _params_in_force(self) -> str:
        rows = self.db.execute("SELECT name, value FROM param_history WHERE id IN (SELECT MAX(id) FROM param_history GROUP BY name) "
                               "AND name NOT LIKE 'weight.state.%' ORDER BY name")
        return ",".join(f"{n}={v:g}" for n, v in rows)

    def log_outcome(self, state_id, kind, *, source, thing_id=None, turn_ref=None, detail=None) -> int:
        if kind not in OUTCOME_KINDS:
            raise StoreError(f"outcome kind must be one of {', '.join(OUTCOME_KINDS)}")
        if state_id is not None and thing_id is None:
            row = self.db.execute("SELECT thing_id FROM states WHERE id = ?", (state_id,)).fetchone()
            thing_id = row[0] if row else None
        cur = self.db.execute("INSERT INTO outcomes (state_id, thing_id, kind, source, turn_ref, detail, params, recorded_utc) VALUES (?,?,?,?,?,?,?,?)",
                              (state_id, thing_id, kind, source, turn_ref, detail, self._params_in_force(), now()))
        return cur.lastrowid

    def outcomes(self, *, kind=None, state_id=None, turn_ref=None) -> list[Outcome]:
        rows = self.db.execute("SELECT id, state_id, thing_id, kind, source, turn_ref, detail, params, recorded_utc FROM outcomes "
                               "WHERE (? IS NULL OR kind = ?) AND (? IS NULL OR state_id = ?) AND (? IS NULL OR turn_ref = ?) ORDER BY id",
                               (kind, kind, state_id, state_id, turn_ref, turn_ref))
        return [Outcome(*r) for r in rows]

    def set_param(self, name, value, reason, *, actor) -> None:
        if not reason.strip():
            raise StoreError("a parameter change needs its reason")
        prev = self.db.execute("SELECT value FROM param_history WHERE name = ? ORDER BY id DESC LIMIT 1", (name,)).fetchone()
        self.db.execute("INSERT INTO param_history (name, value, reason, actor, previous, recorded_utc) VALUES (?,?,?,?,?,?)",
                        (name, float(value), reason.strip(), actor, prev[0] if prev else None, now()))

    def get_param(self, name, default) -> float:
        row = self.db.execute("SELECT value FROM param_history WHERE name = ? ORDER BY id DESC LIMIT 1", (name,)).fetchone()
        return row[0] if row else default

    def state_weights(self) -> dict[int, float]:
        """Every per-state usefulness weight in force (1.0 when none was ever set)."""
        rows = self.db.execute("SELECT name, value FROM param_history WHERE id IN (SELECT MAX(id) FROM param_history WHERE name LIKE 'weight.state.%' GROUP BY name)")
        return {int(n[len(WEIGHT_PREFIX):]): v for n, v in rows if n[len(WEIGHT_PREFIX):].isdigit()}

    # ---- negative links ("these may look related but they are not") ----
    def unlink(self, state_id, *, thing=None, other_thing=None, concept=None, match="any", reason, actor) -> int:
        """Records a negative link and returns its id. One of: (state, thing): the state never connects through that thing (match "any": not shown
        at all when the input names it or it is on topic; "name": only the name/topic connection is cut, meaning may still bring it); (state,
        concept): not shown for inputs about that concept; (state, match="meaning"): shown only through its own name; (thing, other_thing): the
        two never connect each other's states. Never deletes anything; `relink` ends it."""
        if not reason or not reason.strip():
            raise StoreError("an unlink needs its reason")
        if match not in ("any", "name", "meaning"):
            raise StoreError("match must be any, name or meaning")
        ids = {}
        for key, name in (("thing", thing), ("other", other_thing)):
            if name is not None:
                tt = self.find(name)
                if tt is None:
                    raise StoreError(f"no thing named {name!r}")
                ids[key] = tt.id
        if state_id is not None:
            if self.get_state(state_id) is None:
                raise StoreError(f"no state #{state_id}")
            if other_thing is not None or sum(x is not None for x in (thing, concept)) > 1 or (thing is None and concept is None and match != "meaning"):
                raise StoreError("a state is unlinked from one thing, one concept, or (match=meaning) from meaning matches")
        elif not (thing and other_thing) or concept is not None:
            raise StoreError("without a state, unlink two things")
        concept = " ".join(concept.split()) if concept else None
        with self._tx(actor) as db:
            return db.execute("INSERT INTO unlinks (state_id, thing_id, other_thing_id, concept, match, actor, reason, created_utc) VALUES (?,?,?,?,?,?,?,?)",
                              (state_id, ids.get("thing"), ids.get("other"), concept, match, actor, reason.strip(), now())).lastrowid

    def relink(self, unlink_id, reason, *, actor) -> None:
        if not reason or not reason.strip():
            raise StoreError("a relink needs its reason")
        row = self.db.execute("SELECT removed_utc FROM unlinks WHERE id = ?", (unlink_id,)).fetchone()
        if row is None or row[0] is not None:
            raise StoreError(f"unlink {unlink_id} is not active")
        with self._tx(actor) as db:
            db.execute("UPDATE unlinks SET removed_utc = ?, removed_reason = ? WHERE id = ?", (now(), reason.strip(), unlink_id))

    def unlinks(self, *, active=True, state_id=None) -> list[Unlink]:
        rows = self.db.execute("SELECT id, state_id, thing_id, other_thing_id, concept, match, actor, reason, created_utc, removed_utc FROM unlinks "
                               "WHERE (? = 0 OR removed_utc IS NULL) AND (? IS NULL OR state_id = ?) ORDER BY id", (1 if active else 0, state_id, state_id))
        return [Unlink(*r) for r in rows]

    def revert_param(self, name, *, actor) -> float:
        row = self.db.execute("SELECT previous FROM param_history WHERE name = ? ORDER BY id DESC LIMIT 1", (name,)).fetchone()
        if not row or row[0] is None:
            raise StoreError(f"{name} has no earlier value to revert to")
        self.set_param(name, row[0], "reverted", actor=actor)
        return row[0]

    def param_history(self, name) -> list[tuple[int, float, str, str]]:
        return [tuple(r) for r in self.db.execute("SELECT id, value, reason, actor FROM param_history WHERE name = ? ORDER BY id", (name,))]

    def record_injection(self, turn_ref, source, hook_event, kind, text) -> None:
        if source not in ("legacy", "statekeeper"):
            raise StoreError("source must be legacy or statekeeper")
        self.db.execute("INSERT INTO shadow_injections (turn_ref, source, hook_event, kind, text, created_utc) VALUES (?,?,?,?,?,?)", (turn_ref, source, hook_event, kind, _utf8(text), now()))

    def injections(self, *, turn_ref=None, source=None) -> list[tuple[str, str, str, str, str, str]]:
        """(turn_ref, source, hook_event, kind, text, created_utc), oldest first."""
        return [tuple(r) for r in self.db.execute("SELECT turn_ref, source, hook_event, kind, text, created_utc FROM shadow_injections WHERE (? IS NULL OR turn_ref = ?) AND (? IS NULL OR source = ?) ORDER BY id",
                                                  (turn_ref, turn_ref, source, source))]

    def map_import(self, source, source_id, state_id) -> None:
        self.db.execute("INSERT OR IGNORE INTO import_map (source, source_id, state_id) VALUES (?,?,?)", (source, source_id, state_id))

    def imported(self, source) -> dict[int, int]:
        """source_id -> state_id for everything an import already brought in."""
        return {r[0]: r[1] for r in self.db.execute("SELECT source_id, state_id FROM import_map WHERE source = ?", (source,))}

    def link_states(self, a_id, b_id, relation, score, *, actor) -> bool:
        """Links two states (a is the survivor/older for `same`). Returns False if that link already exists."""
        if relation not in ("same", "related"):
            raise StoreError("relation must be same or related")
        if not (self.get_state(a_id) and self.get_state(b_id)) or a_id == b_id:
            raise StoreError("both states must exist and differ")
        a, b = (a_id, b_id) if relation == "same" else (min(a_id, b_id), max(a_id, b_id))
        cur = self.db.execute("INSERT OR IGNORE INTO state_links (a_state, b_state, relation, score, actor, created_utc) VALUES (?,?,?,?,?,?)", (a, b, relation, score, actor, now()))
        return cur.rowcount > 0

    def links_of(self, state_id) -> list[tuple[int, int, str, float]]:
        return [tuple(r) for r in self.db.execute("SELECT a_state, b_state, relation, score FROM state_links WHERE a_state = ? OR b_state = ? ORDER BY id", (state_id, state_id))]

    def reopen(self, state_id, reason, *, actor) -> State:
        """Undoes a rejection (an unmerge) or a close: a rejected state goes back to proposed, a closed or superseded one stands again, with the reason in
        its resolution. The owner's reversal of a consolidation or a close."""
        st = self.get_state(state_id)
        if not reason.strip():
            raise StoreError("reopening needs its reason")
        if st and st.superseded_by is not None and st.status in ("active", "proposed"):
            with self._tx(actor) as db:
                db.execute("UPDATE states SET superseded_by = NULL, resolution = ? WHERE id = ?", (f"reopened {now()[:10]}: {reason.strip()}", state_id))
            return self.get_state(state_id)  # type: ignore[return-value]
        if not st or st.status != "rejected":
            raise StoreError(f"#{state_id} is not rejected or closed")
        with self._tx(actor) as db:
            db.execute("UPDATE states SET status = 'proposed', resolution = ? WHERE id = ?", (f"reopened {now()[:10]}: {reason.strip()}", state_id))
        return self.get_state(state_id)  # type: ignore[return-value]

    def record_turn_text(self, turn_ref, kind, text, *, session_id=None) -> None:
        text = _utf8(text)
        self.db.execute("INSERT OR REPLACE INTO turn_texts (turn_ref, kind, session_id, created_utc, text) VALUES (?,?,?,?,?)", (turn_ref, kind, session_id, now(), text))

    def turn_texts(self, kind, *, after=None) -> list[tuple[str, str | None, str, str]]:
        """(turn_ref, session_id, created_utc, text), oldest first."""
        return [tuple(r) for r in self.db.execute("SELECT turn_ref, session_id, created_utc, text FROM turn_texts WHERE kind = ? AND (? IS NULL OR created_utc > ?) ORDER BY created_utc",
                                                  (kind, after, after))]

    def add_evidence(self, state_id, evidence, *, actor) -> None:
        """Adds another piece of evidence to an existing state (the same source twice is ignored)."""
        if not self.get_state(state_id):
            raise StoreError(f"no state #{state_id}")
        with self._tx(actor) as db:
            db.execute("INSERT OR IGNORE INTO evidence (state_id, ref_type, ref, excerpt, sha256, recorded_utc) VALUES (?,?,?,?,?,?)",
                       (state_id, evidence.ref_type, evidence.ref, evidence.excerpt, evidence.sha256, now()))

    def flag_state(self, state_id, reason, *, actor) -> int:
        """Raises a question about a state without changing it (e.g. a proposal that disputes a standing one). Returns the flag id."""
        if not self.get_state(state_id):
            raise StoreError(f"no state #{state_id}")
        with self._tx(actor) as db:
            return db.execute("INSERT INTO flags (state_id, flagged_by, reason, created_utc) VALUES (?,?,?,?)", (state_id, actor, reason, now())).lastrowid

    def open_flags(self) -> list[tuple[int, str, str]]:
        """(state_id, reason, flagged_by) for every unresolved flag."""
        return [tuple(r) for r in self.db.execute("SELECT state_id, reason, flagged_by FROM flags WHERE resolved_utc IS NULL ORDER BY id")]

    def events(self, after=0, limit=1000, *, thing_id=None) -> list[Event]:
        rows = self.db.execute("SELECT seq, event_type, thing_id, state_id, actor, payload, recorded_utc FROM events WHERE seq > ? AND (? IS NULL OR thing_id = ?) ORDER BY seq LIMIT ?",
                               (after, thing_id, thing_id, limit))
        return [Event(*r) for r in rows]

    # ---- vectors ----
    def put_vector(self, owner_type, owner_id, model, vector, content_hash) -> None:
        from .embed import to_blob
        self.db.execute("INSERT INTO embeddings (owner_type, owner_id, model, dims, vector, content_hash, updated_utc) VALUES (?,?,?,?,?,?,?) "
                        "ON CONFLICT(owner_type, owner_id, model) DO UPDATE SET dims=excluded.dims, vector=excluded.vector, content_hash=excluded.content_hash, updated_utc=excluded.updated_utc",
                        (owner_type, owner_id, model, len(vector), to_blob(vector), content_hash, now()))

    def add_questions(self, state_id, texts, *, writer) -> int:
        """Requests the state answers (see schema: state_questions). Returns how many were new."""
        n = 0
        for t in texts:
            t = " ".join(str(t).split())[:200]
            if t:
                n += self.db.execute("INSERT OR IGNORE INTO state_questions (state_id, text, writer, created_utc) VALUES (?,?,?,?)",
                                     (state_id, t, writer, now())).rowcount
        return n

    def questions(self) -> list[tuple[int, int, str]]:
        """(question id, state id, text) for every standing state's questions."""
        return [tuple(r) for r in self.db.execute("SELECT q.id, q.state_id, q.text FROM state_questions q JOIN states s ON s.id = q.state_id "
                                                  "WHERE s.superseded_by IS NULL AND s.status IN ('active', 'proposed') ORDER BY q.id")]

    def vectors(self, owner_type, model) -> list[dict]:
        from .embed import from_blob
        return [{"owner_id": r[0], "vector": from_blob(r[1]), "content_hash": r[2]}
                for r in self.db.execute("SELECT owner_id, vector, content_hash FROM embeddings WHERE owner_type = ? AND model = ?", (owner_type, model))]
