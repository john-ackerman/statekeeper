import sqlite3
import unittest

from support import ThemeEmbedder, ev, fresh_store
from statekeeper import surface
from statekeeper.learn import suggest
from statekeeper.outcomes import used_proxy
from statekeeper.store import StoreError
from statekeeper.writers.github_pr import observe_pr
from test_writers import payload


class OutcomeLog(unittest.TestCase):
    def test_surfacing_logs_one_outcome_per_shown_state_with_why_and_the_parameters_in_force(self):
        s = fresh_store()
        st = s.add_state("the converter", "caps replicas at 75", evidence=[ev()], writer="rule:t", trust="verified", learned_how="read")
        s.set_param("relevance_floor", 0.72, "initial", actor="owner")
        surface(s, "why does the converter cap replicas?", None, log_turn="turn-1")
        rows = s.outcomes(kind="surfaced")
        self.assertEqual([(st.id, "turn-1", "surface")], [(o.state_id, o.turn_ref, o.source) for o in rows])
        self.assertIn("named", rows[0].detail)
        self.assertIn("relevance_floor=0.72", rows[0].params)

    def test_the_log_is_append_only_and_kinds_are_checked(self):
        s = fresh_store()
        st = s.add_state("x", "y z", evidence=[ev()], writer="w", trust="verified", learned_how="read")
        s.log_outcome(st.id, "used", source="proxy")
        with self.assertRaises(StoreError):
            s.log_outcome(st.id, "liked", source="proxy")
        with self.assertRaises(sqlite3.DatabaseError):
            s.db.execute("DELETE FROM outcomes")
        with self.assertRaises(sqlite3.DatabaseError):
            s.db.execute("UPDATE outcomes SET kind = 'missed'")

    def test_parameter_changes_are_logged_with_a_reason_and_can_be_reverted(self):
        s = fresh_store()
        s.set_param("relevance_floor", 0.72, "initial", actor="owner")
        s.set_param("relevance_floor", 0.75, "precision report", actor="learn")
        self.assertEqual(0.75, s.get_param("relevance_floor", 0.0))
        s.revert_param("relevance_floor", actor="owner")
        self.assertEqual(0.72, s.get_param("relevance_floor", 0.0))
        self.assertEqual([0.72, 0.75, 0.72], [h[1] for h in s.param_history("relevance_floor")])
        self.assertEqual(0.5, s.get_param("never_set", 0.5))


class ExternalTruth(unittest.TestCase):
    def test_a_deterministic_writer_logs_verified_right_and_verified_wrong_against_what_it_read(self):
        s = fresh_store()
        clock = iter(["2026-11-16 20:00:00.000+00:00", "2026-11-16 21:00:00.000+00:00", "2026-11-16 22:00:00.000+00:00"])
        observe_pr(s, "R", 1, payload(), lambda: next(clock))
        observe_pr(s, "R", 1, payload(), lambda: next(clock))
        red = [{"name": "build", "status": "COMPLETED", "conclusion": "FAILURE"}]
        observe_pr(s, "R", 1, payload(statusCheckRollup=red), lambda: next(clock))
        kinds = [(o.kind, o.source) for o in s.outcomes() if o.kind.startswith("verified")]
        self.assertEqual(5, kinds.count(("verified_right", "rule:github-pr")))        # 3 unchanged on the 2nd read, 2 unchanged on the 3rd
        self.assertEqual(1, kinds.count(("verified_wrong", "rule:github-pr")))        # the green CI, once an outside read showed red


class Proxies(unittest.TestCase):
    def test_used_means_the_output_carries_the_things_name_and_a_distinctive_word_of_the_state(self):
        s = fresh_store()
        st = s.add_state("the converter", "caps replicas at 75 per environment", evidence=[ev()], writer="w", trust="verified", learned_how="read")
        self.assertTrue(used_proxy("The converter is capped, 75 replicas per environment.", st))
        self.assertFalse(used_proxy("Nothing about that here.", st))
        self.assertFalse(used_proxy("The converter is fine.", st))

    def test_an_omitted_state_whose_thing_the_agent_then_works_on_is_a_missed_outcome(self):
        s = fresh_store()
        for i in range(5):
            s.add_state(f"thing-number-{i}", f"state number {i} padding", evidence=[ev(f"m{i}")], writer="w", trust="verified", learned_how="read")
        text = " ".join(f"thing-number-{i}" for i in range(5))
        surface(s, text, None, max_things=2, log_turn="t9")
        omitted = s.outcomes(kind="omitted")
        self.assertEqual(3, len(omitted))
        from statekeeper.outcomes import label_turn
        name = s.get(s.get_state(omitted[0].state_id).thing_id).name
        n = label_turn(s, "t9", f"I worked on {name} and its state number padding")
        self.assertEqual((1, 0), (n["missed"], n["used"]))
        self.assertEqual(["missed"], [o.kind for o in s.outcomes(turn_ref="t9") if o.kind == "missed"])


class Suggestions(unittest.TestCase):
    def test_suggestions_need_enough_evidence_and_never_apply_themselves(self):
        few = {"writers": {"extractor:x": {"verified_right": 1, "verified_wrong": 1}}, "change_intervals_days": {"live": [0.1]}, "floor": {"used": 3, "surfaced": 5}}
        self.assertEqual([], suggest(few))
        many = {"writers": {"extractor:x": {"verified_right": 10, "verified_wrong": 10}}, "change_intervals_days": {"live": [0.1] * 12}, "floor": {"used": 3, "surfaced": 50}}
        got = {x["param"]: x for x in suggest(many)}
        self.assertIn("trust.extractor:x", got)
        self.assertIn("half_life_days.live", got)
        self.assertIn("relevance_floor", got)
        self.assertTrue(all(x["applied"] is False for x in got.values()))


if __name__ == "__main__":
    unittest.main()
