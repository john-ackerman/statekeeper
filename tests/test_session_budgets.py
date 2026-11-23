"""The session block puts the live sections first and gives each section its own budget, so a long core can no longer push 'Tracked now' and
'What's running' off the end; and the shadow record of a session start is the text the live hook injects, not a shorter cut of it."""
import unittest

from support import ev, fresh_store
from statekeeper.session import session_block


def filled():
    s = fresh_store()
    s.add_state("Claude", "Claude is the agent.", evidence=[ev()], writer="w", trust="owner", learned_how="told", volatility="stable")
    s.set_kind("Claude", "agent")
    for i in range(400):                                             # a core far larger than the whole budget
        s.add_state("Claude", f"Standing rule number {i}: " + "keep it short and plain " * 4, evidence=[ev(f"r{i}")], writer="w", trust="owner",
                    learned_how="told", volatility="rule", kind="rule")
    s.add_state("ExampleApp PR 7", "open", evidence=[ev("p")], writer="rule:github-pr", trust="verified", learned_how="read", state_key="pr.state",
                volatility="live")
    return s


class Budgets(unittest.TestCase):
    def test_tracked_now_survives_a_core_larger_than_the_budget(self):
        block = session_block(filled(), budget=9000)
        self.assertIn("Tracked now", block)
        self.assertIn("ExampleApp PR 7: open", block)
        self.assertLessEqual(len(block), 9000)

    def test_live_sections_come_before_the_core(self):
        block = session_block(filled(), budget=16000)
        self.assertLess(block.index("Tracked now"), block.index("Core:"))

    def test_a_cut_section_says_so(self):
        self.assertIn("[core truncated", session_block(filled(), budget=9000))


class ShadowRecord(unittest.TestCase):
    def test_the_shadow_session_start_record_uses_the_live_hooks_budget(self):
        import sys
        from pathlib import Path
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "adapters" / "claude_code"))
        import shadow_hook
        import inject_hook
        self.assertEqual(inject_hook.SESSION_CAP, shadow_hook.SESSION_CAP)
        s = filled()
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            shadow_hook.handle({"hook_event_name": "SessionStart", "session_id": "x", "source": "startup"}, s, None, Path(d))
        recorded = s.injections(source="statekeeper")[-1][-2] if s.injections(source="statekeeper") else ""
        self.assertEqual(session_block(s, budget=shadow_hook.SESSION_BLOCK_BUDGET), recorded)


if __name__ == "__main__":
    unittest.main()
