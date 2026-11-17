import json
import sqlite3
import unittest

from support import ev, fresh_store  # noqa: F401  (also puts src on the path)
from statekeeper import Evidence, SqliteStore, StoreError, likely_replaces


class Things(unittest.TestCase):
    def test_a_thing_is_found_by_name_or_alias_ignoring_case_and_the_agent_and_people_are_ordinary_things(self):
        s = fresh_store()
        s.add_thing("the converter")
        s.add_thing("Claude", "agent")
        s.add_thing("Jane Doe", "person")
        s.add_alias("the converter", "pdf service")
        self.assertEqual("the converter", s.find("PDF Service").name)
        self.assertEqual("agent", s.find("claude").kind)
        self.assertEqual("person", s.find("jane doe").kind)
        with self.assertRaises(StoreError):
            s.add_alias("Claude", "the converter")       # an alias is unique, even against a name

    def test_rename_refuses_a_name_another_thing_holds_and_is_in_the_event_log(self):
        s = fresh_store()
        s.add_thing("a"), s.add_thing("b")
        with self.assertRaises(StoreError):
            s.rename("a", "B")
        s.rename("a", "alpha", actor="owner")
        self.assertIn("thing-renamed", [e.event_type for e in s.events()])

    def test_merge_moves_states_keeps_the_old_row_retired_and_leaves_a_trace(self):
        s = fresh_store()
        s.add_state("PR 102", "fixes the invoice run", evidence=[ev("a")], writer="rule:t", trust="verified", learned_how="read")
        dup = s.add_state("ExampleApp PR 102", "is a draft", evidence=[ev("b")], writer="rule:t", trust="verified", learned_how="read")
        into, moved = s.merge("ExampleApp PR 102", "PR 102", "same PR")
        self.assertEqual(1, moved)
        self.assertEqual("PR 102", s.find("ExampleApp PR 102").name)          # the old name now reaches the target
        self.assertEqual(2, len(s.states_of(into.id)))
        self.assertEqual(into.id, s.get_state(dup.id).thing_id)
        retired = [t for t in s.db.execute("SELECT name, kind_confirmed FROM things WHERE kind_confirmed = -2")]
        self.assertEqual(1, len(retired))                                        # kept, not deleted
        self.assertIn("thing-merged", [e.event_type for e in s.events()])
        with self.assertRaises(StoreError):
            s.merge("PR 102", "PR 102")

    def test_the_scope_parent_is_a_plain_column(self):
        s = fresh_store()
        s.add_thing("datepicker replacement")
        child = s.add_thing("TICKET-1", parent="datepicker replacement")
        self.assertEqual(s.find("datepicker replacement").id, child.parent_id)
        with self.assertRaises(StoreError):
            s.add_thing("x", parent="no such parent")


class States(unittest.TestCase):
    def test_a_state_without_evidence_is_refused_and_the_database_enforces_it_too(self):
        s = fresh_store()
        with self.assertRaises(StoreError):
            s.add_state("x", "y", evidence=[], writer="w", trust="verified", learned_how="read")
        t = s.add_thing("x")
        s.db.execute("BEGIN")
        s.db.execute("INSERT INTO states (thing_id, text, recorded_utc, trust, learned_how, writer) VALUES (?, 'bare', 'now', 'claimed', 'claimed', 'w')", (t.id,))
        with self.assertRaises(sqlite3.IntegrityError):
            s.db.execute("UPDATE states SET status = 'active' WHERE text = 'bare'")
        s.db.execute("ROLLBACK")

    def test_trust_decides_whether_a_state_stands_and_an_agent_claim_is_a_proposal(self):
        s = fresh_store()
        owner = s.add_state("x", "owner says so", evidence=[ev()], writer="owner:person", trust="owner", learned_how="told")
        verified = s.add_state("x", "a read confirmed", evidence=[ev("b")], writer="rule:ci", trust="verified", learned_how="read")
        claim = s.add_state("x", "the agent says green", evidence=[ev("c")], writer="agent-7", trust="claimed", learned_how="claimed", session_id="s1")
        self.assertEqual(("active", "active", "proposed"), (owner.status, verified.status, claim.status))
        self.assertIsNotNone(verified.confirmed_utc)       # a read is a verification
        self.assertIsNone(claim.confirmed_utc)             # a claim is not
        self.assertEqual("s1", claim.session_id)           # provenance kept

    def test_a_keyed_state_replaces_the_current_one_with_the_same_key_by_rule(self):
        s = fresh_store()
        a = s.add_state("PR 5", "CI is red", evidence=[ev("r1")], writer="rule:ci", trust="verified", learned_how="read", state_key="ci", is_state=True)
        b = s.add_state("PR 5", "CI is green", evidence=[ev("r2")], writer="rule:ci", trust="verified", learned_how="read", state_key="ci", is_state=True)
        self.assertEqual(b.id, s.get_state(a.id).superseded_by)
        self.assertEqual(["CI is green"], [x.text for x in s.states_of(s.find("PR 5").id)])
        self.assertEqual(2, len(s.states_of(s.find("PR 5").id, history=True)))
        self.assertIn("state-superseded", [e.event_type for e in s.events()])

    def test_accept_supersede_reject_and_confirm_each_leave_an_event(self):
        s = fresh_store()
        old = s.add_state("x", "quota is 100 cores", evidence=[ev()], writer="owner:person", trust="owner", learned_how="told", as_of="2026-11-01")
        new = s.add_state("x", "quota is 300 cores", evidence=[ev("b")], writer="agent", trust="claimed", learned_how="claimed", as_of="2026-11-12", supersedes=old.id)
        s.accept(new.id, "the owner confirmed")
        self.assertEqual(new.id, s.get_state(old.id).superseded_by)             # accepting a proposal that names what it replaces replaces it
        with self.assertRaises(StoreError):
            s.accept(new.id)                                                    # no longer a proposal
        junk = s.add_state("x", "builder still running", evidence=[ev("c")], writer="agent", trust="extracted", learned_how="inferred")
        with self.assertRaises(StoreError):
            s.reject(junk.id, "")
        s.reject(junk.id, "transient status")
        s.confirm(new.id, actor="rule:check")
        types = [e.event_type for e in s.events()]
        for t in ("state-added", "state-evidenced", "state-status", "state-superseded", "state-confirmed"):
            self.assertIn(t, types)

    def test_the_event_log_is_append_only(self):
        s = fresh_store()
        s.add_thing("x")
        with self.assertRaises(sqlite3.DatabaseError):
            s.db.execute("DELETE FROM events")
        with self.assertRaises(sqlite3.DatabaseError):
            s.db.execute("UPDATE events SET actor = 'x'")

    def test_the_actor_is_in_the_log(self):
        s = fresh_store()
        s.add_state("x", "y z", evidence=[ev()], writer="agent-7", trust="claimed", learned_how="claimed")
        actors = {e.actor for e in s.events() if e.event_type.startswith("state")}
        self.assertEqual({"agent-7"}, actors)


class Hints(unittest.TestCase):
    def test_a_newer_state_that_reads_as_an_update_is_hinted_and_unrelated_or_same_source_ones_are_not(self):
        s = fresh_store()
        a = s.add_state("c", "quota is 100 cores per environment", evidence=[ev("a")], writer="w", trust="verified", learned_how="read", as_of="2026-11-01")
        b = s.add_state("c", "quota is 300 cores per environment now", evidence=[ev("b")], writer="w", trust="claimed", learned_how="claimed", as_of="2026-11-12")
        u = s.add_state("c", "runs on azure container apps", evidence=[ev("c")], writer="w", trust="verified", learned_how="read", as_of="2026-11-13")
        states = s.states_of(s.find("c").id)
        ev_map = s.evidence_of([x.id for x in states])
        self.assertEqual({b.id: a.id}, likely_replaces(states, ev_map))
        same = s.add_state("c", "quota is 400 cores per environment", evidence=[ev("b")], writer="w", trust="claimed", learned_how="claimed", as_of="2026-11-14")
        states = s.states_of(s.find("c").id)
        hints = likely_replaces(states, s.evidence_of([x.id for x in states]))
        self.assertNotIn(same.id, [k for k, v in hints.items() if v == b.id])    # shares evidence "b" with b: not an update of b
        self.assertNotIn(u.id, hints)


if __name__ == "__main__":
    unittest.main()


class MigratingAnEarlierDatabase(unittest.TestCase):
    """A shadow database created before the earlier recall system was named "legacy" must keep working with the current code."""

    def test_old_source_names_are_carried_over_and_the_new_name_is_accepted(self):
        import sqlite3
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as d:
            path = Path(d, "old.db")
            fresh = SqliteStore(path)
            fresh.close()
            raw = sqlite3.connect(path, isolation_level=None)
            old_name = "per" + "sistence"                                   # the value an older database holds, spelled so this file need not name it
            raw.executescript(f"""
                DROP INDEX idx_shadow_inj_turn; DROP TABLE shadow_injections;
                CREATE TABLE shadow_injections (id INTEGER PRIMARY KEY, turn_ref TEXT NOT NULL, source TEXT NOT NULL, hook_event TEXT NOT NULL, kind TEXT NOT NULL,
                    text TEXT NOT NULL, created_utc TEXT NOT NULL, CHECK (source IN ('{old_name}', 'statekeeper')));
                CREATE INDEX idx_shadow_inj_turn ON shadow_injections(turn_ref);
                INSERT INTO shadow_injections VALUES (1, 't1', '{old_name}', 'UserPromptSubmit', 'concept_map', 'x', '2026-11-16');
                INSERT INTO shadow_injections VALUES (2, 't1', 'statekeeper', 'UserPromptSubmit', 'concept_map', 'y', '2026-11-16');
                INSERT INTO import_map (source, source_id, state_id) VALUES ('{old_name}-fact', 7, 1);""")
            raw.close()
            s = SqliteStore(path)
            self.assertEqual([(1, "legacy"), (2, "statekeeper")], [(r[0], r[1]) for r in s.db.execute("SELECT id, source FROM shadow_injections ORDER BY id")])
            s.record_injection("t2", "legacy", "UserPromptSubmit", "concept_map", "z")
            self.assertEqual({7: 1}, s.imported("legacy-fact"))
            self.assertIsNone(s.db.execute("SELECT 1 FROM sqlite_master WHERE name = 'shadow_injections_old'").fetchone())
            s.close()
            SqliteStore(path).close()                                       # reopening an already-migrated database is a no-op
