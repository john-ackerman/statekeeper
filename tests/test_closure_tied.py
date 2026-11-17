import unittest

import support  # noqa: F401
from support import fresh_store
from statekeeper.closure_tied import artifacts_in, tie_commitments
from statekeeper.writers.ado_build import observe_build
from statekeeper.writers.github_pr import observe_pr
from test_ado_build import build
from test_writers import payload

T0, T1, T2 = "2026-11-16 20:00:00.000+00:00", "2026-11-16 21:00:00.000+00:00", "2026-11-16 22:00:00.000+00:00"


class Parsing(unittest.TestCase):
    def test_pr_and_run_references_are_found_and_plain_numbers_are_not(self):
        self.assertEqual([("pr", "101")], artifacts_in("I'll verify the push on #101 when it reports."))
        self.assertEqual([("pr", "101")], artifacts_in("When PR 101 is green I'll tell you."))
        self.assertEqual([("build", "1001")], artifacts_in("When CI run 1001 lands I'll read the coverage result."))
        self.assertEqual([("build", "70648")], artifacts_in("I'll read build 70648 when it finishes."))
        self.assertEqual([], artifacts_in("I'll file 3 tickets and report in 10 minutes."))


class Tying(unittest.TestCase):
    def test_a_commitment_on_a_running_build_stays_open_until_the_builds_state_reaches_a_terminal_one(self):
        s = fresh_store()
        c = [("When CI run 1001 lands I'll read the coverage result.", T0)]
        observe_build(s, build(), lambda: T0)
        self.assertEqual(["open"], [r.status for r in tie_commitments(s, c)])
        observe_build(s, build(), lambda: T1)                                       # still running: re-verified, not changed
        self.assertEqual(["open"], [r.status for r in tie_commitments(s, c)])
        observe_build(s, build(status="completed", result="succeeded"), lambda: T2)
        r = tie_commitments(s, c)[0]
        self.assertEqual("closed", r.status)
        self.assertEqual(1, len(r.closing_states))
        self.assertIn("succeeded", s.get_state(r.closing_states[0]).text)

    def test_a_commitment_on_a_pr_closes_when_its_ci_or_state_changes_to_a_terminal_value(self):
        s = fresh_store()
        pending = [{"name": "build", "status": "IN_PROGRESS", "conclusion": ""}]
        observe_pr(s, "R", 101, payload(statusCheckRollup=pending), lambda: T0)
        c = [("I'll verify the push on #101 when it reports.", T0)]
        self.assertEqual("open", tie_commitments(s, c)[0].status)
        observe_pr(s, "R", 101, payload(), lambda: T1)                              # CI turns green
        self.assertEqual("closed", tie_commitments(s, c)[0].status)

    def test_a_terminal_state_that_predates_the_commitment_does_not_close_it(self):
        s = fresh_store()
        observe_build(s, build(status="completed", result="failed"), lambda: T0)
        self.assertEqual("open", tie_commitments(s, [("I'll rerun build 1001 and read it.", T1)])[0].status)

    def test_a_commitment_naming_no_artifact_or_an_unobserved_one_is_untied_not_guessed(self):
        s = fresh_store()
        r = tie_commitments(s, [("I'll report when the agent finishes.", T0), ("I'll read run 99999 when it lands.", T0)])
        self.assertEqual(["untied", "untied"], [x.status for x in r])


if __name__ == "__main__":
    unittest.main()
