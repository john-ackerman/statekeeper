import unittest
from datetime import datetime, timedelta, timezone

from support import ev, fresh_store
from statekeeper.session import session_block, whats_running
from statekeeper.writers.board_agents import observe_board_agents
from statekeeper.writers.live import observe_everything

NOW = datetime(2026, 11, 20, 17, 0, tzinfo=timezone.utc)


def agent(aid, state, minutes_ago=5, card="PR-3349", role="builder"):
    t = (NOW - timedelta(minutes=minutes_ago)).isoformat().replace("+00:00", "Z")
    return {"id": aid, "state": state, "role": role, "card": card, "heartbeatAt": t, "updatedAt": t}


class BoardAgents(unittest.TestCase):
    def setUp(self):
        self.s = fresh_store()

    def status(self, aid):
        t = self.s.find(f"board agent {aid}")
        cur = [x for x in self.s.states_of(t.id) if x.state_key == "agent.status" and x.status == "active"] if t else []
        return cur[0].text if cur else None

    def test_running_agents_are_written_with_what_they_are_for_and_long_ended_ones_are_not(self):
        out = observe_board_agents(self.s, [agent("build-3349", "running"), agent("old", "reported", minutes_ago=3000)], clock=lambda: NOW)
        self.assertEqual(1, out["agents"])
        self.assertEqual("running · builder · card PR-3349 · beat 5m ago", self.status("build-3349"))
        self.assertIsNone(self.s.find("board agent old"))

    def test_a_heartbeat_alone_confirms_and_a_state_change_adds_the_final_value(self):
        observe_board_agents(self.s, [agent("a1", "running", 5)], clock=lambda: NOW)
        first = self.status("a1")
        r = observe_board_agents(self.s, [agent("a1", "running", 1)], clock=lambda: NOW)
        self.assertEqual([], r["changed"])
        self.assertEqual(first, self.status("a1"))                      # no new state for a newer heartbeat
        observe_board_agents(self.s, [agent("a1", "reported", 1)], clock=lambda: NOW)
        self.assertTrue(self.status("a1").startswith("reported"))     # a known agent that ended gets its final value once

    def test_observe_everything_reads_the_board_only_when_given_a_source_and_survives_it_failing(self):
        r = observe_everything(self.s, repos=[], build_repos=[], transcripts=[])
        self.assertNotIn("board_agents", r)

        def down():
            raise OSError("board down")
        r = observe_everything(self.s, repos=[], build_repos=[], transcripts=[], get_board_agents=down)
        self.assertEqual(["board agents: board down"], r["errors"])


class WhatsRunning(unittest.TestCase):
    def test_the_roll_up_lists_running_board_agents_the_last_release_and_recent_pending_notes(self):
        s = fresh_store()
        observe_board_agents(s, [agent("build-3349", "running"), agent("done-one", "running")], clock=lambda: datetime.now(timezone.utc))
        observe_board_agents(s, [agent("done-one", "reported")], clock=lambda: datetime.now(timezone.utc))
        s.add_state("Build 7.15.95.13801", "Build 7.15.95.13801 has shipped.", evidence=[ev()], writer="w", trust="verified", learned_how="read")
        s.add_state("install-supervisor-task.ps1", "Installing the supervisor task is pending for John.", evidence=[ev()], writer="w", trust="verified",
                    learned_how="read")
        lines = "\n".join(whats_running(s))
        self.assertIn("build-3349: running", lines)
        self.assertNotIn("done-one", lines)
        self.assertIn("Last release: Build 7.15.95.13801", lines)
        self.assertIn("pending for John", lines)
        self.assertIn("What's running", session_block(s))


if __name__ == "__main__":
    unittest.main()
