import tempfile
import unittest
from pathlib import Path

from support import fresh_store
from statekeeper.writers.skills import commands, observe_skills, parse_skill

SKILL = """---
name: flag-audit
description: >
  Weekly audit of feature flags against the code.
  Use when asked which flags can be cleaned up.
---
Run `ldcli flags list --project p` then `ldcli flags get --flag f`; pipe through `jq -r .items` and `jq .x`. See `driver.sh` and `git status`.
"""


class SkillsWriter(unittest.TestCase):
    def test_frontmatter_and_commands_are_read(self):
        self.assertEqual(("flag-audit", "Weekly audit of feature flags against the code. Use when asked which flags can be cleaned up."), parse_skill(SKILL))
        self.assertEqual(["jq", "ldcli"], commands(SKILL))

    def test_each_skill_is_two_ordinary_states_and_a_rerun_only_confirms_them(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "flag-audit").mkdir()
            (Path(d) / "flag-audit" / "SKILL.md").write_text(SKILL, encoding="utf-8")
            s = fresh_store()
            self.assertEqual({"skills": 1, "changed": 2}, observe_skills(s, [d]))
            states = s.states_of(s.find("flag-audit skill").id)
            self.assertEqual(2, len(states))
            self.assertTrue(all(x.state_key is None and x.status == "active" and x.volatility == "stable" for x in states))
            self.assertIn("ldcli", " ".join(x.text for x in states))
            self.assertEqual({"skills": 1, "changed": 0}, observe_skills(s, [d]))
            (Path(d) / "flag-audit" / "SKILL.md").write_text(SKILL.replace("Weekly", "Daily"), encoding="utf-8")
            self.assertEqual({"skills": 1, "changed": 1}, observe_skills(s, [d]))
            texts = [x.text for x in s.states_of(s.find("flag-audit skill").id)]
            self.assertEqual(2, len(texts))
            self.assertTrue(any("Daily audit" in t for t in texts))


if __name__ == "__main__":
    unittest.main()
