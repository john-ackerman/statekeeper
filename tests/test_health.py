import json
import unittest

import support  # noqa: F401  (puts src on the path)
from statekeeper.report import health_text, injection_health


class Health(unittest.TestCase):
    def test_the_daily_health_counts_live_prompts_quiet_turns_session_starts_and_errors_since_a_time(self):
        log = [json.dumps(e) for e in [
            {"event": "UserPromptSubmit", "live": True, "block_chars": 400, "ms": 300, "at": "2026-11-18T10:00:00Z"},
            {"event": "UserPromptSubmit", "live": True, "block_chars": 0, "ms": 200, "at": "2026-11-18T10:01:00Z"},
            {"event": "UserPromptSubmit", "live": False, "block_chars": 900, "ms": 9000, "at": "2026-11-18T10:02:00Z"},   # shadow only: not counted
            {"event": "inject-session-start", "refresh": "exit 1", "ms": 1200, "at": "2026-11-18T10:03:00Z"},
            {"event": "inject-error", "error": "OperationalError: locked", "at": "2026-11-18T10:04:00Z"},
            {"event": "UserPromptSubmit", "live": True, "block_chars": 999, "ms": 1, "at": "2026-11-17T10:00:00Z"},      # before the window
        ]] + ["not json"]
        h = injection_health(log, "2026-11-18T00:00:00Z")
        self.assertEqual((2, 1, 1, 400, 1200, 1, 1, 1), (h["live_prompts"], h["injected"], h["quiet"], h["mean_injected_chars"], h["slowest_ms"],
                                                           h["session_starts"], h["refresh_failed"], h["error_count"]))
        text = health_text(h)
        self.assertIn(": CHECK", text)
        self.assertIn("OperationalError", text)
        self.assertIn(": ok", health_text(injection_health(log[:2], "2026-11-18T00:00:00Z")))


if __name__ == "__main__":
    unittest.main()
