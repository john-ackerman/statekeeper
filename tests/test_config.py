import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import support  # noqa: F401
from statekeeper import config


class Config(unittest.TestCase):
    def test_the_file_named_by_the_environment_wins_and_lists_come_back_as_strings(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d, "c.json")
            path.write_text(json.dumps({"github_repos": ["o/a", "o/b"], "ado_org": "https://example.invalid/org"}), encoding="utf-8")
            with mock.patch.dict(os.environ, {"STATEKEEPER_CONFIG": str(path)}):
                self.assertEqual(["o/a", "o/b"], config.get_list("github_repos"))
                self.assertEqual("https://example.invalid/org", config.load()["ado_org"])
                self.assertEqual([], config.get_list("build_repos"))

    def test_a_broken_or_non_object_file_means_no_configuration(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d, "c.json")
            for text in ("{not json", "[1, 2]"):
                path.write_text(text, encoding="utf-8")
                with mock.patch.dict(os.environ, {"STATEKEEPER_CONFIG": str(path)}):
                    self.assertEqual({}, config.load())


if __name__ == "__main__":
    unittest.main()
