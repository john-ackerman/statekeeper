"""Board asks and the watcher are live things whose names are opaque ("board ask ask-2026...", "work-board watcher"); the audit's regression set
(eval/cases-audit-20261123.json) showed the prompts that need them got nothing. An ask's title reaches an input that shares its words, "the asks"
lists the open ones, and "the watcher" names the watcher."""
import unittest

from support import ThemeEmbedder, ev, fresh_store
from statekeeper import surface
from statekeeper.writers.watcher_health import observe_watcher


class AskRouting(unittest.TestCase):
    def setUp(self):
        self.s = fresh_store()
        for i in range(30):
            self.s.add_state(f"filler {i}", f"filler state number {i} about unrelated pipelines", evidence=[ev(f"f{i}")], writer="w", trust="derived",
                             learned_how="inferred")

    def ask(self, aid, text):
        return self.s.add_state(f"board ask {aid}", text, evidence=[ev(aid, text)], writer="observer:board-asks", trust="verified", learned_how="read",
                                state_key="ask.state", volatility="live")

    def test_an_asks_title_reaches_an_input_sharing_its_words(self):
        a = self.ask("ask-1", "open · decide · since 2026-11-23 · Plan the LaunchDarkly flag rollouts for receipting")
        out = surface(self.s, "LaunchDarkly flag rollouts", ThemeEmbedder(), floor=0.78)
        self.assertIn(f"#{a.id}", out.block)

    def test_an_unrelated_asks_title_stays_out(self):
        a = self.ask("ask-2", "open · decide · since 2026-11-23 · Plan the LaunchDarkly flag rollouts for receipting")
        self.assertNotIn(f"#{a.id}", surface(self.s, "statement jobs finished state", ThemeEmbedder(), floor=0.78).block)

    def test_asking_about_the_asks_lists_the_open_ones_not_the_done_ones(self):
        a = self.ask("ask-1", "open · decide · since 2026-11-23 · Plan the LaunchDarkly flag rollouts")
        b = self.ask("ask-2", "open · deploy · since 2026-11-20 · SQL #142 rollout")
        d = self.ask("ask-3", "done · decide · since 2026-11-19 · Old one")
        out = surface(self.s, "what is the priority order of asks", None)
        self.assertIn(f"#{a.id}", out.block)
        self.assertIn(f"#{b.id}", out.block)
        self.assertNotIn(f"#{d.id}", out.block)

    def test_a_passing_use_of_the_word_does_not_list_them(self):
        self.ask("ask-1", "open · decide · since 2026-11-23 · Plan the LaunchDarkly flag rollouts")
        self.assertEqual("", surface(self.s, "he asks a lot of questions about filler", None).block)


class WatcherByName(unittest.TestCase):
    def test_the_watcher_is_named_by_the_word_watcher(self):
        import tempfile
        from pathlib import Path
        s = fresh_store()
        with tempfile.TemporaryDirectory() as d:
            observe_watcher(s, directory=Path(d), server_up=lambda: True)
        out = surface(s, "is the watcher running", None)
        self.assertIn("work-board watcher", out.block)


if __name__ == "__main__":
    unittest.main()
