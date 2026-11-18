import unittest

from support import ev, fresh_store
from statekeeper.promote import promote


def proposal(s, thing, text, *evs):
    return s.add_state(thing, text, evidence=list(evs) or [ev()], writer="extractor:x", trust="extracted", learned_how="inferred")


class Promote(unittest.TestCase):
    def test_two_sources_a_merged_duplicate_or_repeated_use_promote_a_proposal(self):
        s = fresh_store()
        a = proposal(s, "tool", "tool runs read-only", ev("m1"), ev("m2"))
        b = proposal(s, "tool", "tool needs a token")
        d = proposal(s, "tool", "tool needs a token!")
        s.link_states(b.id, d.id, "same", 0.97, actor="t")
        c = proposal(s, "other", "other caps at 50")
        for t in ("t1", "t2"):
            s.log_outcome(c.id, "used", source="t", turn_ref=t)
        lone = proposal(s, "other", "other is new")
        got = dict(promote(s))
        self.assertIn(a.id, got)
        self.assertIn("2 distinct sources", got[a.id])
        self.assertIn(b.id, got)
        self.assertIn(c.id, got)
        self.assertNotIn(lone.id, got)
        self.assertEqual("active", s.get_state(a.id).status)
        self.assertEqual("extracted", s.get_state(a.id).trust)
        self.assertEqual("proposed", s.get_state(lone.id).status)

    def test_one_use_is_not_enough_and_a_contradiction_or_flag_blocks_it(self):
        s = fresh_store()
        a = proposal(s, "tool", "tool runs read-only")
        s.log_outcome(a.id, "used", source="t", turn_ref="t1")
        b = proposal(s, "tool", "tool needs a token", ev("m1"), ev("m2"))
        s.log_outcome(b.id, "contradicted", source="t", turn_ref="t2")
        c = proposal(s, "tool", "tool is slow", ev("m1"), ev("m3"))
        s.flag_state(c.id, "disputes #1", actor="t")
        self.assertEqual([], promote(s))

    def test_dry_run_changes_nothing(self):
        s = fresh_store()
        a = proposal(s, "tool", "tool runs read-only", ev("m1"), ev("m2"))
        self.assertEqual(1, len(promote(s, apply=False)))
        self.assertEqual("proposed", s.get_state(a.id).status)


if __name__ == "__main__":
    unittest.main()
