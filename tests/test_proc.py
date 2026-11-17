import os
import subprocess
import unittest
from pathlib import Path
from unittest import mock

import support  # noqa: F401
from statekeeper import proc

ROOT = Path(__file__).resolve().parents[1]


class Quiet(unittest.TestCase):
    def test_children_get_no_console_window_on_windows_and_never_read_the_callers_stdin(self):
        with mock.patch.object(subprocess, "run") as r:
            proc.run(["gh", "--version"], capture_output=True)
        kwargs = r.call_args.kwargs
        self.assertEqual(subprocess.DEVNULL, kwargs["stdin"])
        if os.name == "nt":
            self.assertEqual(0x08000000, kwargs["creationflags"] & 0x08000000)

    def test_input_and_explicit_stdin_are_respected(self):
        with mock.patch.object(subprocess, "run") as r:
            proc.run(["x"], input="data")
            self.assertNotIn("stdin", r.call_args.kwargs)
            proc.run(["x"], stdin=subprocess.PIPE)
            self.assertEqual(subprocess.PIPE, r.call_args.kwargs["stdin"])

    def test_the_writers_and_the_nightly_job_run_children_only_through_the_helper(self):
        for rel in ("src/statekeeper/writers/live.py", "src/statekeeper/writers/github_pr.py", "src/statekeeper/writers/ado_build.py", "tools/nightly.py"):
            text = (ROOT / rel).read_text(encoding="utf-8")
            self.assertNotIn("subprocess.run(", text, rel)
            self.assertIn("run(", text, rel)


if __name__ == "__main__":
    unittest.main()
