import json
import unittest
from datetime import datetime, timezone

from support import ThemeEmbedder, ev, fresh_store
from statekeeper import surface
from statekeeper import usefulness as u
from statekeeper.outcomes import used_proxy


class Topic(unittest.TestCase):
    def test_a_thing_named_twice_is_on_topic_and_fades_when_the_conversation_moves_on(self):
        t0 = 1_000_000.0
        topic = u.topic_update({}, t0, 1, named=[7], surfaced=[], on_topic=set())
        self.assertGreaterEqual(u.topic_scores(topic, t0 + 60, 2)[7], 0.6)              # the next turn: still what we are talking about
        later = u.topic_scores(topic, t0 + 60, 5)                                        # three more turns without it
        self.assertLess(later.get(7, 0), 0.6)
        moved = u.topic_update(topic, t0 + 60, 2, named=[9], surfaced=[], on_topic=set())   # a new subject: the old one halves at once
        self.assertLess(u.topic_scores(moved, t0 + 60, 2)[7], 0.4)
        self.assertEqual({}, u.topic_scores(topic, t0 + 6 * 3600, 2))                    # hours later: nothing is on topic

    def test_on_topic_a_common_word_resolves_to_the_thing_and_its_relevant_states_surface(self):
        s = fresh_store()
        board = s.add_thing("board", "thing")
        s.add_state("board", "quota scaling asks go on the board with pdf render numbers", evidence=[ev("b1")], writer="w", trust="verified", learned_how="read")
        s.add_state("board", "linear cycle tickets are pointed in fibonacci", evidence=[ev("b2")], writer="w", trust="verified", learned_how="read")
        for i in range(20):
            s.add_state(f"item {i}", f"item {i} sits on the board", evidence=[ev(f"i{i}")], writer="w", trust="verified", learned_how="read")
        emb, prompt = ThemeEmbedder(), "what about the pdf queue on the board"
        today = datetime(2026, 11, 18, tzinfo=timezone.utc)
        from statekeeper.embed import cosine
        c = cosine(emb.embed([prompt])[0], emb.embed(["board: quota scaling asks go on the board with pdf render numbers"])[0])
        floor = c + 0.07                     # beyond what naming a generic thing allows (0.06), within what being on topic allows (0.08)
        cold = surface(s, prompt, emb, floor=floor, today=today).block
        warm = surface(s, prompt, emb, floor=floor, today=today, topic={board.id: 1.2}).block
        self.assertNotIn("quota scaling asks", cold)       # "board" alone is a common word here
        self.assertIn("quota scaling asks", warm)          # we are talking about the board: its relevant state surfaces
        self.assertNotIn("fibonacci", warm)                # ... and only the relevant one
        self.assertEqual([board.id], surface(s, prompt, emb, today=today).named)


class Signals(unittest.TestCase):
    def test_explicit_feedback_is_parsed_only_with_its_prefix(self):
        self.assertEqual([(12, "contradicted"), (5, "used"), (9, "unhelpful")],
                         u.parse_feedback("sk #12 stale, statekeeper: #5 useful and sk #9 noise"))
        self.assertEqual([], u.parse_feedback("PR #3345 is wrong"))                      # a PR number is not feedback

    def test_a_correction_must_point_at_the_shown_state(self):
        s = fresh_store()
        st = s.add_state("PDF rollout", "high-risk orgs are at 50 percent", evidence=[ev()], writer="w", trust="derived", learned_how="inferred")
        self.assertEqual([st.id], u.corrected(s, "no, high-risk orgs are at 100 percent now", [st.id]))
        self.assertEqual([], u.corrected(s, "no, let's do the datepicker first", [st.id]))   # a correction about something else
        self.assertEqual([], u.corrected(s, "the PDF rollout high-risk orgs", [st.id]))     # no correction cue

    def test_acting_on_a_pr_or_citing_the_id_counts_as_use(self):
        s = fresh_store()
        st = s.add_state("ExampleApp PR 42", "merged", evidence=[ev()], writer="w", trust="verified", learned_how="read", state_key="pr.state")
        self.assertTrue(used_proxy('{"command": "gh pr view 42"} PR 42 is merged already', st))
        self.assertTrue(used_proxy(f"per #{st.id} this is done", st))
        self.assertFalse(used_proxy("PR 420 is merged", st))


class Weighing(unittest.TestCase):
    def setUp(self):
        self.s = fresh_store()

    def state(self, text):
        return self.s.add_state("thing", text, evidence=[ev(text[:6])], writer="w", trust="derived", learned_how="inferred")

    def test_weights_follow_evidence_conservatively_with_a_trail_and_contradictions_are_flagged_not_closed(self):
        noisy, good, wrong, quiet = self.state("shown a lot never used"), self.state("used often"), self.state("said to be wrong"), self.state("seen twice")
        for i in range(7):
            self.s.log_outcome(noisy.id, "surfaced", source="surface", turn_ref=f"t{i}")
            self.s.log_outcome(good.id, "surfaced", source="surface", turn_ref=f"t{i}")
        for i in range(4):
            self.s.log_outcome(good.id, "used", source="shadow-stop", turn_ref=f"t{i}")
        u.log_feedback(self.s, [(wrong.id, "contradicted")], source="feedback:prompt")
        for i in range(2):
            self.s.log_outcome(quiet.id, "surfaced", source="surface", turn_ref=f"q{i}")
        out = u.weigh(self.s, actor="nightly:usefulness")
        w = self.s.state_weights()
        self.assertEqual((0.8, 1.1, 0.5), (w[noisy.id], w[good.id], w[wrong.id]))
        self.assertNotIn(quiet.id, w)                                                    # too little evidence: left alone
        self.assertTrue(self.s.get_state(wrong.id).standing)                             # never closed by the machine
        self.assertEqual([wrong.id], [sid for sid, _r, _a in self.s.open_flags()])
        self.assertEqual(3, len(out["changed"]))
        self.assertEqual([], u.weigh(self.s)["changed"])                                 # idempotent
        self.s.set_param(f"weight.state.{noisy.id}", 1.0, "owner says it is fine", actor="owner")
        u.weigh(self.s)
        self.assertEqual(1.0, self.s.state_weights()[noisy.id])                          # a person's weight is not overridden
        self.assertEqual(0.8, self.s.revert_param(f"weight.state.{noisy.id}", actor="owner"))

    def test_a_down_weighted_state_needs_to_be_a_little_closer(self):
        emb = ThemeEmbedder()
        for i in range(4):                   # the same words elsewhere, so they are not rare and only meaning decides
            self.s.add_state(f"other {i}", "caps cores quota pdf render scaling", evidence=[ev(f"o{i}")], writer="w", trust="claimed", learned_how="claimed")
        st = self.s.add_state("the hub", "caps cores at the quota for pdf render scaling", evidence=[ev()], writer="w", trust="claimed", learned_how="claimed")
        prompt = "our cores got upped, how does the quota change pdf scaling"
        from statekeeper.embed import cosine
        c = cosine(emb.embed([prompt])[0], emb.embed([f"the hub: {st.text}"])[0])
        self.assertIn(f"#{st.id} caps cores", surface(self.s, prompt, emb, floor=c - 0.02).block)
        self.s.set_param(f"weight.state.{st.id}", 0.5, "noise", actor="nightly:usefulness")
        self.assertNotIn(f"#{st.id} caps cores", surface(self.s, prompt, emb, floor=c - 0.02).block)

    def test_the_summary_counts_use_and_lists_the_states_shown_most_without_use(self):
        a = self.state("a")
        for i in range(3):
            self.s.log_outcome(a.id, "surfaced", source="surface", turn_ref=f"t{i}")
        sm = u.summary(self.s, days=1)
        self.assertEqual((3, 0, [f"#{a.id} x3"]), (sm["shown"], sm["used"], sm["shown_most_unused"]))
        json.dumps(sm)


if __name__ == "__main__":
    unittest.main()
