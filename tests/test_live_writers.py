import json
import os
import tempfile
import time
import unittest
from pathlib import Path

import support  # noqa: F401
from support import fresh_store
from statekeeper.writers.live import observe_everything, recent_transcripts
from test_ado_build import build
from test_writers import payload


class Live(unittest.TestCase):
    def test_every_source_is_observed_and_a_failing_one_is_recorded_without_stopping_the_rest(self):
        s = fresh_store()

        def list_prs(repo):
            if repo.endswith("Workspace"):
                raise RuntimeError("gh down")
            return [101, 102]
        with tempfile.TemporaryDirectory() as d:
            t = Path(d, "p", "s", "subagents")
            t.mkdir(parents=True)
            (t / "agent-abc.jsonl").write_text(json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": "x"}], "stop_reason": "tool_use"}}), encoding="utf-8")
            r = observe_everything(s, repos=("ExampleOrg/ExampleApp", "ExampleOrg/ExampleWorkspace"), build_repos=("ExampleApp",), list_prs=list_prs, get_pr=lambda repo, n: payload(), get_build=lambda ref: build(id=100 + int(ref.split("/")[2])),
                                   transcripts=recent_transcripts(Path(d)))
        self.assertEqual((2, 2, 1), (r["prs"], r["builds"], r["agents"]))
        self.assertEqual(1, len(r["errors"]))
        self.assertIsNotNone(s.find("ExampleApp PR 101"))
        self.assertIsNotNone(s.find("ci-pipeline #201"))
        self.assertIsNotNone(s.find("agent-abc"))

    def test_a_second_observation_scores_verified_outcomes(self):
        s = fresh_store()
        for _ in range(2):
            observe_everything(s, repos=("o/ExampleApp",), build_repos=("ExampleApp",), list_prs=lambda r: [1], get_pr=lambda repo, n: payload(), get_build=lambda ref: None)
        self.assertEqual(3, len([o for o in s.outcomes(kind="verified_right")]))

    def test_a_pr_held_open_that_left_the_open_list_is_read_again_and_stops_reading_open(self):
        s = fresh_store()
        observe_everything(s, repos=("o/ExampleApp",), build_repos=(), list_prs=lambda r: [7], get_pr=lambda repo, n: payload(), get_build=lambda ref: None)
        seen = []

        def get_pr(repo, n):
            seen.append(n)
            return payload(state="MERGED", isDraft=False)
        observe_everything(s, repos=("o/ExampleApp",), build_repos=(), list_prs=lambda r: [], get_pr=get_pr, get_build=lambda ref: None)
        self.assertEqual([7], seen)
        state = next(x for x in s.states_of(s.find("ExampleApp PR 7").id) if x.state_key == "pr.state")
        self.assertEqual("merged", state.text)
        seen.clear()
        observe_everything(s, repos=("o/ExampleApp",), build_repos=(), list_prs=lambda r: [], get_pr=get_pr, get_build=lambda ref: None)
        self.assertEqual([], seen)                                         # merged is final: not read again

    def test_old_transcripts_are_not_listed(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d, "p", "s", "subagents")
            p.mkdir(parents=True)
            f = p / "agent-old.jsonl"
            f.write_text("{}", encoding="utf-8")
            old = time.time() - 3 * 86400
            os.utime(f, (old, old))
            self.assertEqual([], recent_transcripts(Path(d), hours=24))


if __name__ == "__main__":
    unittest.main()
