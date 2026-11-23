import unittest
from datetime import datetime, timezone

from support import ev, fresh_store
from statekeeper import surface
from statekeeper.identifiers import IdentifierIndex, numbers_in_text


class Numbers(unittest.TestCase):
    def test_bare_hashed_and_cued_numbers_are_identifiers_but_years_dates_and_small_numbers_are_not(self):
        self.assertEqual([n for n, _ in numbers_in_text("is 3342 still a draft")], ["3342"])
        self.assertEqual([n for n, _ in numbers_in_text("#3342 and build 13801")], ["3342", "13801"])
        self.assertEqual(numbers_in_text("CRM-3150"), [("3150", True)])
        self.assertEqual(numbers_in_text("in 2026 at 15:24 on 2026-11-20 for 500 rows"), [])
        self.assertEqual(numbers_in_text("the backfill touched 3000 rows"), [])                # a count, not PR 3000
        self.assertEqual([n for n, _ in numbers_in_text("PR 3000 rows")], ["3000"])             # ... unless it is said to be a PR
        self.assertEqual([n for n, _ in numbers_in_text("SQL-Scripts PR 149")], ["149"])


class Index(unittest.TestCase):
    def test_a_ticket_number_and_a_pr_number_resolve_to_their_own_things(self):
        ix = IdentifierIndex([(1, "VirtuousV2 PR 3150"), (2, "CRM-3150"), (3, "PR #3150"), (4, "crmv2-unified-build #73150")])
        self.assertEqual(set(ix.resolve("is 3150 green", ["is", "3150", "green"])), {1, 3})
        self.assertEqual(set(ix.resolve("CRM-3150 status", ["crm", "3150", "status"])), {2})

    def test_a_named_pr_phrase_stands_for_its_number_only_when_it_is_rare_and_unambiguous(self):
        labels = [(1, "VirtuousV2 PR 3342"), (2, "SendGrid webhook #3342"), (3, "crmv2-unified-build #73266"), (4, "crmv2-unified-build #73281"),
                  (5, "#142 backfill")]
        ix = IdentifierIndex(labels, ["sendgrid webhook", "backfill"] + ["backfill again"] * 20)
        self.assertEqual(set(ix.resolve("the SendGrid webhook PR", ["the", "sendgrid", "webhook", "pr"])), {1, 2})
        self.assertEqual(ix.resolve("the crmv2-unified-build pipeline", ["the", "crmv2", "unified", "build", "pipeline"]), {})
        self.assertEqual(ix.resolve("a backfill script", ["a", "backfill", "script"]), {})        # said about many things: not a name


class Surfacing(unittest.TestCase):
    def setUp(self):
        self.s = fresh_store()
        self.today = datetime(2026, 11, 20, tzinfo=timezone.utc)

    def add(self, thing, text, **kw):
        return self.s.add_state(thing, text, evidence=[ev(f"m-{text[:8]}", text)], writer="w", trust="verified", learned_how="read", **kw)

    def test_a_bare_pr_number_surfaces_the_prs_current_keyed_values(self):
        self.add("VirtuousV2 PR 3342", "draft, open", state_key="pr.state", is_state=True, volatility="live")
        out = surface(self.s, "is 3342 still a draft", None, today=self.today)
        self.assertIn("draft, open", out.block)

    def test_notes_from_before_a_pr_merged_are_left_out_and_only_the_newest_note_shows(self):
        self.add("VirtuousV2 PR 3340", "merged", state_key="pr.state", is_state=True, volatility="live", as_of="2026-11-18T21:22:30Z")
        self.add("#3340", "#3340 needs another merge of the #3338 branch", as_of="2026-11-16")
        self.add("#3340", "#3340 was not included in build 13801", as_of="2026-11-19")
        self.add("datepicker #3340", "#3340 was reviewed twice", as_of="2026-11-19T01:00:00Z")
        out = surface(self.s, "is 3340 in prod yet", None, today=self.today)
        self.assertIn("merged", out.block)
        self.assertNotIn("another merge", out.block)
        self.assertEqual(sum(1 for t in ("not included", "reviewed twice") if t in out.block), 1)


class LiveKeys(unittest.TestCase):
    """A ticket key or PR number said in the input reaches the live thing whose current value carries it (an ask, an agent's card), even when the
    thing's own name is an opaque id, and that value ranks above meaning hits."""

    def setUp(self):
        self.s = fresh_store()

    def live(self, thing, key, text, writer="observer:board-asks"):
        return self.s.add_state(thing, text, evidence=[ev(f"l-{thing}", text)], writer=writer, trust="verified", learned_how="read",
                                state_key=key, volatility="live")

    def test_a_ticket_key_in_an_open_asks_title_names_the_ask(self):
        a = self.live("board ask ask-20261123155629-6e62", "ask.state", "open · decide · since 2026-11-23 · CRM-3148 NOT closable, 4 remaining PRs, pick order")
        self.s.add_state("Prince", "Running Prince as non-root for CRM-3148 is still open", evidence=[ev()], writer="w", trust="derived", learned_how="inferred")
        out = surface(self.s, "CRM-3148 Prince", None)
        self.assertIn(f"#{a.id}", out.block)
        self.assertLess(out.block.index(f"#{a.id}"), out.block.index("non-root") if "non-root" in out.block else len(out.block))

    def test_a_pr_number_on_an_agents_card_names_the_agent(self):
        a = self.live("board agent build-77", "agent.status", "running · builder · card PR-3349 · beat 5m ago", writer="observer:board-agents")
        self.assertIn(f"#{a.id}", surface(self.s, "how is PR 3349 going", None).block)

    def test_a_number_in_an_ordinary_state_does_not_make_its_thing_named(self):
        self.s.add_state("Prince", "Prince loopback bypass was measured for CRM-3182", evidence=[ev()], writer="w", trust="derived", learned_how="inferred")
        self.s.add_state("Prince", "Prince renders fonts from the image", evidence=[ev("x")], writer="w", trust="derived", learned_how="inferred")
        self.assertNotIn("fonts from the image", surface(self.s, "CRM-3182", None).block)

    def test_a_closed_ask_is_not_named_by_its_number(self):
        a = self.live("board ask ask-1", "ask.state", "open · decide · since 2026-11-20 · CRM-4001 pick the default")
        self.s.close_state(a.id, "no longer observed", actor="observer:board-asks")
        self.assertEqual("", surface(self.s, "CRM-4001", None).block)


class People(unittest.TestCase):
    def setUp(self):
        self.s = fresh_store()
        self.today = datetime(2026, 11, 20, tzinfo=timezone.utc)

    def add(self, thing, text, **kw):
        return self.s.add_state(thing, text, evidence=[ev(f"m-{text[:8]}", text)], writer="w", trust="verified", learned_how="read", **kw)

    def test_a_named_person_surfaces_what_is_known_about_their_work_but_not_their_standing_rules(self):
        self.add("Jen Gandy", "Jen Gandy does integration work primarily.")
        self.s.set_kind("Jen Gandy", "person")
        self.add("Jen Gandy", "Jen Gandy always reviews on Fridays.", volatility="rule", kind="rule")
        out = surface(self.s, "what does Jen work on", None, today=self.today, exclude_kinds=("agent", "person"))
        self.assertIn("integration work", out.block)
        self.assertNotIn("Fridays", out.block)                       # standing rules are in the session core already
        self.assertEqual(surface(self.s, "the jen thing", None, today=self.today, exclude_kinds=("agent", "person")).block, "")   # a name is capitalized


if __name__ == "__main__":
    unittest.main()
