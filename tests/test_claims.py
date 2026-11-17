import json
import unittest

from support import fresh_store
from statekeeper.claims import ClaimError, submit_claim

BASE = {"thing": "PR 5", "text": "CI is green", "writer": "agent-7", "session_id": "s1", "learned_how": "read",
        "evidence": [{"type": "tool", "ref": "gh pr checks 5", "excerpt": "all checks passed"}]}


def claim(**kw):
    c = dict(BASE)
    c.update(kw)
    return c


class Boundary(unittest.TestCase):
    def test_a_well_formed_claim_is_written_with_its_provenance_and_accepts_json_text(self):
        s = fresh_store()
        r = submit_claim(s, json.dumps(claim()))
        self.assertEqual(("proposed", "claimed", "s1", "agent-7"), (r.state.status, r.state.trust, r.state.session_id, r.state.writer))
        e = s.evidence_of([r.state.id])[r.state.id][0]
        self.assertEqual(("tool", "gh pr checks 5"), (e.ref_type, e.ref))
        self.assertIsNotNone(e.sha256)                                    # derived from the excerpt

    def test_an_agent_cannot_assert_more_trust_than_its_writer_name_allows(self):
        s = fresh_store()
        r = submit_claim(s, claim(trust="owner"))
        self.assertEqual(("claimed", "proposed", "owner"), (r.state.trust, r.state.status, r.clamped_from))
        ci = submit_claim(s, claim(text="CI is red", writer="rule:ci", trust="verified", state_key="ci"))
        self.assertEqual(("verified", "active", None), (ci.state.trust, ci.state.status, ci.clamped_from))
        too_high = submit_claim(s, claim(text="x y", writer="rule:ci", trust="owner"))
        self.assertEqual("verified", too_high.state.trust)

    def test_malformed_claims_are_refused_and_nothing_is_written(self):
        s = fresh_store()
        bad = [claim(evidence=[]), claim(thing=""), claim(learned_how="dreamed"), claim(session_id=None), claim(surprise=1),
               claim(evidence=[{"type": "tool"}]), claim(kind="fact"), "not json", "[1]"]
        for b in bad:
            with self.assertRaises(ClaimError, msg=str(b)):
                submit_claim(s, b)
        self.assertEqual(0, len(s.things()))
        self.assertEqual([], s.events())

    def test_rule_writers_need_no_session_but_agents_do(self):
        s = fresh_store()
        r = submit_claim(s, claim(writer="rule:ci", session_id=None, trust="verified"))
        self.assertEqual("active", r.state.status)

    def test_a_proposal_that_contradicts_the_current_keyed_state_is_flagged_not_applied(self):
        s = fresh_store()
        cur = submit_claim(s, claim(writer="rule:ci", trust="verified", state_key="ci", text="CI is green"))
        r = submit_claim(s, claim(state_key="ci", text="CI is red"))
        self.assertEqual("proposed", r.state.status)
        self.assertEqual([cur.state.id], r.disputed_with)
        self.assertEqual("active", s.get_state(cur.state.id).status)
        self.assertEqual([(r.state.id, f"disputes #{cur.state.id}")], [(f[0], f[1]) for f in s.open_flags()])
        self.assertIn("state-flagged", [e.event_type for e in s.events()])

    def test_a_newer_verified_observation_replaces_the_current_one_by_rule(self):
        s = fresh_store()
        a = submit_claim(s, claim(writer="rule:ci", trust="verified", state_key="ci", text="CI is red"))
        b = submit_claim(s, claim(writer="rule:ci", trust="verified", state_key="ci", text="CI is green"))
        self.assertEqual(b.state.id, s.get_state(a.state.id).superseded_by)
        self.assertEqual([], b.disputed_with)


if __name__ == "__main__":
    unittest.main()
