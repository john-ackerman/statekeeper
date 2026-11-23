"""The conversation import: an old conversation fragment's extracted facts land as unreviewed, identifier-only states under existing (or numbered,
or 'Known > new') things; agent progress, agent commitments and new generic things are dropped; a fact the store already holds gains the fragment as
evidence instead of a copy; a fragment is imported once."""
import json
import unittest

from support import ev, fresh_store
from statekeeper.convimport import SOURCE, done_fragments, import_fragment
from statekeeper.retrieve import name_only


def answer(*facts):
    return json.dumps({"facts": [{"claim": "fact", "kind": "belief", "is_state": False, "as_of": None, "object": None, **f} for f in facts]})


FRAG = {"id": 7, "content": "a long enough conversation fragment about the PDF converter and CRM-3148 " * 2, "created": "2026-11-10T09:00:00", "source": "local"}


class Import(unittest.TestCase):
    def setUp(self):
        self.s = fresh_store()
        self.s.add_thing("PDF converter")
        self.s.add_thing("LaunchDarkly")

    def run_one(self, ans, near=lambda text: None, frag=FRAG):
        return import_fragment(self.s, frag, ans, near=near)

    def test_kept_facts_are_unreviewed_identifier_only_and_dated(self):
        rec = self.run_one(answer({"thing": "PDF converter", "text": "The PDF converter runs Prince 11.4."},
                                  {"thing": "CRM-3148", "text": "CRM-3148 tracks the Prince security fixes."},
                                  {"thing": "LaunchDarkly > pdf-converter-service-enabled", "text": "The flag gates the PDF converter."}))
        self.assertEqual(3, rec["written"])
        states = [self.s.get_state(i) for i in self.s.imported(SOURCE).values()]
        self.assertTrue(all(name_only(x) for x in states))
        self.assertTrue(all(x.as_of_utc == "2026-11-10" for x in states))
        self.assertEqual(self.s.find("LaunchDarkly").id, self.s.find("pdf-converter-service-enabled").parent_id)
        self.assertEqual("persistence-conv:7", self.s.evidence_of([states[0].id])[states[0].id][0].ref)

    def test_progress_commitments_and_new_things_are_dropped(self):
        rec = self.run_one(answer({"thing": "PDF converter", "text": "The agent is running the converter tests."},
                                  {"thing": "PDF converter", "text": "Claude will fix the quota.", "kind": "commitment"},
                                  {"thing": "rollout", "text": "The rollout goes one org at a time."},
                                  {"thing": "Some New Thing", "text": "It exists."}))
        self.assertEqual(0, rec["written"])
        self.assertEqual({"agent-progress": 1, "agent-commitment": 1, "new-thing": 2}, rec["dropped"])

    def test_a_fact_already_held_adds_evidence_instead_of_a_copy(self):
        old = self.s.add_state("PDF converter", "The PDF converter runs Prince 11.4.", evidence=[ev("x", "y")], writer="w", trust="derived", learned_how="read")
        rec = self.run_one(answer({"thing": "PDF converter", "text": "The PDF converter runs Prince 11.4."}), near=lambda text: old.id)
        self.assertEqual((0, 1), (rec["written"], rec["corroborated"]))
        self.assertIn("persistence-conv:7", [e.ref for e in self.s.evidence_of([old.id])[old.id]])
        self.assertEqual(1, len(self.s.states_of(self.s.find("PDF converter").id)))

    def test_a_fragment_is_imported_once(self):
        ans = answer({"thing": "PDF converter", "text": "The PDF converter runs Prince 11.4."})
        self.run_one(ans)
        self.assertEqual({7}, done_fragments(self.s))
        self.assertEqual({"skipped": "already imported"}, self.run_one(ans))
        self.assertEqual(1, len(self.s.imported(SOURCE)))


if __name__ == "__main__":
    unittest.main()
