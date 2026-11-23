"""Things form a hierarchy (LaunchDarkly > a flag). An input that names a leaf gets that leaf's states and its ancestors', never a sibling leaf's
unless it names that one too; an input that names only the parent gets the parent, not every child."""
import unittest

from support import ThemeEmbedder, ev, fresh_store
from statekeeper import surface
from statekeeper.topics import tree


class Hierarchy(unittest.TestCase):
    def setUp(self):
        s = self.s = fresh_store()
        s.add_thing("LaunchDarkly")
        s.add_thing("receipting-repair-requires-rendered", parent="LaunchDarkly")
        s.add_thing("sendgrid-webhook-require-signature", parent="LaunchDarkly")
        add = lambda thing, text, ref, **kw: s.add_state(thing, text, evidence=[ev(ref, text)], writer="w", trust="derived", learned_how="inferred",
                                                         volatility="stable", **kw)
        self.root = add("LaunchDarkly", "A LaunchDarkly flag's production state is read from its environments.production entry.", "r")
        self.leaf = add("receipting-repair-requires-rendered", "The flag gates the receipt repair guard and is off in production.", "a")
        self.sib = add("sendgrid-webhook-require-signature", "The flag rolls out per organization and is off in production.", "b")

    def ids(self, text):
        out = surface(self.s, text, ThemeEmbedder(), floor=0.5, max_things=4)
        return {st.id for it in out.items for st in it.states}

    def test_the_tree_knows_ancestors(self):
        tt = tree(self.s)
        self.assertEqual({self.s.find("LaunchDarkly").id}, tt.ancestors(self.s.find("receipting-repair-requires-rendered").id))

    def test_a_named_leaf_brings_its_ancestors_states_but_not_a_siblings(self):
        got = self.ids("is receipting-repair-requires-rendered on in production")
        self.assertIn(self.leaf.id, got)
        self.assertIn(self.root.id, got)
        self.assertNotIn(self.sib.id, got)

    def test_two_named_leaves_both_surface(self):
        got = self.ids("receipting-repair-requires-rendered and sendgrid-webhook-require-signature in production")
        self.assertTrue({self.leaf.id, self.sib.id} <= got)

    def test_naming_only_the_parent_gives_the_parent_not_every_child(self):
        got = self.ids("what is the state of the LaunchDarkly flag new-thing-xyz in production")
        self.assertIn(self.root.id, got)
        self.assertNotIn(self.leaf.id, got)
        self.assertNotIn(self.sib.id, got)


if __name__ == "__main__":
    unittest.main()
