import unittest

import support  # noqa: F401  (puts src on the path)
from statekeeper.commitments import Action, find_commitments, open_commitments


class Finding(unittest.TestCase):
    def test_first_person_future_sentences_are_commitments(self):
        text = "I checked the file. I'll add a test for the converter next. Let me run the suite. I'm going to push the branch."
        got = [c.sentence for c in find_commitments(text)]
        self.assertEqual(["I'll add a test for the converter next.", "Let me run the suite.", "I'm going to push the branch."], got)

    def test_offers_questions_negations_conditionals_and_other_peoples_plans_are_not(self):
        for s in ["If you want, I can add a test.", "Should I merge it?", "I won't merge anything.", "I'll skip it if CI is red.",
                  "You'll need to approve it.", "Let me know what you think.", "The agent will report back.", "> I'll do it later",
                  "I can run the suite.", "We could add a test.", "Say the word and I will.", "Until then I'll treat prod runs as scarce.",
                  "Say yes and I'll file the ticket.", "I'll start step 1 on your OK.", "I'll try again later.", "**Other option:** add a rule and I'll have the agent run it."]:
            self.assertEqual([], find_commitments(s), s)


class Fulfilment(unittest.TestCase):
    def test_a_later_action_in_the_turn_that_touches_the_same_thing_fulfils_it(self):
        events = ["I'll add a test for the converter.", Action("Edit", "tests/test_converter.py add test"), "Done."]
        self.assertEqual([], open_commitments(events))

    def test_a_commitment_with_no_matching_later_action_is_open_at_the_end_of_the_turn(self):
        events = ["I'll file a ticket for the quota problem.", Action("Read", "README.md"), "Here is the summary."]
        self.assertEqual(["I'll file a ticket for the quota problem."], [c.sentence for c in open_commitments(events)])

    def test_an_action_before_the_sentence_does_not_count(self):
        events = [Action("Bash", "run suite"), "I'll run the suite again."]
        self.assertEqual(1, len(open_commitments(events)))

    def test_a_check_verb_is_fulfilled_by_any_read_class_action(self):
        events = ["Let me check the retry logic.", Action("Grep", "PdfServiceAdapter retry")]
        self.assertEqual([], open_commitments(events))


class Semantic(unittest.TestCase):
    def test_a_restated_promise_is_not_fulfilment_but_a_report_of_something_done_and_an_action_are(self):
        from statekeeper.commitments import fulfilment_chunks
        got = fulfilment_chunks(["I'll report when it finishes. Nightly Backup and Wake Loop are registered and enabled. The run will take ten minutes.", Action("Bash", '{"command": "git push"}')])
        self.assertEqual(["Nightly Backup and Wake Loop are registered and enabled.", "Bash"], [got[0], got[1].split(":")[0]])
        self.assertIn("git push", got[1])
        self.assertEqual(2, len(got))

    def test_a_later_chunk_close_in_meaning_closes_it_and_an_unrelated_one_does_not(self):
        from support import ThemeEmbedder
        from statekeeper.commitments import closure_by_meaning
        emb = ThemeEmbedder()
        score, chunk, closed = closure_by_meaning("I'll read the reviewbot review loop result", ["Lunch is at noon", "The reviewbot review loop finished clean"], emb, 0.6)
        self.assertEqual(("The reviewbot review loop finished clean", True), (chunk, closed))
        self.assertFalse(closure_by_meaning("I'll read the reviewbot review loop result", ["Lunch is at noon"], emb, 0.6)[2])
        self.assertEqual((0.0, "", False), closure_by_meaning("x", [], emb))


if __name__ == "__main__":
    unittest.main()
