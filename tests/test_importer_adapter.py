import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

from support import ThemeEmbedder, ev, fresh_store
from statekeeper import surface
from statekeeper.importer import best_sentence, import_export

ROOT = Path(__file__).resolve().parents[1]

EXPORT = {
    "concepts": [
        {"id": 1, "name": "the converter", "kind": "thing", "confirmed": True, "aliases": ["pdf service"]},
        {"id": 2, "name": "Claude", "kind": "self", "confirmed": True, "aliases": []},
        {"id": 3, "name": "quota", "kind": "thing", "confirmed": False, "aliases": []},
    ],
    "facts": [
        {"id": 10, "subject": 1, "object": 3, "text": "has a quota of 300 cores per environment", "isState": True, "asOf": "2026-11-11", "recordedAt": "2026-11-11 21:13:20+00:00",
         "status": "active", "extractedBy": "auto:extractor-rule", "sources": [649]},
        {"id": 11, "subject": 1, "object": None, "text": "has a quota of 100 cores", "status": "active", "extractedBy": "by hand", "sources": [601], "supersededBy": 10},
        {"id": 12, "subject": 2, "object": None, "text": "builder is still running", "status": "proposed", "extractedBy": "extractor:abc", "sources": [700]},
        {"id": 13, "subject": 2, "object": None, "text": "never extracted", "status": "rejected", "extractedBy": "extractor:abc", "sources": [700], "resolution": "transient"},
        {"id": 14, "subject": 1, "object": None, "text": "no sources", "status": "proposed", "extractedBy": "extractor:abc", "sources": []},
    ],
    "fragments": {
        "649": {"content": "PDF converter core quota. The quota is 300 cores per environment. Other notes follow.", "sha256": "abc"},
        "601": {"content": "Old note: the quota is 100 cores."},
        "700": {"content": "The builder is still running today. It will report soon."},
    },
}


class Importer(unittest.TestCase):
    def test_accepted_stand_proposals_stay_proposals_rejected_stay_rejected_and_superseded_stays_superseded(self):
        s = fresh_store()
        counts = import_export(s, EXPORT)
        self.assertEqual({"things": 3, "aliases": 1, "states": 4, "superseded": 1, "rejected": 1, "skipped": 1, "synced": 0}, counts)
        by_text = {st.text: st for st in s.searchable_states()}
        self.assertEqual("active", by_text["has a quota of 300 cores per environment"].status)
        self.assertEqual("derived", by_text["has a quota of 300 cores per environment"].trust)
        self.assertEqual("proposed", by_text["builder is still running"].status)
        self.assertNotIn("has a quota of 100 cores", by_text)                       # superseded: history, not standing
        self.assertNotIn("never extracted", by_text)                                # rejected
        self.assertEqual("agent", s.find("Claude").kind)                            # the source's "self" is an ordinary agent thing
        self.assertEqual("the converter", s.find("pdf service").name)
        self.assertEqual("2026-11-11 21:13:20+00:00", by_text["has a quota of 300 cores per environment"].recorded_utc)

    def test_evidence_carries_an_excerpt_a_hash_and_a_handle_to_the_source(self):
        s = fresh_store()
        import_export(s, EXPORT)
        st = next(x for x in s.searchable_states() if x.text.startswith("has a quota of 300"))
        e = s.evidence_of([st.id])[st.id][0]
        self.assertEqual(("legacy-source", "legacy:649"), (e.ref_type, e.ref))
        self.assertEqual("The quota is 300 cores per environment.", e.excerpt)
        self.assertEqual("abc", e.sha256)

    def test_the_imported_proposal_is_found_by_meaning(self):
        s = fresh_store()
        import_export(s, EXPORT)
        out = surface(s, "our cores got upped to 300, what does the quota change", ThemeEmbedder())
        self.assertIn("quota of 300 cores", out.block)

    def test_best_sentence_picks_the_sentence_sharing_the_most_words(self):
        self.assertEqual("Cores are capped.", best_sentence("Hello there. Cores are capped. Bye now.", "the cores capped"))


class Incremental(unittest.TestCase):
    def test_a_second_import_adds_nothing_and_syncs_status_changes_from_the_source(self):
        s = fresh_store()
        import_export(s, EXPORT)
        n = len(s.db.execute("SELECT id FROM states").fetchall())
        again = import_export(s, EXPORT)
        self.assertEqual((0, 0), (again["states"], again["things"]))
        self.assertEqual(n, len(s.db.execute("SELECT id FROM states").fetchall()))
        changed = json.loads(json.dumps(EXPORT))
        changed["facts"][2]["status"] = "active"                                   # the source accepted the 'builder' proposal
        third = import_export(s, changed)
        self.assertEqual(1, third["synced"])
        self.assertEqual("active", next(x for x in s.searchable_states() if x.text == "builder is still running").status)

    def test_states_from_an_import_that_predates_the_map_are_recognised_not_duplicated(self):
        s = fresh_store()
        import_export(s, EXPORT)
        s.db.execute("DELETE FROM import_map")
        n = len(s.db.execute("SELECT id FROM states").fetchall())
        import_export(s, EXPORT)
        self.assertEqual(n, len(s.db.execute("SELECT id FROM states").fetchall()))
        self.assertGreater(len(s.imported("legacy-fact")), 0)

    def test_a_state_merged_away_by_consolidation_is_not_resurrected_by_a_reimport(self):
        s = fresh_store()
        import_export(s, EXPORT)
        st = next(x for x in s.searchable_states() if x.text == "builder is still running")
        s.reject(st.id, "duplicate of #1", actor="rule:dedupe")
        import_export(s, EXPORT)
        self.assertEqual("rejected", s.get_state(st.id).status)


class Adapter(unittest.TestCase):
    def test_the_hook_prints_additional_context_for_a_prompt_that_names_a_thing_and_nothing_otherwise(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            db = os.path.join(d, "s.db")
            from statekeeper import SqliteStore
            st = SqliteStore(db)
            st.add_state("the converter", "caps replicas at 75", evidence=[ev()], writer="w", trust="verified", learned_how="read")
            st.close()
            env = dict(os.environ, STATEKEEPER_DB=db, PYTHONUTF8="1")
            env.pop("STATEKEEPER_EMBED_URL", None)
            hook = str(ROOT / "adapters" / "claude_code" / "recall_hook.py")
            named = subprocess.run([sys.executable, hook], input=json.dumps({"prompt": "why does the converter cap replicas?"}), capture_output=True, text=True, env=env)
            payload = json.loads(named.stdout)["hookSpecificOutput"]
            self.assertEqual("UserPromptSubmit", payload["hookEventName"])
            self.assertIn("caps replicas at 75", payload["additionalContext"])
            quiet = subprocess.run([sys.executable, hook], input=json.dumps({"prompt": "lunch?"}), capture_output=True, text=True, env=env)
            self.assertEqual("", quiet.stdout.strip())
            broken = subprocess.run([sys.executable, hook], input="not json", capture_output=True, text=True, env=env)
            self.assertEqual(0, broken.returncode)                                   # never fails the turn


if __name__ == "__main__":
    unittest.main()


class ImportLegacyTool(unittest.TestCase):
    def run_tool(self, export, *extra):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            src, db = Path(d, "export.json"), Path(d, "out", "s.db")
            src.write_text(json.dumps(export), encoding="utf-8")
            r = subprocess.run([sys.executable, str(ROOT / "tools" / "import_legacy.py"), "--export", str(src), "--into", str(db), *extra],
                               capture_output=True, text=True, env=dict(os.environ, PYTHONUTF8="1"), timeout=60)
            return r, db.exists()

    def test_a_valid_export_is_imported(self):
        r, created = self.run_tool(EXPORT)
        self.assertEqual(0, r.returncode, r.stderr)
        self.assertTrue(created)
        self.assertIn("imported:", r.stdout)

    def test_an_export_missing_a_part_is_refused_and_validate_only_writes_nothing(self):
        bad, created = self.run_tool({"concepts": []})
        self.assertEqual(2, bad.returncode)
        self.assertFalse(created)
        ok, created = self.run_tool(EXPORT, "--validate-only")
        self.assertEqual(0, ok.returncode)
        self.assertFalse(created)
