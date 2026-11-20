import tempfile
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path

from support import fresh_store
from statekeeper.session import session_block, whats_running
from statekeeper.writers.board_asks import observe_board_asks
from statekeeper.writers.live import observe_everything
from statekeeper.writers.watcher_health import observe_watcher, status_text


def ask(aid, title="CRM-1 decide the flag default", kind="decide", created="2026-11-16T18:12:03.602Z", done=False):
    a = {"id": aid, "title": title, "kind": kind, "createdAt": created, "updatedAt": created, "body": "SECRET BODY NOT STORED"}
    if done:
        a.update({"doneByJohn": True, "doneAt": "2026-11-18T20:56:33.135Z"})
    return a


def cur(store, thing, key):
    t = store.find(thing)
    c = [s for s in store.states_of(t.id) if s.state_key == key and s.status == "active"] if t else []
    return c[0].text if c else None


class BoardAsks(unittest.TestCase):
    def setUp(self):
        self.s = fresh_store()

    def test_open_asks_are_written_by_title_only_and_done_ones_the_store_never_saw_are_not(self):
        out = observe_board_asks(self.s, [ask("a1"), ask("a2", done=True)])
        self.assertEqual(1, out["open"])
        self.assertEqual("open · decide · since 2026-11-16 · CRM-1 decide the flag default", cur(self.s, "board ask a1", "ask.state"))
        self.assertIsNone(self.s.find("board ask a2"))
        self.assertNotIn("SECRET", cur(self.s, "board ask a1", "ask.state"))

    def test_a_reread_confirms_and_a_done_or_removed_ask_gets_its_final_value_once(self):
        observe_board_asks(self.s, [ask("a1"), ask("a3")])
        again = observe_board_asks(self.s, [ask("a1"), ask("a3")])
        self.assertEqual([], again["changed"])
        observe_board_asks(self.s, [ask("a1", done=True)])                 # a1 done, a3 removed from the board
        self.assertTrue(cur(self.s, "board ask a1", "ask.state").startswith("done"))
        self.assertTrue(cur(self.s, "board ask a3", "ask.state").startswith("gone"))

    def test_the_cap_keeps_the_newest_open_asks(self):
        asks = [ask(f"x{i}", created=f"2026-11-{10 + i % 9:02d}T00:00:00Z") for i in range(60)]
        self.assertEqual(40, observe_board_asks(self.s, asks)["open"])

    def test_the_board_being_down_is_quiet_and_other_failures_are_recorded(self):
        def down():
            raise ConnectionRefusedError("down")

        def broken():
            raise ValueError("bad json")
        self.assertEqual([], observe_everything(self.s, repos=[], build_repos=[], transcripts=[], get_board_asks=down)["errors"])
        self.assertEqual(["board asks: bad json"], observe_everything(self.s, repos=[], build_repos=[], transcripts=[], get_board_asks=broken)["errors"])

    def test_the_roll_up_lists_open_asks_for_john_but_not_finished_ones(self):
        observe_board_asks(self.s, [ask("a1", title="Decide the Lob flag default"), ask("a2", title="Old finished one")])
        observe_board_asks(self.s, [ask("a1", title="Decide the Lob flag default"), ask("a2", title="Old finished one", done=True)])
        lines = "\n".join(whats_running(self.s))
        self.assertIn("Decide the Lob flag default", lines)
        self.assertNotIn("Old finished one", lines)
        self.assertIn("Open asks on the board", session_block(self.s))


class WatcherHealth(unittest.TestCase):
    def run_watcher(self, store, directory, up=True, beat_age_s=60, now=2_000_000_000.0):
        (directory / "orch-watch-heartbeat").write_text(str(int(now - beat_age_s)), encoding="utf-8")
        return observe_watcher(store, directory=directory, server_up=lambda: up, now=lambda: now)

    def test_states_by_heartbeat_age_pause_flag_and_server(self):
        self.assertTrue(status_text(5, False, True).startswith("running · board server answering"))
        self.assertTrue(status_text(60, False, True).startswith("heartbeat stale"))
        self.assertTrue(status_text(600, False, True).startswith("not running"))
        self.assertTrue(status_text(600, True, True).startswith("paused"))
        self.assertIn("NOT answering", status_text(5, False, False))
        self.assertTrue(status_text(None, False, True).startswith("no heartbeat file"))

    def test_the_heartbeat_file_is_read_and_a_moving_age_only_confirms(self):
        s = fresh_store()
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            self.run_watcher(s, d, beat_age_s=60)
            self.assertEqual("running · board server answering · last heartbeat 1m ago", cur(s, "work-board watcher", "watcher.status"))
            self.assertEqual([], self.run_watcher(s, d, beat_age_s=180)["changed"])
            self.run_watcher(s, d, up=False, beat_age_s=60)
            self.assertIn("NOT answering", cur(s, "work-board watcher", "watcher.status"))
            (d / "orch-watch.paused").write_text("")
            self.run_watcher(s, d, beat_age_s=3 * 3600)
            self.assertTrue(cur(s, "work-board watcher", "watcher.status").startswith("paused"))

    def test_a_missing_directory_does_not_raise_and_the_roll_up_shows_the_watcher(self):
        s = fresh_store()
        observe_watcher(s, directory=Path(tempfile.gettempdir()) / "sk-no-such-dir", server_up=lambda: False)
        self.assertIn("work-board watcher: no heartbeat file", "\n".join(whats_running(s)))
        self.assertGreater(time.time(), 0)
        self.assertIsNotNone(datetime.now(timezone.utc))


if __name__ == "__main__":
    unittest.main()
