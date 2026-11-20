import unittest

from support import ev, fresh_store
from statekeeper.topics import topic_index, topic_states, tree


class Topics(unittest.TestCase):
    def setUp(self):
        self.s = fresh_store()

    def add(self, thing, text, **kw):
        return self.s.add_state(thing, text, evidence=[ev(f"m-{text[:8]}", text)], writer="w", trust="verified", learned_how="read", **kw)

    def test_a_thing_sits_under_another_whose_whole_name_its_name_contains(self):
        for name in ("receipting", "receipting jobs", "receipting security review", "Receipt Date", "Receipt Date backfill", "qa", "qa run"):
            self.add(name, f"something about {name}")
        tt = tree(self.s)
        under = {tt.things[c].name for c in tt.descendants(self.s.find("receipting").id)}
        self.assertEqual({"receipting jobs", "receipting security review"}, under)
        self.assertIn("Receipt Date backfill", {tt.things[c].name for c in tt.descendants(self.s.find("Receipt Date").id)})
        self.assertEqual(set(), tt.descendants(self.s.find("qa").id))        # a short word is not a topic by name

    def test_the_index_names_topics_with_sub_areas_and_skips_run_logs(self):
        for name in ("receipting", "receipting jobs", "receipting permissions"):
            for i in range(4):
                self.add(name, f"{name} fact {i}")
        for n in (73001, 73002, 73003):
            self.add(f"crmv2-unified-build #{n}", "green")
        self.add("crmv2-unified-build", "the pipeline")
        idx = topic_index(self.s, min_states=4)
        self.assertIn("receipting 12", idx)
        self.assertNotIn("crmv2-unified-build", idx)

    def test_a_topic_lists_its_sub_areas_states_newest_first(self):
        self.add("receipting", "older fact", as_of="2026-11-01")
        self.add("receipting jobs", "newer fact", as_of="2026-11-10")
        t, states = topic_states(self.s, "Receipting")
        self.assertEqual("receipting", t.name)
        self.assertEqual(["newer fact", "older fact"], [s.text for s in states])


if __name__ == "__main__":
    unittest.main()
