import unittest
from datetime import datetime, timezone

from support import ThemeEmbedder, fresh_store
from statekeeper import Evidence, surface
from statekeeper.canonical import (Index, canonical, collapse, contradicts, overtaken_by_retirement, proposals, retired_things,
                                   substitutes)


def src(ref):
    return Evidence("prototype", ref, "fragment text")


def fill(store, n=60):
    """Unrelated states, so word rarity means what it means in a real store (in a store of two states every word is in every state)."""
    for i in range(n):
        store.add_state(f"filler {i}", f"Filler{i} gizmo{i} sprocket{i} widgetry{i} contraption{i}.", evidence=[src(f"filler:{i}")], writer="w",
                        trust="verified", learned_how="read")


class Base(unittest.TestCase):
    def setUp(self):
        self.s = fresh_store()
        fill(self.s)
        self.vec = {}

    def add(self, thing, text, ref, *extra, vector=(1.0, 0.0, 0.0), **kw):
        st = self.s.add_state(thing, text, evidence=[src(ref), *extra], writer="w", trust=kw.pop("trust", "extracted"), learned_how="read",
                              status=kw.pop("status", "active"), **kw)
        self.vec[st.id] = list(vector)
        return st

    def idx(self):
        pool = [x for x in self.s.searchable_states() if not x.state_key]
        return Index(pool, self.s.evidence_of([x.id for x in pool]), self.vec)


class Pairs(Base):
    """restates(): the same fact under two ids, decided from evidence (a shared source, word overlap, meaning) and never across a contradiction,
    a different number or a different value."""

    def test_two_wordings_of_one_fragment_are_one_fact_and_the_one_closer_to_the_source_is_canonical(self):
        a = self.add("John Ackerman", "John set a standing rule that every data script, remediation or backfill, is validated locally before he runs it.",
                     "persistence:700")
        b = self.add("data script validation", "Every data script, including remediation and backfill scripts, is validated locally before John runs it.",
                     "persistence:700", Evidence("state", "#1742"))       # b was rewritten from another state (the decompose job)
        i = self.idx()
        self.assertTrue(i.restates(a.id, b.id))
        self.assertEqual({a.id, b.id}, i.cluster(b.id))
        self.assertEqual(a.id, canonical({a.id, b.id}, i))

    def test_a_legacy_reference_and_a_persistence_reference_to_the_same_fragment_are_one_source(self):
        a = self.add("SendGrid", "PR 3342 is reworked to per-org SendGrid signing keys with a separate backfill script.", "legacy:2702")
        b = self.add("SendGrid webhook", "PR 3342 is reworked to per-org SendGrid signing keys, with the backfill as a separate script.", "persistence:2702")
        self.assertTrue(self.idx().restates(a.id, b.id))

    def test_different_sources_need_more_meaning_than_one_source_does(self):
        a = self.add("draft CI", "Queue the draft unified build with az pipelines run on definition 119 from a worktree.", "persistence:313")
        b = self.add("draft CI run", "Queue the draft unified build with az pipelines run on definition 119 from a worktree.", "persistence:767",
                     vector=(0.8, 0.6, 0.0))
        c = self.add("draft CI run", "Queue the draft unified build with az pipelines run on definition 119 from a worktree.", "persistence:313",
                     vector=(0.8, 0.6, 0.0))
        i = self.idx()
        self.assertFalse(i.restates(a.id, b.id))           # cosine 0.80 < 0.85 with no shared source
        self.assertTrue(i.restates(a.id, c.id))            # the same 0.80 is enough for two readings of one source

    def test_a_different_number_is_an_update_not_a_restatement(self):
        a = self.add("PDF rollout", "High-risk orgs are at 10% of the PDF rollout for every high-risk customer org.", "persistence:2579", as_of="2026-11-17")
        b = self.add("PDF rollout", "High-risk orgs are at 50% of the PDF rollout for every high-risk customer org.", "persistence:2579", as_of="2026-11-18")
        i = self.idx()
        self.assertFalse(i.restates(a.id, b.id))
        self.assertTrue(i.updates(a.id, b.id))

    def test_a_negation_of_what_the_other_says_is_a_contradiction_but_a_negated_side_clause_is_not(self):
        self.assertTrue(contradicts("gh-safe request-copilot works, while gh pr edit --add-reviewer does not.",
                                    "A Copilot review can be requested with gh pr edit --add-reviewer when the guard blocks graphql."))
        self.assertFalse(contradicts("Every data script is validated locally, using local Docker only and never dev, QA or prod.",
                                     "Every data script, including backfill scripts, is validated locally before John runs it."))

    def test_the_same_sentence_about_another_value_is_a_sibling(self):
        self.assertTrue(substitutes("The QA Admin app URL is https://admin-qa.crm.example.io.", "The prod Admin app URL is https://admin.crm.example.io.",
                                    "Admin app", "Admin app"))
        self.assertTrue(substitutes("ReceiptProcessorBase subclasses hard-construct the adapters in their constructors.",
                                    "ExportManager hard-constructs the adapters in its constructor."))
        self.assertTrue(substitutes("Item 13 of the feedback is on the board as the card persist-fb-13.",
                                    "Claude adds each feedback item to the board as an ask with id persist-fb-<n>."))
        # a placeholder, a stop word inside an identifier and the thing's own name are not values
        self.assertFalse(substitutes("A flag's production state is read from on, offVariation and rules of its environments.production entry.",
                                     "The production state of a LaunchDarkly flag is read from environments.<env>.on, offVariation and rules.",
                                     "LaunchDarkly", "LaunchDarkly"))
        self.assertFalse(substitutes("Independent pieces still prefer parallel PRs off master (fan-out) over chains.",
                                     "Independent pieces of work should prefer parallel PRs off master (fan-out) over chained stacks."))

    def test_a_hyphenated_word_is_a_word_not_a_value(self):
        self.assertFalse(substitutes("Claude front-loads questions for John into one numbered list.", "Questions for John go front-loaded into one numbered list."))


class Retrieval(Base):
    def setUp(self):
        super().setUp()
        self.emb = ThemeEmbedder()
        self.today = datetime(2026, 11, 20, tzinfo=timezone.utc)

    def pointing(self):
        orig = self.add("John Ackerman", "John set the rule on 2026-11-12: parent tickets point the investigation effort, sub-issues point their own "
                        "effort, and totals need not match.", "persistence:998", as_of="2026-11-12", volatility="rule")
        copy = self.add("ticket pointing", "Parent tickets point the investigation effort, sub-issues point their own effort, and totals need not match.",
                        "persistence:998", Evidence("state", "#1830"), as_of="2026-11-12", volatility="rule")
        from statekeeper.embed import ensure_vectors
        ensure_vectors(self.s, self.emb)
        return orig, copy

    def shown(self, out):
        return [x.id for it in out.items for x in it.states if not x.state_key]

    def test_two_copies_of_one_fact_take_one_slot_and_the_canonical_is_shown(self):
        orig, copy = self.pointing()
        out = surface(self.s, "do parent tickets have to point the sum of the sub-issues effort", self.emb, floor=0.3, today=self.today)
        self.assertEqual([orig.id], [x for x in self.shown(out) if x in (orig.id, copy.id)])

    def test_switched_off_both_copies_compete_as_before(self):
        import statekeeper.canonical as C
        orig, copy = self.pointing()
        C.ON = False
        try:
            out = surface(self.s, "do parent tickets have to point the sum of the sub-issues effort", self.emb, floor=0.3, today=self.today)
        finally:
            C.ON = True
        self.assertNotIn(orig.id, self.shown(out))           # the old newer-id-wins pass keeps the rewrite, not the original
        self.assertIn(copy.id, self.shown(out))

    def test_a_canonical_already_in_the_session_core_drops_the_whole_cluster(self):
        core = self.add("Claude", "Claude front-loads questions for John into one numbered list with labelled options and a lean.", "persistence:13",
                        kind="rule", volatility="rule")
        copy = self.add("John's communication defaults", "Questions for John go front-loaded into one numbered list with labelled options and a lean.",
                        "persistence:13", Evidence("state", "#1655"))
        sub, drop, _rec = collapse([copy], self.idx(), eligible=lambda c: "core" if c.id == core.id else "ok")
        self.assertEqual(({}, {copy.id}), (sub, drop))
        sub, drop, _rec = collapse([copy], self.idx(), eligible=lambda c: "ok")
        self.assertEqual(({copy.id: core.id}, set()), (sub, drop))

    def test_a_thing_declared_frozen_makes_what_was_recorded_about_it_before_history(self):
        old = self.add("Persistence store", "To roll back the Persistence migration, restore claude.db from the backup folder.", "persistence:265",
                       as_of="2026-11-09", kind="rule", volatility="rule")
        decl = self.add("Persistence store", "The Persistence store is frozen and kept only as history.", "file:orchestrator", as_of="2026-11-18")
        side = self.add("receipting", "The standalone-service plan is superseded by the in-place strategy.", "persistence:1", as_of="2026-11-18")
        other = self.add("receipting", "Receipts are stamped at render.", "persistence:2", as_of="2026-11-10")
        retired = retired_things(self.s.searchable_states())
        self.assertEqual({decl.thing_id: decl.id}, {k: v.id for k, v in retired.items()})     # "plan is superseded" does not retire "receipting"
        self.assertEqual({old.id: decl.id}, {k: v.id for k, v in overtaken_by_retirement([old, other, side], retired, "how do I roll back the move").items()})
        self.assertEqual({}, overtaken_by_retirement([old], retired, "what was the old rollback, for history"))
        out = surface(self.s, "how do I roll back the Persistence store migration", None, today=self.today)
        self.assertNotIn(f"#{old.id} ", out.block)
        self.assertIn(f"#{decl.id} ", out.block)


class Proposals(Base):
    def test_proposals_list_each_cluster_once_with_evidence_and_write_nothing(self):
        a = self.add("John Ackerman", "John set a standing rule that every data script, remediation or backfill, is validated locally before he runs it.",
                     "persistence:700")
        b = self.add("data script validation", "Every data script, including remediation and backfill scripts, is validated locally before John runs it.",
                     "persistence:700", Evidence("state", "#1742"), trust="derived")
        self.add("Linear", "Search Linear before creating any ticket.", "persistence:9", vector=(0.0, 1.0, 0.0))
        for sid, v in self.vec.items():
            self.s.put_vector("state", sid, "test", v, "h")
        for st in self.s.searchable_states():
            if st.id not in self.vec:
                self.s.put_vector("state", st.id, "test", [0.0, 0.0, 1.0 + st.id], "h")
        before = self.s.db.execute("SELECT COUNT(*) FROM events").fetchone()[0]
        props = [p for p in proposals(self.s, model="test") if p["relation"] == "restates"]
        self.assertEqual(before, self.s.db.execute("SELECT COUNT(*) FROM events").fetchone()[0])
        self.assertEqual(1, len(props))
        p = props[0]
        self.assertEqual(("supersede", "restates", a.id, [b.id], "high"), (p["op"], p["relation"], p["keep"], p["retire"], p["confidence"]))
        self.assertEqual(["persistence:700"], p["evidence"][str(b.id)]["shared_sources"])
        self.assertIn("closer to the source", p["why_keep"])


if __name__ == "__main__":
    unittest.main()
