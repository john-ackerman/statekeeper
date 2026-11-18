"""The hooks and command line that replace an earlier memory system's lifecycle: subagent core, curation pass at a context threshold, tool-time
reminders (cues, existing definitions), the SessionEnd keep, and the `sk` command line."""
import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from support import ev, fresh_store
from statekeeper import SqliteStore

os.environ["STATEKEEPER_NO_OBSERVE_IN_TESTS"] = "1"
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "adapters" / "claude_code"))
sys.path.insert(0, str(ROOT / "tools"))
import curate_hook  # noqa: E402
import shadow_hook  # noqa: E402
import sk  # noqa: E402
import subagent_hook  # noqa: E402
import tool_hook  # noqa: E402


def run_sk(db: str, *argv: str, stdin: str = "") -> str:
    out = io.StringIO()
    old = sys.stdin
    sys.stdin = io.StringIO(stdin)
    try:
        with contextlib.redirect_stdout(out):
            sk.main(["--db", db, *argv])
    finally:
        sys.stdin = old
    return out.getvalue()


class SubagentCore(unittest.TestCase):
    def test_the_block_carries_the_core_and_working_note_and_says_how_to_look_and_write(self):
        s = fresh_store()
        s.add_thing("Agent", "agent")
        s.add_state("Agent", "Tests come first.", evidence=[ev()], writer="owner", trust="owner", learned_how="told", kind="rule", volatility="rule")
        s.add_state("working note", "WHERE I AM: shipping the importer", evidence=[ev()], writer="rule:t", trust="verified", learned_how="read",
                    state_key="note", is_state=True, volatility="live")
        text = subagent_hook.block(s)
        self.assertIn("Tests come first.", text)
        self.assertIn("WHERE I AM: shipping the importer", text)
        self.assertIn("sk.py\" recall", text)
        self.assertIn("add <thing>", text)

    def test_an_empty_store_gives_nothing(self):
        self.assertEqual("", subagent_hook.block(fresh_store()))


class CurationPass(unittest.TestCase):
    def transcript(self, d: str, tokens: int) -> str:
        p = Path(d) / "t.jsonl"
        p.write_text(json.dumps({"type": "assistant", "message": {"usage": {"input_tokens": tokens, "cache_read_input_tokens": 0}}}) + "\n", encoding="utf-8")
        return str(p)

    def test_fires_once_past_the_threshold_and_not_below_it(self):
        with tempfile.TemporaryDirectory() as d:
            ev_ = {"session_id": "s1", "transcript_path": self.transcript(d, 400_000)}
            self.assertEqual("", curate_hook.decide(ev_, Path(d), 500_000))
            ev_["transcript_path"] = self.transcript(d, 600_000)
            reason = curate_hook.decide(ev_, Path(d), 500_000)
            self.assertIn("600,000 tokens", reason)
            self.assertIn("note --content", reason)
            self.assertEqual("", curate_hook.decide(ev_, Path(d), 500_000))      # once per stretch

    def test_never_fires_while_a_stop_hook_is_already_active_or_when_off(self):
        with tempfile.TemporaryDirectory() as d:
            ev_ = {"session_id": "s1", "transcript_path": self.transcript(d, 900_000), "stop_hook_active": True}
            self.assertEqual("", curate_hook.decide(ev_, Path(d), 500_000))
            ev_.pop("stop_hook_active")
            self.assertEqual("", curate_hook.decide(ev_, Path(d), 0))


class ToolTime(unittest.TestCase):
    def test_new_definitions_are_read_from_a_write_and_from_the_new_side_of_an_edit(self):
        with tempfile.TemporaryDirectory() as d:
            path = str(Path(d) / "m.py")
            Path(path).write_text("def old_one():\n    pass\n", encoding="utf-8")
            _, names = tool_hook.new_names({"tool_name": "Write", "tool_input": {"file_path": path, "content": "def old_one():\n    pass\ndef load_qa_session():\n    pass\n"}})
            self.assertEqual(["load_qa_session"], names)
            _, names = tool_hook.new_names({"tool_name": "Edit", "tool_input": {"file_path": path, "old_string": "def old_one():", "new_string": "def old_one():\n    pass\nclass QaLogin:"}})
            self.assertEqual(["QaLogin"], names)

    def test_existing_definitions_with_the_same_name_or_words_are_named(self):
        with tempfile.TemporaryDirectory() as d:
            subprocess.run(["git", "init", "-q"], cwd=d, check=True)
            Path(d, "a.py").write_text("def refresh_qa_session():\n    pass\n", encoding="utf-8")
            Path(d, "b.py").write_text("def refresh_qa_session_state():\n    pass\n", encoding="utf-8")
            subprocess.run(["git", "add", "-A"], cwd=d, check=True)
            lines = tool_hook.existing(str(Path(d) / "new.py"), ["refresh_qa_session"])
            self.assertTrue(any("already exists: a.py:1" in x for x in lines), lines)
            self.assertTrue(any("refresh_qa_session_state" in x for x in lines), lines)

    def test_a_cue_fires_on_its_moment_once_per_window(self):
        with tempfile.TemporaryDirectory() as d:
            Path(d, "cues.json").write_text(json.dumps([{"moment": "tool", "name": "search-first", "when": "save_issue", "text": "search before creating"},
                                                        {"moment": "prompt", "name": "other", "when": "save_issue", "text": "not a tool cue"}]), encoding="utf-8")
            self.assertEqual(["- search before creating"], tool_hook.cues(Path(d), "tool", "mcp__linear__save_issue: {}", "s1"))
            self.assertEqual([], tool_hook.cues(Path(d), "tool", "mcp__linear__save_issue: {}", "s1"))
            self.assertEqual(["- search before creating"], tool_hook.cues(Path(d), "tool", "mcp__linear__save_issue: {}", "s2"))
            self.assertEqual([], tool_hook.cues(Path(d), "tool", "Read: {}", "s3"))


class SessionEndKeep(unittest.TestCase):
    def setUp(self):
        self._inject = os.environ.pop("STATEKEEPER_INJECT", None)

    def tearDown(self):
        if self._inject is not None:
            os.environ["STATEKEEPER_INJECT"] = self._inject

    def test_session_end_keeps_the_last_answer_when_stop_did_not_and_never_labels_twice(self):
        with tempfile.TemporaryDirectory() as d:
            shadow_hook.HOME = Path(d)
            s = fresh_store()
            s.add_state("the converter", "caps replicas at 75", evidence=[ev()], writer="rule:t", trust="verified", learned_how="read")
            shadow_hook.handle({"hook_event_name": "UserPromptSubmit", "session_id": "s1", "prompt": "why does the converter cap replicas?"}, s, None, Path(d))
            rec = shadow_hook.handle({"hook_event_name": "SessionEnd", "session_id": "s1", "last_assistant_message": "the converter caps them"}, s, None, Path(d))
            self.assertEqual("SessionEnd", rec["event"])
            self.assertEqual(1, len(s.turn_texts("output")))
            self.assertEqual({}, shadow_hook.handle({"hook_event_name": "SessionEnd", "session_id": "s1", "last_assistant_message": "again"}, s, None, Path(d)))
            self.assertEqual([], s.outcomes(kind="used"))


class CommandLine(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.db = str(Path(self.dir.name) / "s.db")
        s = SqliteStore(self.db)
        s.add_state("qa-login", "Run node qa-login.mjs a to refresh the QA sessions.", evidence=[ev("persistence:996", "the whole fragment text")],
                    writer="rule:t", trust="derived", learned_how="inferred", volatility="stable")
        s.close()
        os.environ.pop("STATEKEEPER_SESSION", None)

    def tearDown(self):
        self.dir.cleanup()

    def test_search_finds_by_thing_text_or_evidence_and_show_prints_the_full_evidence(self):
        self.assertIn("[qa-login]", run_sk(self.db, "search", "qa-login", "refresh"))
        self.assertIn("[qa-login]", run_sk(self.db, "search", "whole", "fragment"))
        self.assertIn("nothing found", run_sk(self.db, "search", "datadog"))
        out = run_sk(self.db, "show", "#1")
        self.assertIn("the whole fragment text", out)
        self.assertIn("evidence message persistence:996", out)

    def test_add_writes_an_agent_claim_and_supersede_replaces_it_keeping_history(self):
        out = run_sk(self.db, "add", "pup", "-", "--stable", stdin="Datadog is queried with pup --read-only.")
        self.assertIn("proposed/claimed", out)
        sid = int(out.split("#", 1)[1].split()[0])
        out = run_sk(self.db, "supersede", str(sid), "Datadog is queried with pup --read-only or the MCP.")
        self.assertIn(f"#{sid} -> #", out)
        s = SqliteStore(self.db)
        self.assertFalse(s.get_state(sid).standing)
        self.assertEqual(["Datadog is queried with pup --read-only or the MCP."], [x.text for x in s.states_of(s.find("pup").id)])

    def test_owner_statements_stand_and_aliases_name_the_thing(self):
        self.assertIn("active/owner", run_sk(self.db, "add", "qa-login", "Agents never read .env.local.", "--owner", "--rule"))
        run_sk(self.db, "alias", "qa-login", "sign in to QA")
        self.assertEqual("qa-login", SqliteStore(self.db).find("sign in to QA").name)

    def test_the_working_note_is_replaced_and_read_back(self):
        self.assertIn("(no working note)", run_sk(self.db, "note"))
        run_sk(self.db, "note", "--content", "-", stdin="WHERE I AM: one")
        run_sk(self.db, "note", "--content", "WHERE I AM: two")
        self.assertEqual("WHERE I AM: two", run_sk(self.db, "note").strip())
        self.assertIn("WHERE I AM: two", run_sk(self.db, "core"))


if __name__ == "__main__":
    unittest.main()
