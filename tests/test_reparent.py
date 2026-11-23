"""Re-parenting: things get an explicit parent from their names, ticket keys, PR titles and flag keys, so retrieval's hierarchy (a named leaf brings its
ancestors' states and keeps its siblings out) has a tree to work with. Dry run by default, never overrides a parent it did not derive, idempotent."""
import unittest

from support import ev, fresh_store
from statekeeper.reparent import apply, plan


def add(s, thing, text, ref="r", **kw):
    return s.add_state(thing, text, evidence=[ev(ref, text)], writer="w", trust="derived", learned_how="inferred", **kw)


class Plan(unittest.TestCase):
    def setUp(self):
        self.s = fresh_store()

    def moves(self):
        return {m["child"]: (m["parent"], m["rule"]) for m in plan(self.s)}

    def test_identifier_prefix_goes_under_the_identifier(self):
        for n in ("CRM-2971", "CRM-2971 Prince parity", "#142", "#142 backfill", "PR 3376", "PR 3376 120-day rule"):
            self.s.add_thing(n)
        got = self.moves()
        self.assertEqual(("CRM-2971", "identifier"), got["CRM-2971 Prince parity"])
        self.assertEqual(("#142", "identifier"), got["#142 backfill"])
        self.assertEqual(("PR 3376", "identifier"), got["PR 3376 120-day rule"])
        self.assertNotIn("CRM-2971", got)

    def test_a_pr_goes_under_the_one_ticket_its_title_names(self):
        self.s.add_thing("CRM-3340")
        self.s.add_thing("VirtuousV2")
        self.s.add_thing("VirtuousV2 PR 3428", parent="VirtuousV2")
        add(self.s, "VirtuousV2 PR 3428", "CRM-3340 ExportManager one-shot claim · draft · CI green", state_key="pr.title", is_state=True)
        self.assertEqual(("CRM-3340", "pr-ticket"), self.moves()["VirtuousV2 PR 3428"])

    def test_other_spellings_of_a_pr_follow_its_ticket(self):
        self.s.add_thing("CRM-3052")
        add(self.s, "PR #3376", "PR #3376 (CRM-3052) has its conflict resolved.")
        self.s.add_thing("PR 3376")
        self.s.add_thing("PR 3376 120-day rule")
        got = self.moves()
        self.assertEqual(("CRM-3052", "pr-ticket"), got["PR 3376"])
        self.assertEqual(("PR 3376", "identifier"), got["PR 3376 120-day rule"])

    def test_a_pr_naming_two_tickets_or_an_unknown_ticket_stays(self):
        self.s.add_thing("CRM-1")
        self.s.add_thing("CRM-2")
        add(self.s, "PR 3001", "Fixes CRM-1 and CRM-2 together")
        add(self.s, "PR 3002", "Fixes CRM-9999")
        got = self.moves()
        self.assertNotIn("PR 3001", got)
        self.assertNotIn("PR 3002", got)

    def test_a_flag_key_goes_under_launchdarkly_flag(self):
        self.s.add_thing("LaunchDarkly")
        self.s.add_thing("LaunchDarkly flag")
        add(self.s, "receipting-repair-requires-rendered", "The flag is off in production.")
        self.s.add_thing("pdf-converter-service-enabled flag")
        add(self.s, "qa-login-tool-cache", "The tool caches sessions.")      # kebab, but nothing says it is a flag
        got = self.moves()
        self.assertEqual(("LaunchDarkly flag", "flag"), got["receipting-repair-requires-rendered"])
        self.assertEqual(("LaunchDarkly flag", "flag"), got["pdf-converter-service-enabled flag"])
        self.assertNotIn("qa-login-tool-cache", got)
        self.assertEqual(("LaunchDarkly", "name"), got["LaunchDarkly flag"])

    def test_name_containment_takes_the_longest_leading_name(self):
        for n in ("Receipt", "Receipt Date", "Receipt Date backfill", "PDF converter", "egress rules", "PDF converter egress rules", "Agent", "QA Agent",
                  "DevOps", "Azure DevOps"):
            self.s.add_thing(n)
        got = self.moves()
        self.assertEqual(("Receipt Date", "name"), got["Receipt Date backfill"])
        self.assertEqual(("Receipt", "name"), got["Receipt Date"])
        self.assertEqual(("PDF converter", "name"), got["PDF converter egress rules"])
        self.assertNotIn("QA Agent", got)                                   # a kind of agent, not a part of it
        self.assertNotIn("Azure DevOps", got)

    def test_a_common_lower_case_word_is_no_parent(self):
        self.s.add_thing("data")
        for i in range(3):
            add(self.s, "tooling", f"tooling rule {i}", ref=f"r{i}")
        self.s.add_thing("data explorer")
        self.s.add_thing("tooling notes")
        got = self.moves()
        self.assertNotIn("data explorer", got)
        self.assertEqual(("tooling", "name"), got["tooling notes"])

    def test_lists_of_identifiers_and_files_are_not_filed_by_identifier(self):
        for n in ("CRM-2970", "CRM-2970 and CRM-2971", "CRM-1898, CRM-1899", "crm-2970-notes.md", "CRM-2970 notes.md"):
            self.s.add_thing(n)
        got = self.moves()
        for n in ("CRM-2970 and CRM-2971", "CRM-1898, CRM-1899", "crm-2970-notes.md", "CRM-2970 notes.md"):
            self.assertNotIn(n, got)

    def test_a_kebab_word_is_a_flag_only_when_said_near_flag(self):
        self.s.add_thing("LaunchDarkly flag")
        add(self.s, "orchestrator", "Arm the watcher with Monitor: run board-watch-orch with pwsh -NoProfile, then later pass the --flag value only once.")
        add(self.s, "rollout", "Turn on the flag lob-webhook-require-signature for one org first.")
        self.s.add_thing("board-watch-orch")
        self.s.add_thing("lob-webhook-require-signature")
        got = self.moves()
        self.assertNotIn("board-watch-orch", got)
        self.assertEqual(("LaunchDarkly flag", "flag"), got["lob-webhook-require-signature"])

    def test_people_paths_and_existing_parents_are_left_alone(self):
        self.s.add_thing("John Ackerman", "person")
        self.s.add_thing("John Ackerman prefs")
        self.s.add_thing("tooling")
        self.s.add_thing("C:/repos/tooling notes")
        self.s.add_thing("Receipt")
        self.s.add_thing("receipting")
        self.s.add_thing("Receipt checks", parent="receipting")
        got = self.moves()
        self.assertNotIn("John Ackerman prefs", got)
        self.assertNotIn("C:/repos/tooling notes", got)
        self.assertNotIn("Receipt checks", got)

    def test_no_cycles(self):
        self.s.add_thing("alpha beta")
        self.s.add_thing("alpha beta gamma")
        self.s.add_thing("alpha", parent="alpha beta gamma")
        got = self.moves()
        self.assertNotIn("alpha beta", got)                                # alpha beta -> alpha -> alpha beta gamma -> alpha beta would loop


class SameFlag(unittest.TestCase):
    def test_a_flag_said_two_ways_becomes_one_thing(self):
        from statekeeper.reparent import merge_plan
        s = fresh_store()
        s.add_thing("LaunchDarkly flag")
        a = add(s, "pdf-converter-service-enabled", "The rollout buckets by organization key.", ref="a")
        b = add(s, "pdf-converter-service-enabled flag", "In production the flag is on with fallthrough false.", ref="b")
        self.assertEqual([("pdf-converter-service-enabled flag", "pdf-converter-service-enabled")], merge_plan(s))
        apply(s, plan(s), actor="t", merges=merge_plan(s))
        t = s.find("pdf-converter-service-enabled flag")
        self.assertEqual("pdf-converter-service-enabled", t.name)          # the old name is an alias now
        self.assertEqual({a.id, b.id}, {x.id for x in s.states_of(t.id)})
        self.assertEqual([], merge_plan(s))


class Apply(unittest.TestCase):
    def test_apply_sets_parents_logs_events_and_is_idempotent(self):
        s = fresh_store()
        s.add_thing("CRM-2971")
        s.add_thing("CRM-2971 Prince parity")
        moves = plan(s)
        self.assertEqual(1, len(apply(s, moves, actor="tool:reparent")))
        self.assertEqual(s.find("CRM-2971").id, s.find("CRM-2971 Prince parity").parent_id)
        ev_ = [e for e in s.events() if e.event_type == "thing-reparented"]
        self.assertEqual(1, len(ev_))
        self.assertEqual("tool:reparent", ev_[0].actor)
        self.assertEqual([], plan(s))


if __name__ == "__main__":
    unittest.main()
