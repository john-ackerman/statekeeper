import json
import unittest

from support import ev, fresh_store
from statekeeper.decompose import apply, is_long, parse, plan

LONG_TEXT = ("PDF rollout state. The flag is at 50% for high-risk orgs. The converter retries 503 twice. Run `pup logs search --read-only` to see "
             "renders. Jane decided to wait for the core quota before going to 100%.")


def answer(_prompt):
    return json.dumps({"1": [
        {"thing": "PDF rollout", "object": None, "text": "The PDF flag is at 50% for high-risk orgs.", "kind": "belief", "durable": False},
        {"thing": "pup", "object": "PDF service", "text": "Renders are visible with `pup logs search --read-only`.", "kind": "rule", "durable": True},
        {"thing": "Jane", "object": "PDF rollout", "text": "Jane waits for the core quota before 100%.", "kind": "rule", "durable": True},
        {"thing": "x", "text": "the token is: abcdef123456", "kind": "belief"}]})


class Decompose(unittest.TestCase):
    def setUp(self):
        self.s = fresh_store()
        self.s.add_thing("Jane", "person")
        self.p = self.s.add_state("rollout", LONG_TEXT, evidence=[ev("doc:7", "the whole document " * 40)], writer="import:x", trust="derived",
                                  learned_how="inferred", status="active")

    def test_long_states_are_candidates_short_and_keyed_ones_are_not(self):
        self.assertTrue(is_long(self.p))
        short = self.s.add_state("rollout", "The flag is on.", evidence=[ev()], writer="t", trust="derived", learned_how="inferred")
        self.assertFalse(is_long(short))

    def test_a_secret_never_becomes_a_state(self):
        self.assertEqual(3, len(parse(answer(""))[1]))

    def test_atoms_are_filed_under_their_topic_cite_the_source_and_the_parent_is_superseded(self):
        got = plan(self.s, [self.p], call=answer)
        counts = apply(self.s, got)
        self.assertEqual({"parents": 1, "atoms": 3, "skipped": 0}, counts)
        old = self.s.get_state(self.p.id)
        self.assertFalse(old.standing)
        self.assertIn("decomposed into", old.resolution)
        kids = [x for x in self.s.searchable_states() if x.writer == "rule:decompose"]
        self.assertEqual({"PDF rollout", "pup"}, {k.thing_name for k in kids})            # Jane's decision is filed under the rollout
        quota = next(k for k in kids if "quota" in k.text)
        self.assertEqual(("PDF rollout", "Jane", "rule", "rule", "active", "derived"),
                         (quota.thing_name, quota.object_name, quota.kind, quota.volatility, quota.status, quota.trust))
        flag = next(k for k in kids if "50%" in k.text)
        self.assertEqual("work", flag.volatility)
        refs = {e.ref for e in self.s.evidence_of([quota.id])[quota.id]}
        self.assertEqual({"doc:7", f"#{self.p.id}"}, refs)

    def test_fewer_than_two_atoms_leaves_the_parent(self):
        counts = apply(self.s, {self.p.id: [{"thing": "a", "object": None, "text": "one", "kind": "belief", "durable": True}]})
        self.assertEqual(1, counts["skipped"])
        self.assertTrue(self.s.get_state(self.p.id).standing)


if __name__ == "__main__":
    unittest.main()
