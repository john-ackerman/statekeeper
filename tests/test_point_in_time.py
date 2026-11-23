"""Point-in-time ('work' volatility) states say when they were true, and after a week with nobody confirming them they stop reaching an input by
meaning alone: naming their thing still shows them."""
import unittest
from datetime import datetime, timezone

from support import ThemeEmbedder, ev, fresh_store
from statekeeper import surface

TODAY = datetime(2026, 11, 23, 12, 0, tzinfo=timezone.utc)


class PointInTime(unittest.TestCase):
    def setUp(self):
        self.s = fresh_store()

    def add(self, text, recorded, volatility="work", thing="slack relay"):
        return self.s.add_state(thing, text, evidence=[ev(text[:6], text)], writer="w", trust="derived", learned_how="inferred",
                                volatility=volatility, recorded=recorded)

    def test_a_work_state_carries_its_date(self):
        st = self.add("the board watch relay for slack is still open", "2026-11-21 09:00:00")
        out = surface(self.s, "is the slack board watch working", ThemeEmbedder(), floor=0.5, today=TODAY)
        self.assertIn(f"#{st.id}", out.block)
        self.assertIn("as of 2026-11-21", out.block)

    def test_a_week_old_unconfirmed_work_state_does_not_route_by_meaning(self):
        st = self.add("the board watch relay for slack is still open", "2026-11-12 09:00:00")
        self.assertNotIn(f"#{st.id}", surface(self.s, "is the slack board watch working", ThemeEmbedder(), floor=0.5, today=TODAY).block)

    def test_naming_its_thing_still_shows_it_dated(self):
        st = self.add("the board watch relay for slack is still open", "2026-11-12 09:00:00")
        out = surface(self.s, "what about the slack relay", ThemeEmbedder(), floor=0.5, today=TODAY)
        self.assertIn(f"#{st.id}", out.block)
        self.assertIn("as of 2026-11-12", out.block)

    def test_a_recent_confirmation_keeps_it_routable(self):
        st = self.add("the board watch relay for slack is still open", "2026-11-12 09:00:00")
        self.s.confirm(st.id, actor="owner", at="2026-11-22 09:00:00")
        self.assertIn(f"#{st.id}", surface(self.s, "is the slack board watch working", ThemeEmbedder(), floor=0.5, today=TODAY).block)

    def test_stable_states_are_neither_dated_nor_demoted(self):
        st = self.add("the board watch relay posts to slack", "2026-10-01 09:00:00", volatility="stable")
        out = surface(self.s, "is the slack board watch working", ThemeEmbedder(), floor=0.5, today=TODAY)
        self.assertIn(f"#{st.id}", out.block)
        self.assertNotIn("as of", out.block)


if __name__ == "__main__":
    unittest.main()
