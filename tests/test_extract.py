import json
import unittest

from support import fresh_store
from statekeeper.extract import Extractor, is_status_line, parse


def answer(*facts):
    return json.dumps({"facts": [dict({"claim": "fact", "is_state": False, "as_of": None, "object": None}, **f) for f in facts]})


def msg(text, source="Alex", id=1):
    return {"id": id, "source": source, "created": "2026-11-16 10:00:00", "content": text}


class Retry(unittest.TestCase):
    def test_invalid_json_is_retried_once_with_the_failure_named_and_then_succeeds(self):
        prompts = []
        replies = iter(["Sure! here is what I found", answer({"thing": "the converter", "text": "caps replicas at 75"})])

        def call(prompt):
            prompts.append(prompt)
            return next(replies)
        s = fresh_store()
        rec = Extractor(s, call=call).extract(msg("the converter caps replicas at 75, I decided"))
        self.assertEqual(2, len(prompts))
        self.assertIn("not valid JSON", prompts[1])
        self.assertEqual((1, 1), (rec["retries"], len(rec["written"])))
        self.assertEqual("proposed", s.states_of(s.find("the converter").id)[0].status)

    def test_a_second_invalid_answer_is_recorded_as_an_error_and_writes_nothing(self):
        s = fresh_store()
        rec = Extractor(s, call=lambda p: "nope").extract(msg("x" * 200))
        self.assertIn("error", rec)
        self.assertEqual((1, []), (rec["retries"], rec["written"]))
        self.assertEqual(0, len(s.things()))


class Filtering(unittest.TestCase):
    def test_the_agents_own_status_lines_are_dropped_and_lasting_facts_kept(self):
        s = fresh_store()
        call = lambda p: answer({"thing": "the builder", "text": "is still running"}, {"thing": "PR 5", "text": "fixes the invoice run"},
                                {"thing": "the watcher", "text": "was re-armed, nothing new"})
        rec = Extractor(s, call=call).extract(msg("The builder is still running. PR 5 fixes the invoice run.", source="Claude"))
        self.assertEqual(["fixes the invoice run"], [w["text"] for w in rec["written"]])
        self.assertEqual({"status-line": 2}, rec["rejected"])

    def test_a_person_saying_something_is_waiting_is_not_dropped_but_instructions_are(self):
        s = fresh_store()
        call = lambda p: answer({"thing": "PR 9", "text": "is awaiting review from Sam"}, {"thing": "PR 9", "text": "merge it if CI is green", "claim": "instruction"})
        rec = Extractor(s, call=call).extract(msg("PR 9 is awaiting review from Sam. Merge it if CI is green.", source="Alex"))
        self.assertEqual(["is awaiting review from Sam"], [w["text"] for w in rec["written"]])
        self.assertEqual({"instruction": 1}, rec["rejected"])

    def test_a_status_claim_about_a_thing_a_writer_already_tracks_is_dropped_even_from_john(self):
        from statekeeper.writers.agent_status import observe_agent
        s = fresh_store()
        observe_agent(s, {"agent": "agent-abc", "status": "working"})
        call = lambda p: answer({"thing": "agent-abc", "text": "is still running the tests"}, {"thing": "agent-abc", "text": "owns the billing branch"})
        rec = Extractor(s, call=call).extract(msg("agent-abc is still running the tests and owns the billing branch", source="Alex"))
        self.assertEqual(["owns the billing branch"], [w["text"] for w in rec["written"]])
        self.assertEqual({"owned-by-writer": 1}, rec["rejected"])

    def test_a_proposal_that_reads_as_done_is_kept_but_marked_proposed(self):
        s = fresh_store()
        call = lambda p: answer({"thing": "the board", "text": "gets a new column", "claim": "proposal"}, {"thing": "the board", "text": "the owner wants a filter", "claim": "proposal"})
        rec = Extractor(s, call=call).extract(msg("add a column, and I want a filter", source="Alex"))
        self.assertEqual(["Proposed: gets a new column", "the owner wants a filter"], [w["text"] for w in rec["written"]])
        self.assertEqual({}, rec["rejected"])

    def test_facts_are_written_as_extracted_proposals_citing_the_message(self):
        s = fresh_store()
        Extractor(s, call=lambda p: answer({"thing": "quota", "text": "is 300 cores"}), session="run-1").extract(msg("the quota is 300 cores", id=42))
        st = s.states_of(s.find("quota").id)[0]
        self.assertEqual(("proposed", "extracted", "run-1"), (st.status, st.trust, st.session_id))
        self.assertTrue(st.writer.startswith("extractor:"))
        e = s.evidence_of([st.id])[st.id][0]
        self.assertEqual(("message", "42"), (e.ref_type, e.ref))

    def test_parse_and_status_helpers(self):
        self.assertEqual({"facts": []}, parse('prefix {"facts": []} suffix'))
        with self.assertRaises(ValueError):
            parse("[1]")
        self.assertTrue(is_status_line("is still running"))
        self.assertFalse(is_status_line("caps replicas at 75"))


if __name__ == "__main__":
    unittest.main()
