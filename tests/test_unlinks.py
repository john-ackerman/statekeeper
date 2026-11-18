import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from support import ThemeEmbedder, ev, fresh_store
from statekeeper import SqliteStore, StoreError, surface
from statekeeper import usefulness as u
from statekeeper.retrieve import explain

ROOT = Path(__file__).resolve().parents[1]
TODAY = datetime(2026, 11, 18, tzinfo=timezone.utc)


def add(s, thing, text, **kw):
    return s.add_state(thing, text, evidence=[ev(text[:8])], writer="w", trust="verified", learned_how="read", **kw)


class NegativeLinks(unittest.TestCase):
    def test_a_state_unlinked_from_a_thing_stays_out_when_that_thing_is_named_but_still_surfaces_for_its_own(self):
        s = fresh_store()
        st = add(s, "the cli", "its skill doc lives at skills/cli.md", object="TheRepo")
        self.assertIn("skills/cli.md", surface(s, "the cli skill doc for TheRepo", None, today=TODAY).block)
        uid = s.unlink(st.id, thing="TheRepo", reason="the skill path is not what TheRepo questions are about", actor="owner")
        self.assertNotIn("skills/cli.md", surface(s, "the cli skill doc for TheRepo", None, today=TODAY).block)
        self.assertIn("skills/cli.md", surface(s, "the cli skill doc", None, today=TODAY).block)         # still shown for what it IS about
        self.assertTrue(s.get_state(st.id).standing)                                                   # nothing deleted
        s.relink(uid, "it was related after all", actor="owner")
        self.assertIn("skills/cli.md", surface(s, "the cli skill doc for TheRepo", None, today=TODAY).block)
        kinds = [e.event_type for e in s.events() if e.state_id == st.id]
        self.assertIn("unlinked", kinds)
        self.assertIn("relinked", kinds)
        with self.assertRaises(StoreError):
            s.relink(uid, "again", actor="owner")

    def test_a_concept_unlink_and_a_meaning_unlink(self):
        s, emb = fresh_store(), ThemeEmbedder()
        st = add(s, "the hub", "caps cores at the quota for pdf render scaling")
        prompt = "our cores got upped, how does the quota change pdf scaling"
        self.assertIn("caps cores", surface(s, prompt, emb, today=TODAY).block)
        uid = s.unlink(st.id, concept="pdf render quota", reason="not that kind of quota", actor="owner")
        self.assertNotIn("caps cores", surface(s, prompt, emb, today=TODAY).block)
        s.relink(uid, "ok", actor="owner")
        s.unlink(st.id, match="meaning", reason="only when the hub is named", actor="owner")
        self.assertNotIn("caps cores", surface(s, prompt, emb, today=TODAY).block)
        self.assertIn("caps cores", surface(s, "the hub", emb, today=TODAY).block)

    def test_two_things_unlinked_never_connect_each_others_states(self):
        s = fresh_store()
        add(s, "the cli", "its skill doc lives at skills/cli.md", object="TheRepo")
        s.unlink(None, thing="the cli", other_thing="TheRepo", reason="unrelated", actor="owner")
        self.assertNotIn("skills/cli.md", surface(s, "TheRepo and the cli", None, today=TODAY).block)
        with self.assertRaises(StoreError):
            s.unlink(None, thing="the cli", reason="needs two things", actor="owner")

    def test_typed_in_chat_it_becomes_a_negative_link_and_not_useful_stays_feedback(self):
        s = fresh_store()
        st = add(s, "the cli", "its skill doc lives at skills/cli.md")
        s.add_thing("TheRepo")
        self.assertEqual([(st.id, "TheRepo")], u.parse_unlinks(f"sk #{st.id} not TheRepo."))
        self.assertEqual([], u.parse_unlinks(f"sk #{st.id} not useful"))
        made = u.apply_unlinks(s, u.parse_unlinks(f"sk #{st.id} not TheRepo, statekeeper: #{st.id} is not about release planning"), actor="person:chat", reason="chat")
        got = s.unlinks(state_id=st.id)
        self.assertEqual(2, len(made))
        self.assertEqual((s.find("TheRepo").id, None), (got[0].thing_id, got[0].concept))
        self.assertEqual("release planning", got[1].concept)


class Explaining(unittest.TestCase):
    def test_each_shown_state_logs_how_it_was_connected_and_only_meaning_matches_carry_a_tag(self):
        s, emb = fresh_store(), ThemeEmbedder()
        named = add(s, "the datepicker", "is flatpickr now")
        meant = add(s, "the hub", "caps cores at the quota for pdf render scaling")
        out = surface(s, "the datepicker", emb, today=TODAY, log_turn="t1")
        self.assertNotIn("~", out.block)                                                  # a name match needs no tag
        out2 = surface(s, "how does the quota change pdf scaling", emb, today=TODAY, log_turn="t2")
        self.assertRegex(out2.block, r"\[verified today · msg [^\]]+ · ~0\.\d\d\]")
        r1 = json.loads(s.outcomes(kind="surfaced", turn_ref="t1")[0].detail)["reason"]
        r2 = json.loads(s.outcomes(kind="surfaced", turn_ref="t2")[0].detail)["reason"]
        self.assertEqual(("named", "the datepicker"), (r1["via"], r1["thing"]))
        self.assertEqual("meaning", r2["via"])
        self.assertIn("close in meaning", explain(r2))
        self.assertIn("the name 'the datepicker'", explain(r1))
        self.assertEqual(named.id, s.outcomes(kind="surfaced", turn_ref="t1")[0].state_id)
        self.assertEqual(meant.id, s.outcomes(kind="surfaced", turn_ref="t2")[0].state_id)

    def test_the_explain_tool_answers_why_for_a_session_and_a_state(self):
        with tempfile.TemporaryDirectory() as d:
            db = os.path.join(d, "s.db")
            s = SqliteStore(db)
            st = add(s, "the datepicker", "is flatpickr now")
            s.record_turn_text("sess1:100", "prompt", "what is the datepicker now?")
            surface(s, "what is the datepicker now?", None, today=TODAY, log_turn="sess1:100")
            s.close()
            r = subprocess.run([sys.executable, str(ROOT / "tools" / "explain.py"), "sess1", f"#{st.id}", "--db", db], capture_output=True, text=True,
                               env=dict(os.environ, PYTHONUTF8="1"))
            self.assertIn("shown because: the input contains the name 'the datepicker'", r.stdout)
            self.assertIn("what is the datepicker now?", r.stdout)


class Audit(unittest.TestCase):
    def test_states_shown_repeatedly_through_weak_connections_and_never_used_are_listed(self):
        s = fresh_store()
        weak, strong = add(s, "a", "weakly connected"), add(s, "b", "named often")
        for i in range(4):
            s.log_outcome(weak.id, "surfaced", source="surface", turn_ref=f"t{i}", detail=json.dumps({"reason": {"via": "named", "thing": "TheRepo", "generic": True}}))
            s.log_outcome(strong.id, "surfaced", source="surface", turn_ref=f"t{i}", detail=json.dumps({"reason": {"via": "named", "thing": "b", "specific": True}}))
        rows = u.weak_connections(s, days=1)
        self.assertEqual([(weak.id, 4, "TheRepo")], [(r["state"], r["weak"], r["via"]) for r in rows])


if __name__ == "__main__":
    unittest.main()
