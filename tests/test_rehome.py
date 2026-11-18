"""States about tools and topics are filed under the tool or topic, not under the person or agent who said or did it."""
import unittest

from support import ev, fresh_store
from statekeeper.claims import submit_claim
from statekeeper.rehome import apply, home_of, plan


def people(s):
    s.add_thing("Jane", "person")
    s.add_thing("Agent", "agent")
    s.add_thing("login-tool", "thing")
    return s


class HomeOf(unittest.TestCase):
    def test_a_person_subject_with_a_thing_object_is_filed_under_the_thing(self):
        s = people(fresh_store())
        self.assertEqual(("login-tool", "Jane"), home_of(s, "Jane", "login-tool"))
        self.assertEqual(("login-tool", "Agent"), home_of(s, "Agent", "login-tool"))

    def test_no_object_a_person_object_or_a_keyed_value_stays_put(self):
        s = people(fresh_store())
        self.assertEqual(("Jane", None), home_of(s, "Jane", None))
        self.assertEqual(("Jane", "Agent"), home_of(s, "Jane", "Agent"))
        self.assertEqual(("Agent", "login-tool"), home_of(s, "Agent", "login-tool", "agent.status"))
        self.assertEqual(("login-tool", "Jane"), home_of(s, "login-tool", "Jane"))

    def test_an_unknown_object_counts_as_a_thing(self):
        s = people(fresh_store())
        self.assertEqual(("new-cli", "Jane"), home_of(s, "Jane", "new-cli"))

    def test_the_claim_boundary_applies_it(self):
        s = people(fresh_store())
        r = submit_claim(s, {"thing": "Jane", "object": "login-tool", "text": "Jane fills the env file for the login tool.", "writer": "extractor:x",
                             "session_id": "s1", "evidence": [{"type": "message", "ref": "m1"}]})
        self.assertEqual(("login-tool", "Jane"), (r.state.thing_name, r.state.object_name))


class Backfill(unittest.TestCase):
    def test_plan_moves_object_states_and_single_named_topic_states_but_not_vague_ones(self):
        s = people(fresh_store())
        a = s.add_state("Jane", "Jane fills the env file.", object="login-tool", evidence=[ev()], writer="extractor:x", trust="extracted", learned_how="inferred")
        b = s.add_state("Agent", "Agents refresh sessions with login-tool before runs.", evidence=[ev()], writer="extractor:x", trust="extracted", learned_how="inferred")
        c = s.add_state("Jane", "Jane prefers short messages.", evidence=[ev()], writer="extractor:x", trust="extracted", learned_how="inferred")
        k = s.add_state("Agent", "working", evidence=[ev()], writer="rule:t", trust="verified", learned_how="read", state_key="agent.status", is_state=True)
        moves = {m["state"]: m for m in plan(s)}
        self.assertEqual({a.id, b.id}, set(moves))
        self.assertEqual("object", moves[a.id]["why"])
        self.assertEqual("named in text", moves[b.id]["why"])
        self.assertNotIn(c.id, moves)
        self.assertNotIn(k.id, moves)

    def test_apply_supersedes_and_keeps_evidence_status_and_history(self):
        s = people(fresh_store())
        a = s.add_state("Jane", "Jane fills the env file.", object="login-tool", evidence=[ev("m7", "said in m7")], writer="extractor:x", trust="extracted", learned_how="inferred")
        [(old, new)] = apply(s, plan(s))
        self.assertEqual(a.id, old)
        st = s.get_state(new)
        self.assertEqual(("login-tool", "Jane", "proposed", "extracted"), (st.thing_name, st.object_name, st.status, st.trust))
        self.assertEqual(["m7"], [e.ref for e in s.evidence_of([new])[new]])
        self.assertEqual(new, s.get_state(old).superseded_by)
        self.assertEqual([], plan(s))                     # idempotent: nothing left to move


if __name__ == "__main__":
    unittest.main()
