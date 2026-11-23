"""A live value the observers no longer see is closed ("no longer observed"), so it stops standing as current; and recall never shows a stale live
value as current unless the input names its thing."""
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from support import ThemeEmbedder, ev, fresh_store
from statekeeper import surface
from statekeeper.writers.board_agents import observe_board_agents
from statekeeper.writers.board_asks import observe_board_asks
from statekeeper.writers.common import NOT_OBSERVED
from statekeeper.writers.live import observe_everything, recent_transcripts
from test_ado_build import build
from test_board_agents import agent
from test_board_asks_watcher import ask
from test_writers import payload


def active(store, thing):
    t = store.find(thing)
    return [s for s in store.states_of(t.id) if s.status == "active"] if t else []


class BoardAsksGone(unittest.TestCase):
    def test_an_ask_the_board_no_longer_lists_is_closed_not_kept_as_a_gone_value(self):
        s = fresh_store()
        observe_board_asks(s, [ask("a1"), ask("a3")])
        out = observe_board_asks(s, [ask("a1", done=True)])                # a1 done (still listed), a3 removed
        self.assertTrue(active(s, "board ask a1")[0].text.startswith("done"))
        self.assertEqual([], active(s, "board ask a3"))
        self.assertIn("a3:closed", out["changed"])

    def test_a_done_ask_that_later_leaves_the_board_is_closed_too(self):
        s = fresh_store()
        observe_board_asks(s, [ask("a1")])
        observe_board_asks(s, [ask("a1", done=True)])
        observe_board_asks(s, [])
        self.assertEqual([], active(s, "board ask a1"))

    def test_a_gone_value_left_by_the_earlier_writer_is_closed_on_the_next_read(self):
        s = fresh_store()
        s.add_state("board ask old", "gone · decide · since 2026-11-16 · x", evidence=[ev()], writer="observer:board-asks", trust="verified",
                    learned_how="read", state_key="ask.state", volatility="live")
        observe_board_asks(s, [ask("a1")])
        self.assertEqual([], active(s, "board ask old"))
        self.assertIn(NOT_OBSERVED, " ".join(e.payload for e in s.events(thing_id=s.find("board ask old").id)))


class BoardAgentsGone(unittest.TestCase):
    def test_a_known_agent_the_board_stops_reporting_recently_is_closed(self):
        s = fresh_store()
        now = datetime.now(timezone.utc)
        observe_board_agents(s, [agent("b1", "running", now=now), agent("b2", "running", now=now)], clock=lambda: now)
        observe_board_agents(s, [agent("b1", "running", now=now)], clock=lambda: now)
        self.assertEqual(1, len(active(s, "board agent b1")))
        self.assertEqual([], active(s, "board agent b2"))


class ObserveEverythingGone(unittest.TestCase):
    def test_a_merged_pr_is_read_once_more_then_closed_and_its_old_build_too(self):
        s = fresh_store()
        kw = dict(repos=("o/ExampleApp",), build_repos=("ExampleApp",), get_build=lambda ref: build(id=100 + int(ref.split("/")[2])))
        observe_everything(s, list_prs=lambda r: [7, 8], get_pr=lambda repo, n: payload(), **kw)
        observe_everything(s, list_prs=lambda r: [8], get_pr=lambda repo, n: payload(state="MERGED", isDraft=False) if n == 7 else payload(), **kw)
        self.assertEqual("merged", next(x.text for x in active(s, "ExampleApp PR 7") if x.state_key == "pr.state"))   # the final value is read once
        self.assertEqual([], active(s, "ci-pipeline #107"))                 # no longer the newest build of an open PR
        observe_everything(s, list_prs=lambda r: [8], get_pr=lambda repo, n: payload(), **kw)
        self.assertEqual([], active(s, "ExampleApp PR 7"))
        self.assertEqual(3, len(active(s, "ExampleApp PR 8")))
        self.assertTrue(active(s, "ci-pipeline #108"))

    def test_a_failed_listing_closes_nothing(self):
        s = fresh_store()
        observe_everything(s, repos=("o/ExampleApp",), build_repos=(), list_prs=lambda r: [7], get_pr=lambda repo, n: payload(), get_build=lambda ref: None)

        def down(repo):
            raise RuntimeError("gh down")
        observe_everything(s, repos=("o/ExampleApp",), build_repos=(), list_prs=down, get_pr=lambda repo, n: payload(), get_build=lambda ref: None)
        self.assertEqual(3, len(active(s, "ExampleApp PR 7")))

    def test_a_pr_whose_read_fails_is_not_closed(self):
        s = fresh_store()
        observe_everything(s, repos=("o/ExampleApp",), build_repos=(), list_prs=lambda r: [7], get_pr=lambda repo, n: payload(), get_build=lambda ref: None)

        def broken(repo, n):
            raise RuntimeError("rate limited")
        observe_everything(s, repos=("o/ExampleApp",), build_repos=(), list_prs=lambda r: [7], get_pr=broken, get_build=lambda ref: None)
        self.assertEqual(3, len(active(s, "ExampleApp PR 7")))

    def test_an_agent_whose_transcript_went_quiet_is_closed_only_when_transcripts_were_read(self):
        s = fresh_store()
        with tempfile.TemporaryDirectory() as d:
            t = Path(d, "p", "s", "subagents")
            t.mkdir(parents=True)
            (t / "agent-abc.jsonl").write_text(json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": "x"}], "stop_reason": "tool_use"}}),
                                               encoding="utf-8")
            observe_everything(s, repos=[], build_repos=[], transcripts=recent_transcripts(Path(d)))
        self.assertTrue(active(s, "agent-abc"))
        observe_everything(s, repos=[], build_repos=[])                      # transcripts not read: nothing is closed
        self.assertTrue(active(s, "agent-abc"))
        observe_everything(s, repos=[], build_repos=[], transcripts=[])      # read, and it is not among the recent ones
        self.assertEqual([], active(s, "agent-abc"))

    def test_other_writers_states_on_the_same_thing_are_left_alone(self):
        s = fresh_store()
        observe_everything(s, repos=("o/ExampleApp",), build_repos=(), list_prs=lambda r: [7], get_pr=lambda repo, n: payload(), get_build=lambda ref: None)
        note = s.add_state("ExampleApp PR 7", "Jared asked for a split", evidence=[ev()], writer="agent:x", trust="derived", learned_how="inferred")
        observe_everything(s, repos=("o/ExampleApp",), build_repos=(), list_prs=lambda r: [], get_pr=lambda repo, n: payload(state="CLOSED", isDraft=False),
                           get_build=lambda ref: None)
        observe_everything(s, repos=("o/ExampleApp",), build_repos=(), list_prs=lambda r: [], get_pr=lambda repo, n: payload(), get_build=lambda ref: None)
        self.assertEqual([note.id], [x.id for x in active(s, "ExampleApp PR 7")])


class StaleLiveInRecall(unittest.TestCase):
    def setUp(self):
        self.s = fresh_store()
        self.st = self.s.add_state("slack relay", "the board watch relay is up and the slack watcher is running", evidence=[ev()], writer="rule:w",
                                   trust="verified", learned_how="read", volatility="live")

    def test_a_fresh_live_state_surfaces_by_meaning(self):
        out = surface(self.s, "is the slack board watch working", ThemeEmbedder(), floor=0.5)
        self.assertIn(f"#{self.st.id}", out.block)

    def test_a_stale_live_state_does_not_surface_by_meaning(self):
        later = datetime.now(timezone.utc) + timedelta(hours=20)
        out = surface(self.s, "is the slack board watch working", ThemeEmbedder(), floor=0.5, today=later)
        self.assertNotIn(f"#{self.st.id}", out.block)

    def test_a_stale_live_state_still_surfaces_marked_stale_when_its_thing_is_named(self):
        later = datetime.now(timezone.utc) + timedelta(hours=20)
        out = surface(self.s, "is the slack relay still running", ThemeEmbedder(), floor=0.5, today=later)
        self.assertIn(f"#{self.st.id}", out.block)
        self.assertIn("STALE", out.block)


if __name__ == "__main__":
    unittest.main()
