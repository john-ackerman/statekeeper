import unittest

from support import ThemeEmbedder, ev, fresh_store
from statekeeper.consolidate import run_pass, validate


def seeded():
    s = fresh_store()
    s.add_state("fonts", "the company already has licensing for the pdf fonts", evidence=[ev("m1")], writer="extractor:1", trust="extracted", learned_how="inferred", session_id="s")
    s.add_state("template", "the company already has licensing for the pdf fonts", evidence=[ev("m2")], writer="extractor:2", trust="extracted", learned_how="inferred", session_id="s")
    s.add_state("board", "linear ticket sprint cycle planning", evidence=[ev("m3")], writer="extractor:3", trust="extracted", learned_how="inferred", session_id="s")
    return s


class Pass(unittest.TestCase):
    def test_a_pass_merges_duplicates_links_them_and_is_idempotent(self):
        s, emb = seeded(), ThemeEmbedder()
        first = run_pass(s, emb)
        self.assertEqual((1, 1), (first["merged"], first["duplicate_pairs_found"]))
        self.assertEqual("same", s.links_of(1)[0][2])
        second = run_pass(s, emb)
        self.assertEqual((0, 0), (second["merged"], second["duplicate_pairs_found"]))

    def test_the_threshold_is_a_logged_revertible_parameter(self):
        s, emb = seeded(), ThemeEmbedder()
        s.set_param("dedupe_threshold", 1.01, "test: nothing may merge", actor="owner")
        self.assertEqual(0, run_pass(s, emb)["merged"])
        s.revert_param("dedupe_threshold", actor="owner") if s.param_history("dedupe_threshold")[0][1] != 1.01 else s.set_param("dedupe_threshold", 0.95, "restore", actor="owner")
        self.assertEqual(1, run_pass(s, emb)["merged"])

    def test_a_dry_run_changes_nothing(self):
        s, emb = seeded(), ThemeEmbedder()
        r = run_pass(s, emb, apply=False)
        self.assertEqual((1, 0), (r["duplicate_pairs_found"], r["merged"]))
        self.assertEqual("proposed", s.get_state(2).status)


class Validation(unittest.TestCase):
    def test_validation_counts_merges_reopenings_and_misses_on_survivors(self):
        s, emb = seeded(), ThemeEmbedder()
        run_pass(s, emb)
        s.log_outcome(1, "missed", source="restated-stored-not-surfaced", turn_ref="t1")
        v = validate(s)
        self.assertEqual((1, 0, 1), (v["currently_merged"], v["reopened_by_a_person"], v["misses_logged_on_survivors"]))
        s.reopen(2, "these are different facts", actor="owner")
        v2 = validate(s)
        self.assertEqual((0, 1, 1), (v2["currently_merged"], v2["reopened_by_a_person"], v2["merged_total"]))
        self.assertEqual("proposed", s.get_state(2).status)


if __name__ == "__main__":
    unittest.main()
