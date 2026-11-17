import unittest

from support import fresh_store
from statekeeper.parity import build_parity, names_legacy, names_statekeeper

LEGACY_MAP = """Concept map for this turn (current beliefs with the fragments behind them):

Billing work (thing; related: written nearly as "billin work"): 0 accepted, 5 proposed, from 2 fragments
  #77 Both invoice date columns are kept dual-written
Reviewbot (thing; from the last turn): 1 accepted, 7 proposed, from 5 fragments
  #660 ci-tool -> Reviewbot: works"""
STATEKEEPER_BLOCK = """Current state for this input (each with how sure to be):

Reviewbot (thing; named):
  [#5] ci-tool request-review works (verified today)
the converter (thing; meaning 0.80):
  [#9] caps replicas (proposed)"""


class Names(unittest.TestCase):
    def test_thing_names_are_read_from_each_sides_block_format(self):
        self.assertEqual({"billing work", "reviewbot"}, names_legacy(LEGACY_MAP))
        self.assertEqual({"reviewbot", "the converter"}, names_statekeeper(STATEKEEPER_BLOCK))


class Parity(unittest.TestCase):
    def test_the_report_compares_per_turn_what_each_side_surfaced_and_their_sizes(self):
        s = fresh_store()
        s.record_injection("t1", "legacy", "UserPromptSubmit", "concept_map", LEGACY_MAP)
        s.record_injection("t1", "legacy", "UserPromptSubmit", "tracked", "## Tracked now\n- git: master")
        s.record_injection("t1", "statekeeper", "UserPromptSubmit", "concept_map", STATEKEEPER_BLOCK)
        s.record_injection("t2", "legacy", "UserPromptSubmit", "concept_map", "")
        s.record_injection("t2", "statekeeper", "UserPromptSubmit", "concept_map", "")
        s.record_injection("sess:s:1", "legacy", "SessionStart", "session_core", "x" * 4000)
        s.record_injection("sess:s:1", "statekeeper", "SessionStart:startup", "session_core", "y" * 2000)
        r = build_parity(s)
        self.assertEqual(2, r["prompt_turns"])
        row = next(x for x in r["turns"] if x["turn"] == "t1")
        self.assertEqual(["billing work"], row["only_legacy"])
        self.assertEqual(["the converter"], row["only_statekeeper"])
        self.assertEqual(["reviewbot"], row["both"])
        self.assertGreater(row["legacy_tokens"], row["statekeeper_tokens"] - 1000)
        self.assertEqual(1, r["both_empty_turns"])
        self.assertEqual(1000, r["session_start"][0]["legacy_tokens"])
        self.assertEqual(500, r["session_start"][0]["statekeeper_tokens"])
        self.assertIn("tracked_tokens_legacy_mean", r)


if __name__ == "__main__":
    unittest.main()
