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
        self.assertIsNone(fuzzy_match(words("look at ExampleApp PR 3345"), "ExampleApp PR 3351"))   # numbers are identifiers, never typos


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
        self.assertIn("[verified today · msg m-is flatp]", out.block)   # one marker, one short pointer
        self.assertNotIn("source for", out.block)                        # no evidence quotes

    def test_a_proposal_is_found_by_meaning_and_only_the_state_that_bears_on_the_input_is_shown(self):
        self.add("the hub", "runs a nightly job", trust="claimed")
        self.add("the hub", "caps cores at the quota for pdf render scaling", trust="claimed")
        out = surface(self.s, "our cores got upped, how does the quota change pdf scaling", self.emb, today=self.today)
        self.assertIn("the hub (thing; meaning", out.block)
        self.assertIn("caps cores", out.block)
        self.assertNotIn("nightly job", out.block)          # same thing, nothing to do with the input
        self.assertIn("[agent-claimed", out.block)
        self.assertNotIn("proposed", out.block)             # one marker per line, not a pile of tags

    def test_nothing_bears_on_an_input_so_nothing_is_surfaced_and_an_embedder_failure_leaves_names_working(self):
        self.add("the hub", "caps cores at the quota for pdf render scaling", trust="claimed")
        self.assertEqual("", surface(self.s, "lunch on friday?", self.emb).block)
        self.emb.fails = True
        self.assertEqual("", surface(self.s, "the pdf quota", self.emb).block)               # meaning is down, nothing named
        self.add("Legacy", "is the project")
        self.assertIn("Legacy", surface(self.s, "about Legacy", self.emb).block)   # names still work

    def test_a_newer_state_that_updates_an_older_one_replaces_it_at_injection_time(self):
        self.add("the quota", "is 100 cores per environment", as_of="2026-11-01")
        new = self.add("the quota", "is 300 cores per environment now", trust="claimed", as_of="2026-11-12")
        out = surface(self.s, "the quota", None, today=self.today)
        self.assertIn("300 cores", out.block)
        self.assertNotIn("100 cores", out.block)
        self.assertEqual([new.id], [s.id for s in out.items[0].states])

    def test_a_newer_state_about_the_same_subject_on_another_thing_replaces_the_older_one(self):
        self.add("the rollout", "pdf render rollout at 10 percent of orgs", as_of="2026-11-06")
        self.add("Jane", "bumped the pdf render rollout to 50 percent of orgs", as_of="2026-11-17")
        out = surface(self.s, "where is the pdf render rollout at", self.emb, today=self.today)
        self.assertIn("50 percent", out.block)
        self.assertNotIn("10 percent", out.block)

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


class Relevance(unittest.TestCase):
    """A thing's name alone is not enough: the state itself has to bear on what the input asks."""
    def setUp(self):
        self.s = fresh_store()
        self.emb = ThemeEmbedder()
        self.today = datetime(2026, 11, 16, tzinfo=timezone.utc)

    def add(self, thing, text, **kw):
        return self.s.add_state(thing, text, evidence=[ev(f"m-{text[:12]}")], writer="w", trust="verified", learned_how="read", **kw)

    def test_naming_a_generic_thing_surfaces_nothing_unless_one_of_its_states_bears_on_the_input(self):
        for i in range(8):
            self.add("TheRepo", f"worktree cleanup fact number {i} about archived sessions")
        self.add("TheRepo", "the copilot review loop runs before every push")
        self.assertEqual("", surface(self.s, "TheRepo has merge conflicts after the datepicker merge", self.emb, today=self.today).block)
        out = surface(self.s, "TheRepo copilot review loop findings", self.emb, today=self.today)
        self.assertIn("copilot review loop", out.block)
        self.assertNotIn("worktree cleanup", out.block)

    def test_a_state_that_only_points_at_a_named_thing_or_a_near_spelling_needs_meaning_too(self):
        self.add("Polly retry policy", "re-enters the whole send-email batch", object="Service Bus")
        self.add("Service Bus", "lock loss redelivers the batch")
        self.assertNotIn("re-enters", surface(self.s, "the Service Bus namespace", self.emb, today=self.today).block)   # object named only
        self.assertEqual("", surface(self.s, "the service just failed", self.emb, today=self.today).block)            # "service just" ~ "servicebus"

    def test_a_close_spelling_of_a_specific_thing_names_it(self):
        self.add("Datepicker", "specs only pass after a real dotnet build")
        self.assertIn("real dotnet build", surface(self.s, "check every date picker in the app", self.emb, today=self.today).block)

    def test_a_name_many_states_mention_in_passing_is_generic_however_few_states_it_has(self):
        self.add("board", "points on board issues are Fibonacci numbers")
        for i in range(20):
            self.add(f"item {i}", f"item {i} sits on the board in column {i}")
        self.assertNotIn("Fibonacci", surface(self.s, "PR 7 approved, update the board", None, today=self.today).block)

    def test_naming_a_specific_thing_shows_its_states(self):
        self.add("Acme", "import fix not yet confirmed in prod")
        out = surface(self.s, "are the Acme PRs ready?", self.emb, today=self.today)
        self.assertIn("import fix", out.block)

    def test_a_persons_learned_fact_surfaces_by_meaning_but_not_by_name_and_core_rules_are_not_repeated(self):
        self.s.add_thing("Jane", "person")
        self.add("Jane", "set the pdf render quota scaling to 70 cores")
        self.add("Jane", "prefers numbered questions", volatility="rule")
        out = surface(self.s, "what is the pdf render quota scaling now", self.emb, today=self.today, exclude_kinds=("agent", "person"))
        self.assertIn("70 cores", out.block)
        self.assertEqual("", surface(self.s, "Jane numbered questions", None, today=self.today, exclude_kinds=("agent", "person")).block)

    def test_config_keys_are_read_as_the_words_people_say(self):
        from statekeeper.retrieve import split_identifiers, terms
        self.assertEqual("Max Slot Wait Seconds", split_identifiers("MaxSlotWaitSeconds"))
        self.assertTrue({"maxslotwaitseconds", "seconds", "fairness"} <= terms("set Fairness:MaxSlotWaitSeconds to 30"))
        self.assertNotIn("hub", terms("GitHub CLI"))                          # short identifier parts are too common to count
        self.assertNotIn("there", terms("there slot"))

    def test_a_question_about_agents_gets_one_line_of_fresh_agent_status(self):
        from datetime import timedelta
        now = datetime.now(timezone.utc)
        for name, status in (("agent-a1", "working"), ("agent-a2", "silent"), ("agent-a3", "finished")):
            self.s.add_state(name, status, evidence=[ev(name)], writer="rule:agent-status", trust="verified", learned_how="read", state_key="agent.status", volatility="live")
        out = surface(self.s, "are the agents running fine?", None, today=now)
        self.assertIn("working 1 (agent-a1)", out.block)
        self.assertIn("silent 1 (agent-a2)", out.block)
        self.assertIn("finished 1", out.block)
        self.assertEqual("", surface(self.s, "are the agents running fine?", None, today=now + timedelta(hours=12)).block)   # stale: nothing
        self.assertEqual("", surface(self.s, "lunch?", None, today=now).block)
        self.assertEqual("", surface(self.s, "have agents do reviews of the PR", None, today=now).block)   # agents mentioned, status not asked
        self.assertEqual("", surface(self.s, "since you are running a local agent anyway", None, today=now).block)
        rule = self.s.add_state("shepherd agents", "are not resumable after a restart: dispatch a fresh one", evidence=[ev("r")], writer="w", trust="verified",
                                learned_how="read", kind="rule", volatility="rule")
        self.assertIn(f"#{rule.id} are not resumable", surface(self.s, "are the agents running fine?", None, today=now, log_turn="t-agents").block)
        from statekeeper.report import build_report
        self.assertGreater(build_report(self.s)["outcomes_by_kind"]["surfaced"], 0)      # the outcome report reads what the agents line logged

    def test_a_pasted_pr_link_names_the_pr(self):
        self.s.add_state("ExampleApp PR 7", "open", evidence=[ev("p")], writer="rule:github-pr", trust="verified", learned_how="read", state_key="pr.state", volatility="live")
        out = surface(self.s, "any activity on https://github.com/acme/ExampleApp/pull/7 yet?", None, today=datetime.now(timezone.utc))
        self.assertIn("state: open", out.block)


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
