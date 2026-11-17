import unittest

from support import ThemeEmbedder, ev, fresh_store
from statekeeper.misses import find_changed_not_surfaced, find_restatements, log_misses

P = [("t1", "2026-11-16 10:00:00", "the pdf converter quota is too low for render scaling, what should we do about the core quota"),
     ("t2", "2026-11-16 10:05:00", "lunch on friday?"),
     ("t3", "2026-11-16 10:30:00", "again: the pdf converter quota is too low for render scaling, what should we do about the core quota")]


class Restatements(unittest.TestCase):
    def test_a_near_repeat_within_the_window_is_found_and_unrelated_turns_are_not(self):
        pairs = find_restatements(P, ThemeEmbedder(), 0.9)
        self.assertEqual([("t1", "t3")], [(a, b) for a, b, _ in pairs])

    def test_a_repeat_outside_the_window_is_not_a_restatement(self):
        far = [P[0], ("t9", "2026-11-20 10:00:00", P[2][2])]
        self.assertEqual([], find_restatements(far, ThemeEmbedder(), 0.9, hours=24))


class Logging(unittest.TestCase):
    def test_a_restated_input_with_no_stored_state_is_an_unstored_miss_and_one_with_an_unsurfaced_state_is_a_surfacing_miss(self):
        s, emb = fresh_store(), ThemeEmbedder()
        for ref, at, text in P:
            s.record_turn_text(ref, "prompt", text, session_id="s")
        counts = log_misses(s, emb, threshold=0.9, state_floor=0.5)
        self.assertEqual(1, counts["restated"])
        self.assertEqual(1, counts["restated-unstored"])
        st = s.add_state("pdf converter", "quota is too low for render scaling core", evidence=[ev()], writer="rule:t", trust="verified", learned_how="read")
        from statekeeper.embed import ensure_vectors
        ensure_vectors(s, emb)
        again = log_misses(s, emb, threshold=0.9, state_floor=0.5)
        self.assertEqual(1, again["restated-stored-not-surfaced"])
        self.assertEqual([st.id], [o.state_id for o in s.outcomes(kind="missed") if o.source == "restated-stored-not-surfaced"])

    def test_logging_twice_does_not_double_count(self):
        s, emb = fresh_store(), ThemeEmbedder()
        for ref, at, text in P:
            s.record_turn_text(ref, "prompt", text, session_id="s")
        log_misses(s, emb, threshold=0.9, state_floor=0.5)
        log_misses(s, emb, threshold=0.9, state_floor=0.5)
        self.assertEqual(2, len(s.outcomes(kind="missed")))                     # restated + unstored, once each


class Changed(unittest.TestCase):
    def test_an_omitted_state_of_a_thing_that_never_surfaced_is_not_a_changed_state_miss(self):
        s = fresh_store()
        st = s.add_state("PR 5", "CI is red", evidence=[ev("a")], writer="rule:ci", trust="verified", learned_how="read")
        s.log_outcome(st.id, "omitted", source="surface", turn_ref="t1")
        self.assertEqual([], find_changed_not_surfaced(s))

    def test_a_state_that_changed_after_the_last_time_its_thing_surfaced_and_is_omitted_now_is_a_miss(self):
        s = fresh_store()
        old = s.add_state("PR 5", "CI is red", evidence=[ev("a")], writer="rule:ci", trust="verified", learned_how="read", state_key="ci", is_state=True)
        s.log_outcome(old.id, "surfaced", source="surface", turn_ref="t1")
        new = s.add_state("PR 5", "CI is green", evidence=[ev("b")], writer="rule:ci", trust="verified", learned_how="read", state_key="ci", is_state=True)
        s.log_outcome(new.id, "omitted", source="surface", turn_ref="t2")
        got = find_changed_not_surfaced(s)
        self.assertEqual([(new.id, "t2")], [(g[0], g[1]) for g in got])


if __name__ == "__main__":
    unittest.main()
