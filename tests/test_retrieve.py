import unittest
from datetime import datetime, timezone

from support import ThemeEmbedder, ev, fresh_store
from statekeeper import surface
from statekeeper.retrieve import fuzzy_match, words


class Fuzzy(unittest.TestCase):
    def test_a_typo_or_a_spacing_is_a_near_spelling_but_a_truncation_or_prefix_is_not(self):
        self.assertIsNotNone(fuzzy_match(words("the datepiker broke"), "datepicker"))
        self.assertIsNotNone(fuzzy_match(words("janedoe said"), "Jane Doe"))
        self.assertIsNone(fuzzy_match(words("use claud whatever as the address"), "Claude"))
        self.assertIsNone(fuzzy_match(words("our example org address"), "ExampleApp"))
        self.assertIsNone(fuzzy_match(words("spelled whole datepicker"), "datepicker"))   # the exact match's, not fuzzy
        self.assertIsNone(fuzzy_match(words("tiny"), "tin"))                              # short labels are never fuzzy


class Surfacing(unittest.TestCase):
    def setUp(self):
        self.s = fresh_store()
        self.emb = ThemeEmbedder()
        self.today = datetime(2026, 11, 16, tzinfo=timezone.utc)

    def add(self, thing, text, trust="verified", **kw):
        return self.s.add_state(thing, text, evidence=[ev(f"m-{text[:8]}", f"source for {text}")], writer="w", trust=trust,
                                learned_how="read" if trust == "verified" else "claimed", **kw)

    def test_a_named_thing_surfaces_with_its_states_newest_first_and_a_freshness_label(self):
        self.add("the datepicker", "is flatpickr now", as_of="2026-11-10")
        self.add("the datepicker", "was bootstrap before", as_of="2026-11-01")
        out = surface(self.s, "why is the datepicker slow?", None, today=self.today)
        self.assertIn("the datepicker (thing; named)", out.block)
        self.assertLess(out.block.index("flatpickr"), out.block.index("bootstrap"))
        self.assertIn("verified today", out.block)
        self.assertIn('message m-is flatp: "source for is flatpickr now"', out.block)

    def test_a_proposal_is_found_by_meaning_without_naming_it_and_the_matched_state_comes_first(self):
        self.add("the hub", "runs a nightly job", trust="claimed")
        self.add("the hub", "caps cores at the quota for pdf render scaling", trust="claimed")
        out = surface(self.s, "our cores got upped, how does the quota change pdf scaling", self.emb, today=self.today)
        self.assertIn("the hub (thing; meaning", out.block)
        self.assertLess(out.block.index("caps cores"), out.block.index("nightly job"))
        self.assertIn("agent-claimed, unverified", out.block)
        self.assertIn("proposed", out.block)

    def test_nothing_bears_on_an_input_so_nothing_is_surfaced_and_an_embedder_failure_leaves_names_working(self):
        self.add("the hub", "caps cores at the quota for pdf render scaling", trust="claimed")
        self.assertEqual("", surface(self.s, "lunch on friday?", self.emb).block)
        self.emb.fails = True
        self.assertEqual("", surface(self.s, "the pdf quota", self.emb).block)               # meaning is down, nothing named
        self.add("Legacy", "is the project")
        self.assertIn("Legacy", surface(self.s, "about Legacy", self.emb).block)   # names still work

    def test_a_newer_state_that_updates_an_older_one_says_so(self):
        self.add("the quota", "is 100 cores per environment", as_of="2026-11-01")
        new = self.add("the quota", "is 300 cores per environment now", trust="claimed", as_of="2026-11-12")
        out = surface(self.s, "the quota", None, today=self.today)
        self.assertIn(f"newer than #", out.block)
        self.assertLess(out.block.index("300 cores"), out.block.index("100 cores"))
        self.assertEqual(new.id, out.items[0].states[0].id)

    def test_the_block_is_capped_and_says_what_it_left_out(self):
        for i in range(6):
            self.add(f"thing-number-{i}", f"state {i} " + "padding " * 20)
        out = surface(self.s, " ".join(f"thing-number-{i}" for i in range(6)), None, max_things=2, today=self.today)
        self.assertEqual(2, len(out.items))
        self.assertIn("omitted", out.block)
        small = surface(self.s, "thing-number-0 thing-number-1", None, budget=300, today=self.today)
        self.assertLessEqual(len(small.block), 600)

    def test_a_retired_thing_is_never_surfaced_and_its_name_reaches_the_target(self):
        self.add("PR 102", "fixes the run")
        self.add("ExampleApp PR 102", "is draft")
        self.s.merge("ExampleApp PR 102", "PR 102")
        out = surface(self.s, "ExampleApp PR 102 status", None, today=self.today)
        self.assertEqual(["PR 102"], [i.thing.name for i in out.items])
        self.assertIn("is draft", out.block)


class Currency(unittest.TestCase):
    def test_a_live_state_not_re_verified_for_hours_is_labelled_stale_with_how_to_re_check(self):
        s = fresh_store()
        s.add_state("PR 5", "CI is green", evidence=[ev("a")], writer="rule:ci", trust="verified", learned_how="read", state_key="pr.ci", volatility="live",
                    verify_handle="gh pr view 5")
        from datetime import timedelta
        out = surface(s, "PR 5", None, today=datetime.now(timezone.utc) + timedelta(hours=20))
        self.assertIn("STALE", out.block)
        self.assertIn("gh pr view 5", out.block)

    def test_a_fresh_live_state_says_how_recently_it_was_verified(self):
        s = fresh_store()
        s.add_state("PR 5", "CI is green", evidence=[ev("a")], writer="rule:ci", trust="verified", learned_how="read", state_key="pr.ci", volatility="live")
        out = surface(s, "PR 5", None)
        self.assertIn("verified", out.block)
        self.assertNotIn("STALE", out.block)


class Quiet(unittest.TestCase):
    def test_excluded_kinds_and_names_and_recently_surfaced_states_stay_out(self):
        s = fresh_store()
        s.add_thing("Claude", "agent")
        s.add_state("Claude", "works carefully", evidence=[ev("a")], writer="rule:t", trust="verified", learned_how="read")
        s.add_state("working note", "doing things", evidence=[ev("b")], writer="rule:t", trust="verified", learned_how="read")
        seen = s.add_state("the converter", "caps replicas at 75", evidence=[ev("c")], writer="rule:t", trust="verified", learned_how="read")
        text = "Claude working note the converter"
        out = surface(s, text, None, exclude_kinds=("agent", "person"), exclude_names=("working note",))
        self.assertEqual(["the converter"], [i.thing.name for i in out.items])
        self.assertEqual("", surface(s, text, None, exclude_kinds=("agent",), exclude_names=("working note",), skip_state_ids=frozenset({seen.id})).block)


if __name__ == "__main__":
    unittest.main()
