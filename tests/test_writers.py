import unittest
from datetime import datetime, timezone

import support  # noqa: F401  (puts src on the path)
from support import fresh_store
from statekeeper import surface
from statekeeper.writers.github_pr import normalize, observe_pr


def payload(**kw):
    p = {"state": "OPEN", "isDraft": True, "reviewDecision": "", "headRefOid": "abcdef1234567890", "updatedAt": "2026-11-16T20:00:00Z",
         "statusCheckRollup": [{"name": "build", "status": "COMPLETED", "conclusion": "SUCCESS"}, {"name": "cov", "status": "COMPLETED", "conclusion": "SUCCESS"}]}
    p.update(kw)
    return p


class Normalize(unittest.TestCase):
    def test_a_drafts_wip_check_is_not_ci(self):
        wip = [{"name": "build", "status": "COMPLETED", "conclusion": "SUCCESS"}, {"name": "WIP", "status": "IN_PROGRESS", "conclusion": ""}]
        self.assertTrue(normalize(payload(statusCheckRollup=wip))["pr.ci"].startswith("CI green (1 of 1"))
        self.assertTrue(normalize(payload(statusCheckRollup=wip, isDraft=False))["pr.ci"].startswith("CI pending"))

    def test_a_pr_payload_becomes_three_keyed_observations(self):
        got = normalize(payload())
        self.assertEqual({"pr.state", "pr.ci", "pr.review"}, set(got))
        self.assertEqual("draft, open", got["pr.state"])
        self.assertEqual("CI green (2 of 2 checks passed) on abcdef1", got["pr.ci"])
        self.assertEqual("no review decision yet", got["pr.review"])

    def test_ci_is_red_if_any_check_failed_and_pending_while_any_is_running(self):
        red = normalize(payload(statusCheckRollup=[{"name": "build", "status": "COMPLETED", "conclusion": "FAILURE"}, {"name": "cov", "status": "IN_PROGRESS", "conclusion": ""}]))
        self.assertTrue(red["pr.ci"].startswith("CI red (build failed)"))
        pending = normalize(payload(statusCheckRollup=[{"name": "build", "status": "IN_PROGRESS", "conclusion": ""}]))
        self.assertTrue(pending["pr.ci"].startswith("CI pending"))
        self.assertTrue(normalize(payload(state="MERGED", isDraft=False))["pr.state"].startswith("merged"))


class Observe(unittest.TestCase):
    def test_an_unchanged_observation_moves_last_verified_but_not_last_changed(self):
        s = fresh_store()
        first = observe_pr(s, "ExampleApp", 101, payload(), lambda: "2026-11-16 20:00:00.000+00:00")
        n_states = len(s.states_of(s.find("ExampleApp PR 101").id, history=True))
        again = observe_pr(s, "ExampleApp", 101, payload(), lambda: "2026-11-16 21:30:00.000+00:00")
        thing = s.find("ExampleApp PR 101")
        self.assertEqual(n_states, len(s.states_of(thing.id, history=True)))           # no new rows
        ci = next(x for x in s.states_of(thing.id) if x.state_key == "pr.ci")
        self.assertEqual("2026-11-16 21:30:00.000+00:00", ci.confirmed_utc)          # last verified moved
        self.assertEqual(first["pr.ci"].recorded_utc, ci.recorded_utc)               # last changed did not
        self.assertEqual([], again["changed"])

    def test_a_changed_observation_supersedes_by_key_and_keeps_the_old_one_as_history(self):
        s = fresh_store()
        observe_pr(s, "ExampleApp", 101, payload(), lambda: "2026-11-16 20:00:00.000+00:00")
        out = observe_pr(s, "ExampleApp", 101, payload(statusCheckRollup=[{"name": "build", "status": "COMPLETED", "conclusion": "FAILURE"}]), lambda: "2026-11-16 22:00:00.000+00:00")
        thing = s.find("ExampleApp PR 101")
        self.assertEqual(["pr.ci"], out["changed"])
        current = {x.state_key: x.text for x in s.states_of(thing.id)}
        self.assertTrue(current["pr.ci"].startswith("CI red"))
        self.assertEqual(2, len([x for x in s.states_of(thing.id, history=True) if x.state_key == "pr.ci"]))

    def test_the_states_are_verified_carry_a_verify_handle_and_evidence_and_the_pr_sits_under_its_repo(self):
        s = fresh_store()
        out = observe_pr(s, "ExampleApp", 101, payload(), lambda: "2026-11-16 20:00:00.000+00:00")
        st = out["pr.ci"]
        self.assertEqual(("verified", "active", "rule:github-pr"), (st.trust, st.status, st.writer))
        self.assertIn("gh pr view 101", st.verify_handle)
        self.assertEqual("tool", s.evidence_of([st.id])[st.id][0].ref_type)
        self.assertEqual(s.find("ExampleApp").id, s.find("ExampleApp PR 101").parent_id)

    def test_the_surfaced_block_shows_the_current_pr_state(self):
        s = fresh_store()
        observe_pr(s, "ExampleApp", 101, payload(), lambda: "2026-11-16 20:00:00.000+00:00")
        out = surface(s, "what is ExampleApp PR 101 doing", None, today=datetime(2026, 11, 16, tzinfo=timezone.utc))
        self.assertIn("CI green", out.block)
        self.assertIn("draft, open", out.block)


if __name__ == "__main__":
    unittest.main()
