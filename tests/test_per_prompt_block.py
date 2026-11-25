"""The per-prompt block (`surface(..., per_prompt=True)`, what the inject hook asks for) leaves out what the live probes already show:
open board asks reach it only through their own words (a generated ask only through its id). `sk recall` and the default `surface` are unchanged."""
import unittest

from support import ThemeEmbedder, ev, fresh_store
from statekeeper import surface


class PerPromptBlock(unittest.TestCase):
    def setUp(self):
        self.s = fresh_store()
        for i in range(30):
            self.s.add_state(f"filler {i}", f"filler state number {i} about unrelated pipelines", evidence=[ev(f"f{i}")], writer="w", trust="derived",
                             learned_how="inferred")

    def ask(self, aid, text):
        return self.s.add_state(f"board ask {aid}", text, evidence=[ev(aid, text)], writer="observer:board-asks", trust="verified", learned_how="read",
                                state_key="ask.state", volatility="live")

    def add(self, thing, text, **kw):
        kw = {"writer": "w", "trust": "derived", "learned_how": "inferred", "volatility": "work", **kw}
        return self.s.add_state(thing, text, evidence=[ev(text[:12], text)], **kw)

    def ids(self, text, floor=0.78, **kw):
        out = surface(self.s, text, ThemeEmbedder(), floor=floor, **kw)
        return [x.id for it in out.items for x in it.states]

    # B: open asks are the probes' business
    def test_an_open_ask_that_only_sounds_like_the_input_stays_out_of_the_per_prompt_block(self):
        a = self.ask("auto-stalled-1", "open · stalled · since 2026-11-25 · Slack stays quiet")
        prompt = "is the board watcher alive"
        self.assertIn(a.id, self.ids(prompt), "the case must be reachable by meaning without the flag")
        self.assertNotIn(a.id, self.ids(prompt, per_prompt=True))

    def test_an_open_ask_still_surfaces_when_the_input_says_its_title_words(self):
        a = self.ask("ask-1", "open · decide · since 2026-11-23 · Plan the LaunchDarkly flag rollouts for receipting")
        self.assertIn(a.id, self.ids("LaunchDarkly flag rollouts", per_prompt=True))

    def test_a_generated_ask_is_not_brought_up_by_the_number_of_the_pr_it_is_about(self):
        ask = self.ask("auto-look-v2-3443", "open · look · since 2026-11-24 · Look: VirtuousV2#3443 CRM-2085 Date mail receipt export")
        pr = self.add("VirtuousV2 PR 3443", "draft, open", state_key="pr.state", volatility="live", trust="verified")
        self.assertIn(ask.id, self.ids("is 3443 approved"), "without the flag the PR number names the ask too")
        got = self.ids("is 3443 approved", per_prompt=True)
        self.assertNotIn(ask.id, got)
        self.assertIn(pr.id, got)

    def test_a_generated_ask_surfaces_when_the_input_says_its_id(self):
        ask = self.ask("auto-look-v2-3443", "open · look · since 2026-11-24 · Look: VirtuousV2#3443 CRM-2085 Date mail receipt export")
        self.assertIn(ask.id, self.ids("what is board ask auto-look-v2-3443 about", per_prompt=True))

    def test_asking_for_the_asks_still_lists_the_open_ones(self):
        a = self.ask("ask-1", "open · decide · since 2026-11-23 · Plan the LaunchDarkly flag rollouts")
        self.assertIn(a.id, self.ids("what is the priority order of asks", per_prompt=True))


if __name__ == "__main__":
    unittest.main()
