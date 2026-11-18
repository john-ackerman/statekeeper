import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from support import ev, fresh_store
from statekeeper import SqliteStore

os.environ["STATEKEEPER_NO_OBSERVE_IN_TESTS"] = "1"          # the prompt handler must not start the live writers during tests
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "adapters" / "claude_code"))
import shadow_hook  # noqa: E402


def seeded():
    s = fresh_store()
    s.add_state("the converter", "caps replicas at 75 per environment", evidence=[ev()], writer="rule:t", trust="verified", learned_how="read")
    return s


class Handling(unittest.TestCase):
    """Shadow mode: the live flag must be off here even on a machine where it is on."""
    def setUp(self):
        self._inject = os.environ.pop("STATEKEEPER_INJECT", None)

    def tearDown(self):
        if self._inject is not None:
            os.environ["STATEKEEPER_INJECT"] = self._inject

    def test_a_prompt_logs_surfaced_outcomes_and_the_stop_labels_used_from_the_answer(self):
        with tempfile.TemporaryDirectory() as d:
            shadow_hook.HOME = Path(d)
            s = seeded()
            rec = shadow_hook.handle({"hook_event_name": "UserPromptSubmit", "session_id": "s1", "prompt": "why does the converter cap replicas?"}, s, None, Path(d))
            self.assertEqual(["the converter"], rec["things"])
            self.assertEqual(1, len(s.outcomes(kind="surfaced")))
            transcript = Path(d) / "t.jsonl"
            transcript.write_text("\n".join(json.dumps(x) for x in [
                {"type": "user", "message": {"content": "why does the converter cap replicas?"}},
                {"type": "assistant", "message": {"content": [{"type": "text", "text": "The converter caps replicas at 75 per environment. I'll check the quota next."}]}}]), encoding="utf-8")
            stop = shadow_hook.handle({"hook_event_name": "Stop", "session_id": "s1", "transcript_path": str(transcript)}, s, None, Path(d))
            self.assertEqual((1, 0, 1), (stop["used"], stop["missed"], stop["commitments"]))
            self.assertEqual(1, len(s.outcomes(kind="used")))
            self.assertEqual(["prompt", "output"], [k for k in ("prompt", "output") if s.turn_texts(k)])

    def test_what_legacy_injected_is_read_back_from_the_transcript_and_recorded_beside_what_statekeeper_would_have(self):
        import time as _time
        from datetime import datetime, timezone
        with tempfile.TemporaryDirectory() as d:
            shadow_hook.HOME = Path(d)
            s = seeded()
            shadow_hook.handle({"hook_event_name": "SessionStart", "session_id": "s1", "source": "startup"}, s, None, Path(d))
            shadow_hook.handle({"hook_event_name": "UserPromptSubmit", "session_id": "s1", "prompt": "why does the converter cap replicas?"}, s, None, Path(d))
            stamp = datetime.fromtimestamp(_time.time() + 1, timezone.utc).isoformat().replace("+00:00", "Z")
            lines = [
                {"type": "attachment", "timestamp": stamp, "attachment": {"type": "hook_additional_context", "hookName": "SessionStart", "content": ["# Who you are\ncore text"]}},
                {"type": "attachment", "timestamp": stamp, "attachment": {"type": "hook_additional_context", "hookName": "UserPromptSubmit", "content": ["Concept map for this turn (x)\n\nthe converter (thing): 1 accepted"]}},
                {"type": "attachment", "timestamp": stamp, "attachment": {"type": "hook_additional_context", "hookName": "UserPromptSubmit", "content": ["## Tracked now (15:00)\n- git: master"]}},
                {"type": "assistant", "message": {"content": [{"type": "text", "text": "ok"}], "stop_reason": "end_turn"}}]
            tp = Path(d) / "t.jsonl"
            tp.write_text("\n".join(json.dumps(x) for x in lines), encoding="utf-8")
            shadow_hook.handle({"hook_event_name": "Stop", "session_id": "s1", "transcript_path": str(tp)}, s, None, Path(d))
            theirs = [(i[2], i[3]) for i in s.injections(source="legacy")]
            self.assertIn(("UserPromptSubmit", "concept_map"), theirs)
            self.assertIn(("UserPromptSubmit", "tracked"), theirs)
            self.assertIn(("SessionStart", "session_core"), theirs)
            ours = s.injections(source="statekeeper")
            self.assertEqual({"UserPromptSubmit", "SessionStart:startup"}, {i[2] for i in ours})
            self.assertIn("caps replicas", [i[4] for i in ours if i[2] == "UserPromptSubmit"][0])

    def test_an_empty_prompt_and_an_unknown_event_do_nothing(self):
        with tempfile.TemporaryDirectory() as d:
            shadow_hook.HOME = Path(d)
            s = seeded()
            self.assertEqual({}, shadow_hook.handle({"hook_event_name": "UserPromptSubmit", "session_id": "s", "prompt": " "}, s, None, Path(d)))
            self.assertEqual({}, shadow_hook.handle({"hook_event_name": "Whatever"}, s, None, Path(d)))
            self.assertEqual([], s.outcomes())


class Inject(unittest.TestCase):
    def run_inject(self, prompt, db, home, flag):
        env = dict(os.environ, STATEKEEPER_HOME=home, STATEKEEPER_DB=db, PYTHONUTF8="1", STATEKEEPER_NO_OBSERVE_IN_TESTS="1")
        env.pop("STATEKEEPER_EMBED_URL", None)
        if flag is None:
            env.pop("STATEKEEPER_INJECT", None)
        else:
            env["STATEKEEPER_INJECT"] = flag
        return subprocess.run([sys.executable, str(ROOT / "adapters" / "claude_code" / "inject_hook.py")], input=json.dumps({"hook_event_name": "UserPromptSubmit", "session_id": "s9", "prompt": prompt}),
                              capture_output=True, text=True, env=env)

    def test_the_flag_is_off_by_default_prints_state_when_on_and_nothing_when_nothing_is_relevant(self):
        with tempfile.TemporaryDirectory() as d:
            db = os.path.join(d, "s.db")
            st = SqliteStore(db)
            st.add_state("the converter", "caps replicas at 75", evidence=[ev()], writer="rule:t", trust="verified", learned_how="read")
            st.close()
            off = self.run_inject("why does the converter cap replicas?", db, d, None)
            self.assertEqual((0, ""), (off.returncode, off.stdout))
            on = self.run_inject("why does the converter cap replicas?", db, d, "1")
            out = json.loads(on.stdout)["hookSpecificOutput"]
            self.assertEqual("UserPromptSubmit", out["hookEventName"])
            self.assertTrue(out["additionalContext"].startswith("[statekeeper] "))
            self.assertIn("caps replicas at 75", out["additionalContext"])
            again = self.run_inject("why does the converter cap replicas?", db, d, "1")      # shown in the last 3 turns: stays quiet
            self.assertEqual("", again.stdout)
            quiet = self.run_inject("lunch on friday?", db, d, "1")
            self.assertEqual((0, ""), (quiet.returncode, quiet.stdout))

    def test_session_start_runs_the_configured_refresh_then_prints_the_session_block(self):
        with tempfile.TemporaryDirectory() as d:
            db = os.path.join(d, "s.db")
            marker = os.path.join(d, "refreshed.txt")
            refresher = os.path.join(d, "refresh.py")
            Path(refresher).write_text("\n".join([
                "import sys",
                f"sys.path.insert(0, {str(ROOT / 'src')!r})",
                "from statekeeper import SqliteStore, Evidence",
                f"s = SqliteStore({db!r})",
                "s.add_state('working note', 'doing the flip', evidence=[Evidence('tool', 'note')], writer='rule:t', trust='verified', learned_how='read', state_key='note')",
                f"open({marker!r}, 'w').write('ok')",
            ]) + "\n", encoding="utf-8")
            cfg = os.path.join(d, "config.json")
            Path(cfg).write_text(json.dumps({"session_refresh_command": [sys.executable, refresher]}), encoding="utf-8")
            st = SqliteStore(db)
            st.add_thing("Claude", "agent")
            st.add_state("Claude", "works carefully", evidence=[ev()], writer="rule:t", trust="verified", learned_how="read", volatility="rule")
            st.close()
            env = dict(os.environ, STATEKEEPER_HOME=d, STATEKEEPER_DB=db, PYTHONUTF8="1", STATEKEEPER_INJECT="1", STATEKEEPER_CONFIG=cfg)
            r = subprocess.run([sys.executable, str(ROOT / "adapters" / "claude_code" / "inject_hook.py")],
                               input=json.dumps({"hook_event_name": "SessionStart", "session_id": "s1", "source": "compact"}), capture_output=True, text=True, env=env)
            self.assertTrue(os.path.exists(marker))                               # the refresh ran first
            out = json.loads(r.stdout)["hookSpecificOutput"]
            self.assertEqual("SessionStart", out["hookEventName"])
            self.assertIn("works carefully", out["additionalContext"])
            self.assertIn("doing the flip", out["additionalContext"])             # the refreshed working note is in the block
            env.pop("STATEKEEPER_INJECT")
            off = subprocess.run([sys.executable, str(ROOT / "adapters" / "claude_code" / "inject_hook.py")],
                                 input=json.dumps({"hook_event_name": "SessionStart", "session_id": "s1"}), capture_output=True, text=True, env=env)
            self.assertEqual((0, ""), (off.returncode, off.stdout))

    def test_the_session_block_marks_a_tracked_pr_not_re_read_for_hours_as_stale(self):
        from statekeeper.session import session_block
        s = SqliteStore(":memory:")
        s.add_state("ExampleApp PR 9", "open", evidence=[ev()], writer="rule:github-pr", trust="verified", learned_how="read", state_key="pr.state", volatility="live")
        self.assertNotIn("STALE", session_block(s))
        s.db.execute("UPDATE states SET confirmed_utc = '2026-01-01 00:00:00', recorded_utc = '2026-01-01 00:00:00'")
        self.assertIn("ExampleApp PR 9: open", session_block(s))
        self.assertIn("STALE, last verified", session_block(s))

    def test_a_prompt_with_non_ascii_text_is_read_as_utf8_whatever_the_console_code_page(self):
        with tempfile.TemporaryDirectory() as d:
            db = os.path.join(d, "s.db")
            st = SqliteStore(db)
            st.add_state("the converter", "caps replicas at 75", evidence=[ev()], writer="rule:t", trust="verified", learned_how="read")
            st.close()
            env = dict(os.environ, STATEKEEPER_HOME=d, STATEKEEPER_DB=db, STATEKEEPER_INJECT="1", STATEKEEPER_NO_OBSERVE_IN_TESTS="1")
            env.pop("PYTHONUTF8", None)
            env.pop("STATEKEEPER_EMBED_URL", None)
            body = json.dumps({"hook_event_name": "UserPromptSubmit", "session_id": "u1", "prompt": "why doesn’t the converter cap replicas? 🤔"},
                              ensure_ascii=False).encode("utf-8")
            r = subprocess.run([sys.executable, str(ROOT / "adapters" / "claude_code" / "inject_hook.py")], input=body, capture_output=True, env=env)
            self.assertIn("caps replicas at 75", json.loads(r.stdout)["hookSpecificOutput"]["additionalContext"])
            self.assertNotIn("error", Path(d, "shadow.log.jsonl").read_text(encoding="utf-8"))

    def test_automated_turns_get_nothing_and_a_thing_shown_lately_is_not_shown_again_until_it_changes(self):
        with tempfile.TemporaryDirectory() as d:
            db = os.path.join(d, "s.db")
            st = SqliteStore(db)
            for i in range(4):
                st.add_state("the converter", f"converter fact {i} about replicas", evidence=[ev(f"m{i}")], writer="rule:t", trust="verified", learned_how="read")
            st.close()
            note = "<task-notification>\n<summary>Monitor event: the converter replicas</summary>\n</task-notification>"
            self.assertEqual("", self.run_inject(note, db, d, "1").stdout)
            first = json.loads(self.run_inject("the converter replicas?", db, d, "1").stdout)["hookSpecificOutput"]["additionalContext"]
            self.assertIn("converter fact", first)
            self.assertEqual("", self.run_inject("the converter replicas, again?", db, d, "1").stdout)       # not its other states either
            st = SqliteStore(db)
            time.sleep(1.1)
            st.add_state("the converter", "converter now caps replicas at 70", evidence=[ev("m9")], writer="rule:t", trust="verified", learned_how="read")
            st.close()
            third = json.loads(self.run_inject("the converter replicas now?", db, d, "1").stdout)["hookSpecificOutput"]["additionalContext"]
            self.assertIn("caps replicas at 70", third)
            self.assertNotIn("converter fact", third)
            st = SqliteStore(db)
            prompts = [t for _, _, _, t in st.turn_texts("prompt")]
            st.close()
            self.assertIn(note, prompts)                                                                  # still recorded

    def test_a_broken_event_never_fails_the_turn_or_prints(self):
        with tempfile.TemporaryDirectory() as d:
            env = dict(os.environ, STATEKEEPER_HOME=d, STATEKEEPER_INJECT="1", PYTHONUTF8="1")
            r = subprocess.run([sys.executable, str(ROOT / "adapters" / "claude_code" / "inject_hook.py")], input="nope", capture_output=True, text=True, env=env)
            self.assertEqual((0, ""), (r.returncode, r.stdout))

    def test_the_shadow_adapter_does_not_also_surface_when_the_live_hook_is_on(self):
        with tempfile.TemporaryDirectory() as d:
            shadow_hook.HOME = Path(d)
            s = seeded()
            os.environ["STATEKEEPER_INJECT"] = "1"
            try:
                rec = shadow_hook.handle({"hook_event_name": "UserPromptSubmit", "session_id": "s1", "prompt": "why does the converter cap replicas?"}, s, None, Path(d))
            finally:
                os.environ.pop("STATEKEEPER_INJECT", None)
            self.assertEqual(({}, []), (rec, s.outcomes()))


class Observing(unittest.TestCase):
    def test_the_writers_start_at_most_every_twenty_minutes_and_can_be_switched_off(self):
        with tempfile.TemporaryDirectory() as d:
            started = []
            import subprocess
            real = subprocess.Popen
            subprocess.Popen = lambda *a, **k: started.append(a[0]) or type("P", (), {})()
            try:
                st = Path(d)
                self.assertTrue(shadow_hook.maybe_observe(st, now=1000.0))
                self.assertFalse(shadow_hook.maybe_observe(st, now=1000.0 + 60))
                import os as _os
                _os.utime(st / "observe.stamp", (1000.0, 1000.0))
                self.assertTrue(shadow_hook.maybe_observe(st, now=1000.0 + 21 * 60))
                _os.environ["STATEKEEPER_OBSERVE"] = "0"
                _os.utime(st / "observe.stamp", (1000.0, 1000.0))
                self.assertFalse(shadow_hook.maybe_observe(st, now=1000.0 + 3600))
            finally:
                subprocess.Popen = real
                os.environ.pop("STATEKEEPER_OBSERVE", None)
            self.assertEqual(2, len(started))


class Process(unittest.TestCase):
    def run_hook(self, payload, **env):
        with tempfile.TemporaryDirectory() as d:
            e = dict(os.environ, STATEKEEPER_HOME=d, PYTHONUTF8="1", **env)
            e.pop("STATEKEEPER_EMBED_URL", None)
            r = subprocess.run([sys.executable, str(ROOT / "adapters" / "claude_code" / "shadow_hook.py")], input=payload, capture_output=True, text=True, env=e)
            log = Path(d, "shadow.log.jsonl")
            return r, log.read_text(encoding="utf-8") if log.exists() else ""

    def test_the_process_prints_nothing_exits_zero_on_garbage_and_logs_the_error(self):
        r, log = self.run_hook("not json")
        self.assertEqual((0, ""), (r.returncode, r.stdout))
        self.assertIn("adapter-error", log)

    def test_the_off_switch_does_nothing_at_all(self):
        r, log = self.run_hook(json.dumps({"hook_event_name": "SessionStart", "session_id": "s", "source": "startup"}), STATEKEEPER_SHADOW="0")
        self.assertEqual((0, "", ""), (r.returncode, r.stdout, log))

    def test_a_session_start_is_logged_and_nothing_is_printed(self):
        r, log = self.run_hook(json.dumps({"hook_event_name": "SessionStart", "session_id": "s", "source": "compact"}))
        self.assertEqual((0, ""), (r.returncode, r.stdout))
        self.assertIn('"source": "compact"', log)


if __name__ == "__main__":
    unittest.main()
