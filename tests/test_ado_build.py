import unittest

import support  # noqa: F401
from support import fresh_store
from statekeeper.writers.ado_build import normalize, observe_build


def build(**kw):
    b = {"id": 1001, "buildNumber": "20261116.7", "status": "inProgress", "result": None, "sourceBranch": "refs/heads/ticket-1-exclude-reversed",
         "sourceVersion": "0123456789abcdef", "definition": {"name": "ci-pipeline"}, "startTime": "2026-11-16T20:00:00Z", "finishTime": None}
    b.update(kw)
    return b


class Normalize(unittest.TestCase):
    def test_status_and_result_become_one_plain_state(self):
        self.assertEqual("running on 0123456 (ticket-1-exclude-reversed)", normalize(build())["build.state"])
        self.assertEqual("queued on 0123456 (ticket-1-exclude-reversed)", normalize(build(status="notStarted"))["build.state"])
        self.assertEqual("succeeded on 0123456 (ticket-1-exclude-reversed)", normalize(build(status="completed", result="succeeded"))["build.state"])
        self.assertEqual("failed on 0123456 (ticket-1-exclude-reversed)", normalize(build(status="completed", result="failed"))["build.state"])
        self.assertTrue(normalize(build(status="completed", result="partiallySucceeded"))["build.state"].startswith("partially succeeded"))
        self.assertTrue(normalize(build(status="completed", result="canceled"))["build.state"].startswith("canceled"))


class PullRequestBuilds(unittest.TestCase):
    def test_a_pull_request_merge_ref_reads_as_the_pr(self):
        self.assertEqual("running on 0123456 (PR 3341)", normalize(build(sourceBranch="refs/pull/3341/merge"))["build.state"])


class Observe(unittest.TestCase):
    def test_a_build_goes_from_running_to_failed_and_the_running_state_becomes_history_marked_wrong(self):
        s = fresh_store()
        observe_build(s, build(), lambda: "2026-11-16 20:05:00.000+00:00")
        again = observe_build(s, build(), lambda: "2026-11-16 20:10:00.000+00:00")
        self.assertEqual([], again["changed"])
        done = observe_build(s, build(status="completed", result="failed", finishTime="2026-11-16T20:20:00Z"), lambda: "2026-11-16 20:21:00.000+00:00")
        self.assertEqual(["build.state"], done["changed"])
        thing = s.find("ci-pipeline #1001")
        self.assertTrue(thing.parent_id == s.find("ci-pipeline").id)
        self.assertTrue(next(x for x in s.states_of(thing.id) if x.state_key == "build.state").text.startswith("failed"))
        kinds = [o.kind for o in s.outcomes() if o.kind.startswith("verified")]
        self.assertEqual(["verified_right", "verified_wrong"], kinds)
        st = done["build.state"]
        self.assertEqual(("verified", "rule:ado-build"), (st.trust, st.writer))
        self.assertIn("az devops invoke", st.verify_handle)
        self.assertIn("1001", st.verify_handle)


if __name__ == "__main__":
    unittest.main()
