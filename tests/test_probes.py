"""Live probes: abnormal lines always, other lines only when new or changed since this session's last turn, the first turn quiet but for the
abnormal, a slow probe never holds the prompt past the deadline, a failing probe never breaks the block, and the block stays short."""
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "adapters" / "claude_code"))

import support  # noqa: F401,E402  (puts src on the path)
import probes  # noqa: E402
from probes import Probe, live_block  # noqa: E402


class Probes(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.home = Path(self.dir.name)
        self.asks = [{"key": "ask:1", "text": "ask (decide): pick a flag", "sig": "1", "abnormal": False}]
        self.spend = [{"key": "spend", "text": "spend cap: ok", "sig": "ok", "abnormal": False}]
        self.probes = [Probe("asks", lambda: list(self.asks), 0), Probe("spend", lambda: list(self.spend), 0)]

    def tearDown(self):
        self.dir.cleanup()

    def block(self, session="s1", probes=None):
        return live_block(self.home, session, probes=probes or self.probes, background=False)

    def test_first_turn_is_quiet_then_a_new_ask_shows_once(self):
        self.assertEqual("", self.block())
        self.asks.append({"key": "ask:2", "text": "ask (review): PR 3428", "sig": "2", "abnormal": False})
        time.sleep(0.01)
        got = self.block()
        self.assertIn("PR 3428", got)
        self.assertNotIn("pick a flag", got)
        time.sleep(0.01)
        self.assertEqual("", self.block())

    def test_abnormal_lines_show_every_turn_even_the_first(self):
        self.spend[0] = {"key": "spend", "text": "spend cap: paused", "sig": "paused", "abnormal": True}
        self.assertIn("paused", self.block())
        time.sleep(0.01)
        self.assertIn("paused", self.block())

    def test_sessions_are_diffed_separately(self):
        self.block("a")
        self.asks.append({"key": "ask:2", "text": "ask: new one", "sig": "2", "abnormal": False})
        time.sleep(0.01)
        self.assertEqual("", self.block("b"))                      # b's first turn
        self.assertIn("new one", self.block("a"))

    def test_a_slow_probe_does_not_hold_the_prompt(self):
        slow = Probe("slow", lambda: time.sleep(5) or [{"key": "x", "text": "late", "sig": "1", "abnormal": True}], 0)
        t0 = time.time()
        self.block(probes=[slow, *self.probes])
        self.assertLess(time.time() - t0, probes.DEADLINE + 1.0)

    def test_a_failing_probe_keeps_its_last_value_and_never_breaks_the_block(self):
        self.spend[0] = {"key": "spend", "text": "spend cap: paused", "sig": "paused", "abnormal": True}
        self.block()
        boom = Probe("spend", lambda: 1 / 0, 0)
        time.sleep(0.01)
        self.assertIn("paused", self.block(probes=[self.probes[0], boom]))

    def test_the_block_is_short(self):
        many = [{"key": f"health:{i}", "text": "FAIL check " + "x" * 80, "sig": "FAIL", "abnormal": True} for i in range(20)]
        got = self.block(probes=[Probe("board-health", lambda: many, 0)])
        self.assertLess(len(got), probes.MAX_CHARS + 250)
        self.assertIn("more", got)

    def test_a_slow_probe_runs_only_in_the_background(self):
        called = []
        bg = Probe("board-health", lambda: called.append(1) or [], 0, inline=False)
        self.block(probes=[bg])
        self.assertEqual([], called)


if __name__ == "__main__":
    unittest.main()
