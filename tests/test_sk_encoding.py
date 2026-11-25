"""`sk` prints whatever the store holds; a state with an emoji must not crash the command on a console whose encoding cannot show it
(Windows cp1252), it prints a replacement character instead."""
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from support import ev
from statekeeper import SqliteStore

ROOT = Path(__file__).resolve().parents[1]


class SkConsoleEncoding(unittest.TestCase):
    def run_sk(self, db, *args):
        env = dict(os.environ, PYTHONIOENCODING="cp1252", STATEKEEPER_EMBED_URL="http://127.0.0.1:9/v1")
        env.pop("PYTHONUTF8", None)
        return subprocess.run([sys.executable, str(ROOT / "tools" / "sk.py"), "--db", db, *args], capture_output=True, env=env)

    def test_search_and_show_survive_an_emoji_on_a_cp1252_console(self):
        with tempfile.TemporaryDirectory() as d:
            db = str(Path(d) / "t.db")
            s = SqliteStore(db)
            st = s.add_state("Rollout plan", "the rollout is done \U0001F680 and verified", evidence=[ev("m1")], writer="w",
                             trust="verified", learned_how="read")
            s.close()
            for args in (("search", "rollout"), ("show", str(st.id))):
                r = self.run_sk(db, *args)
                self.assertEqual(0, r.returncode, r.stderr.decode("utf-8", "replace"))
                self.assertIn(b"rollout is done", r.stdout)


if __name__ == "__main__":
    unittest.main()
