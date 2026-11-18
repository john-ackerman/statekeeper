import unittest

import support  # noqa: F401
from support import fresh_store
from statekeeper.writers.core_notes import observe_core

FRAGMENTS = [
    {"id": 1, "type": "Identity", "content": "Prefers small, reversible changes.", "summary": "working style", "created": "2026-11-01"},
    {"id": 2, "type": "Relational", "content": "Likes numbered questions.", "summary": "how to ask"},
    {"id": 3, "type": "Other", "content": "ignored"},
]


class CoreNotes(unittest.TestCase):
    def test_fragments_become_keyed_states_of_the_agent_and_person_and_the_note_its_own_thing(self):
        s = fresh_store()
        r = observe_core(s, FRAGMENTS, "Doing the migration; next the tests.", agent="Bot", person="Pat")
        self.assertEqual({"core": 2, "changed": 3, "note": True}, r)
        self.assertEqual("agent", s.find("Bot").kind)
        self.assertEqual("person", s.find("Pat").kind)
        self.assertIn("working style: Prefers small, reversible changes.", [x.text for x in s.states_of(s.find("Bot").id)])
        self.assertIn("Doing the migration; next the tests.", [x.text for x in s.states_of(s.find("working note").id)])

    def test_an_unchanged_source_changes_nothing_on_the_second_run_and_a_blank_note_is_skipped(self):
        s = fresh_store()
        observe_core(s, FRAGMENTS, None)
        again = observe_core(s, FRAGMENTS, "  ")
        self.assertEqual({"core": 2, "changed": 0, "note": False}, again)

    def test_a_person_made_earlier_as_a_plain_thing_is_corrected_so_the_session_core_shows_it(self):
        from statekeeper.session import session_block
        s = fresh_store()
        s.add_thing("Pat", "thing", kind_said=False)
        observe_core(s, FRAGMENTS, None, agent="Bot", person="Pat")
        self.assertEqual("person", s.find("Pat").kind)
        self.assertIn("[Pat] how to ask: Likes numbered questions.", session_block(s))

    def test_a_kind_someone_confirmed_is_left_alone(self):
        s = fresh_store()
        s.add_thing("Pat", "thing")
        s.set_kind("Pat", "thing")
        observe_core(s, FRAGMENTS, None, agent="Bot", person="Pat")
        self.assertEqual("thing", s.find("Pat").kind)

    def test_a_changed_fragment_is_a_change(self):
        s = fresh_store()
        observe_core(s, FRAGMENTS, None)
        edited = [dict(FRAGMENTS[0], content="Prefers large changes.")] + FRAGMENTS[1:]
        self.assertEqual(1, observe_core(s, edited, None)["changed"])


if __name__ == "__main__":
    unittest.main()
