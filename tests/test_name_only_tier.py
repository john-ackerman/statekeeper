"""The bulk-imported, unreviewed tier ranks below every other state: it never takes a slot from one, never replaces one, and never changes the
statistics that decide whether a thing is specific."""
import unittest

from support import ev, fresh_store
from statekeeper import surface


class Tier(unittest.TestCase):
    def setUp(self):
        self.s = fresh_store()

    def add(self, thing, text, writer="w", ref=None, trust="derived", **kw):
        return self.s.add_state(thing, text, evidence=[ev(ref or text[:20], text)], writer=writer, trust=trust, learned_how="inferred",
                                volatility="stable", **kw)

    def imported(self, thing, text):
        return self.add(thing, text, writer="import:conversation:r1", trust="extracted", status="active")

    def shown(self, text, **kw):
        out = surface(self.s, text, None, floor=0.5, **kw)
        return [x.id for it in out.items for x in it.states]

    def test_imported_states_never_take_a_slot_from_a_standing_one(self):
        own = self.add("Build 7.15.95.13801", "Build 7.15.95.13801 went to prod on 11-20.")
        for i in range(6):
            self.imported("Build 7.15.95.13801", f"Old conversation fact {i} about the build.")
        got = self.shown("what is in build 13801", max_things=1, states_each=1, max_states=1)
        self.assertEqual([own.id], got)

    def test_imported_states_do_not_make_a_thing_generic(self):
        own = self.add("board watcher", "It failed silently when it expired.")
        for i in range(12):
            self.imported("board watcher", f"Old conversation fact {i}.")
        self.assertIn(own.id, self.shown("how does the board watcher work", max_things=3))

    def test_an_imported_state_never_replaces_a_standing_one(self):
        own = self.add("PR 3340", "PR 3340 is deployed to prod.")
        self.imported("PR 3340", "PR 3340 is deployed to prod and approved.")
        self.assertIn(own.id, self.shown("is PR 3340 in prod", max_things=3))

    def test_an_imported_state_still_fills_a_free_slot(self):
        imp = self.imported("CRM-3148", "CRM-3148 tracks the Prince security fixes.")
        self.assertIn(imp.id, self.shown("CRM-3148", max_things=3))


if __name__ == "__main__":
    unittest.main()
