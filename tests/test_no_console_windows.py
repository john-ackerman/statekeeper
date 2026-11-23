"""Hooks, observers, the nightly job and probes run without a console. On Windows every child process started without CREATE_NO_WINDOW flashes a
console window ("Git CLI" bursts on every tool call), so all of them go through statekeeper.proc.run, and a detached Popen passes the flag itself."""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCANNED = [ROOT / "src", ROOT / "adapters", ROOT / "tools"]
ALLOWED = {ROOT / "src" / "statekeeper" / "proc.py"}               # the one place that calls subprocess.run, with the flag
DIRECT = re.compile(r"\bsubprocess\.(run|call|check_call|check_output|getoutput|getstatusoutput)\s*\(|\bos\.(system|popen|spawn\w*)\s*\(|"
                    r"^\s*from\s+subprocess\s+import\s+.*\b(run|call|check_call|check_output|Popen)\b", re.M)
POPEN = re.compile(r"\bPopen\s*\(")


def sources():
    return [p for d in SCANNED for p in d.rglob("*.py") if "__pycache__" not in p.parts]


class NoConsoleWindows(unittest.TestCase):
    def test_the_scan_sees_the_hook_adapters(self):
        names = {p.name for p in sources()}
        self.assertTrue({"tool_hook.py", "inject_hook.py", "shadow_hook.py", "observe_all.py", "nightly.py"} <= names)

    def test_no_module_runs_a_child_process_except_through_proc_run(self):
        bad = []
        for p in sources():
            if p in ALLOWED:
                continue
            for m in DIRECT.finditer(p.read_text(encoding="utf-8")):
                bad.append(f"{p.relative_to(ROOT)}: {m.group(0).strip()}")
        self.assertEqual([], bad, "use statekeeper.proc.run (CREATE_NO_WINDOW, stdin=DEVNULL)")

    def test_every_detached_popen_passes_create_no_window(self):
        bad = []
        for p in sources():
            text = p.read_text(encoding="utf-8")
            for m in POPEN.finditer(text):
                call = text[m.start():text.find("\n", text.find(")", m.end()))]
                if "creationflags" not in call or "CREATE_NO_WINDOW" not in text:
                    bad.append(f"{p.relative_to(ROOT)}: {call[:80]}")
        self.assertEqual([], bad)


if __name__ == "__main__":
    unittest.main()
