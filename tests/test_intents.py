import unittest
from datetime import datetime, timezone

from support import ev, fresh_store
from statekeeper import surface
from statekeeper.intents import asks_ship


class ShipQuestions(unittest.TestCase):
    def test_a_question_about_shipping_is_one_but_a_condition_about_deploying_is_not(self):
        self.assertTrue(asks_ship("did the datepicker ship"))
        self.assertTrue(asks_ship("is 3340 in prod yet"))
        self.assertTrue(asks_ship("what went out in the last prod release"))
        self.assertFalse(asks_ship("what Linear status does a ticket get when the PR is merged but not deployed yet"))
        self.assertFalse(asks_ship("how do I check what a flag is set to in production"))


class Answers(unittest.TestCase):
    def setUp(self):
        self.s = fresh_store()
        self.today = datetime(2026, 11, 20, tzinfo=timezone.utc)

    def add(self, thing, text, **kw):
        return self.s.add_state(thing, text, evidence=[ev(f"m-{text[:8]}", text)], writer="w", trust="verified", learned_how="read", **kw)

    def test_did_it_ship_brings_the_newest_release_and_the_prs_state(self):
        self.add("Release 7.15.93", "Release 7.15.93 has shipped.", as_of="2026-11-10")
        self.add("Build 7.15.95.13801", "Build 7.15.95.13801 has shipped.", as_of="2026-11-18")
        self.add("VirtuousV2 PR 3340", "merged", state_key="pr.state", is_state=True, volatility="live", as_of="2026-11-18T21:00:00Z")
        out = surface(self.s, "is 3340 in prod yet", None, today=self.today)
        self.assertIn("13801 has shipped", out.block)
        self.assertNotIn("7.15.93 has shipped", out.block)
        self.assertIn("merged", out.block)

    def test_waiting_on_me_lists_recent_pending_notes_only(self):
        self.add("supervisor task", "Installing the supervisor task is pending for John.")
        self.add("old ask", "The spike decision is waiting on John's decision.")
        self.s.db.execute("UPDATE states SET recorded_utc = '2026-11-01 10:00:00' WHERE text LIKE 'The spike%'")
        self.add("unrelated", "The coverage gate is whole-file.")
        out = surface(self.s, "is anything stuck waiting on me", None, today=self.today)
        self.assertIn("supervisor task is pending", out.block)
        self.assertNotIn("spike decision", out.block)

    def test_a_plan_about_prs_written_before_they_all_merged_is_left_out(self):
        self.add("VirtuousV2 PR 3337", "merged", state_key="pr.state", is_state=True, volatility="live", as_of="2026-11-18T20:00:00Z")
        self.add("VirtuousV2 PR 3340", "merged", state_key="pr.state", is_state=True, volatility="live", as_of="2026-11-18T21:00:00Z")
        self.add("Datepicker", "The release plan proposes putting #3337 and #3340 in the release.")
        self.s.db.execute("UPDATE states SET recorded_utc = '2026-11-18 16:00:00' WHERE text LIKE 'The release plan%'")
        out = surface(self.s, "what about the Datepicker", None, today=self.today)
        self.assertNotIn("release plan", out.block)


if __name__ == "__main__":
    unittest.main()
