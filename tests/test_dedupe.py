import unittest

from support import ThemeEmbedder, ev, fresh_store
from statekeeper.dedupe import find_duplicates, merge_duplicates
from statekeeper.embed import ensure_vectors


def setup():
    s = fresh_store()
    emb = ThemeEmbedder()
    a = s.add_state("fonts", "the company already has licensing for the pdf fonts", evidence=[ev("m1", "first source")], writer="extractor:1", trust="extracted", learned_how="inferred", session_id="s")
    b = s.add_state("template", "the company already has licensing for the pdf fonts", evidence=[ev("m2", "second source")], writer="extractor:2", trust="extracted", learned_how="inferred", session_id="s")
    other = s.add_state("board", "linear ticket sprint cycle planning", evidence=[ev("m3")], writer="extractor:3", trust="extracted", learned_how="inferred", session_id="s")
    ensure_vectors(s, emb)
    return s, emb, a, b, other


class Dedupe(unittest.TestCase):
    def test_a_newer_proposal_with_the_same_meaning_is_found_and_unrelated_ones_are_not(self):
        s, emb, a, b, other = setup()
        pairs = find_duplicates(s, emb.model, 0.95)
        self.assertEqual([(a.id, b.id)], [(p[0], p[1]) for p in pairs])

    def test_merging_keeps_the_older_state_gains_the_evidence_and_rejects_the_newer_with_a_reason(self):
        s, emb, a, b, other = setup()
        n = merge_duplicates(s, find_duplicates(s, emb.model, 0.95))
        self.assertEqual(1, n)
        self.assertEqual({"m1", "m2"}, {e.ref for e in s.evidence_of([a.id])[a.id]})
        self.assertEqual("rejected", s.get_state(b.id).status)
        self.assertIn(f"duplicate of #{a.id}", s.get_state(b.id).resolution)
        self.assertIn("state-evidenced", [e.event_type for e in s.events()])

    def test_a_standing_verified_state_is_never_merged_away_and_a_verified_one_outranks_the_proposal(self):
        s = fresh_store()
        emb = ThemeEmbedder()
        p = s.add_state("x", "pdf render quota scaling note", evidence=[ev("m1")], writer="extractor:1", trust="extracted", learned_how="inferred", session_id="s")
        v = s.add_state("y", "pdf render quota scaling note", evidence=[ev("m2")], writer="rule:t", trust="verified", learned_how="read")
        ensure_vectors(s, emb)
        pairs = find_duplicates(s, emb.model, 0.95)
        self.assertEqual([(v.id, p.id)], [(x[0], x[1]) for x in pairs])           # keep the verified one, drop the proposal
        merge_duplicates(s, pairs)
        self.assertEqual("active", s.get_state(v.id).status)
        self.assertEqual("rejected", s.get_state(p.id).status)


if __name__ == "__main__":
    unittest.main()
