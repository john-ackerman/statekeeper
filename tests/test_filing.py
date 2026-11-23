"""Writers file under the hierarchy: "Parent > child" names a child under a known parent, a new thing is filed by the re-parent rules the moment it
is made, a new one-word generic thing ("watcher") is refused, and the extractor sees where known things sit."""
import unittest

from support import ev, fresh_store
from statekeeper.claims import ClaimError, submit_claim
from statekeeper.extract import Extractor
from statekeeper.reparent import place
from statekeeper.store import StoreError
from statekeeper.writers.github_pr import observe_pr


def claim(thing, text, **kw):
    return {"thing": thing, "text": text, "writer": "extractor:t", "session_id": "s", "evidence": [{"type": "message", "ref": "1"}], **kw}


class Place(unittest.TestCase):
    def setUp(self):
        self.s = fresh_store()
        self.s.add_thing("LaunchDarkly")
        self.s.add_thing("LaunchDarkly flag", parent="LaunchDarkly")

    def test_parent_path_creates_the_child_under_the_parent(self):
        name = place(self.s, "LaunchDarkly flag > receipting-repair-requires-rendered")
        self.assertEqual("receipting-repair-requires-rendered", name)
        self.assertEqual(self.s.find("LaunchDarkly flag").id, self.s.find(name).parent_id)

    def test_parent_path_files_an_existing_unfiled_child_and_never_moves_a_filed_one(self):
        self.s.add_thing("lob-webhook-require-signature")
        self.s.add_thing("Copilot")
        self.s.add_thing("Copilot review", parent="Copilot")
        place(self.s, "LaunchDarkly flag > lob-webhook-require-signature")
        place(self.s, "LaunchDarkly > Copilot review")
        self.assertEqual(self.s.find("LaunchDarkly flag").id, self.s.find("lob-webhook-require-signature").parent_id)
        self.assertEqual(self.s.find("Copilot").id, self.s.find("Copilot review").parent_id)

    def test_an_unknown_parent_is_refused(self):
        with self.assertRaises(StoreError):
            place(self.s, "Nowhere > something specific")

    def test_a_new_thing_is_filed_by_the_rules(self):
        self.s.add_thing("CRM-2971")
        self.assertEqual(self.s.find("CRM-2971").id, self.s.find(place(self.s, "CRM-2971 sweeper design")).parent_id)
        self.assertEqual(self.s.find("LaunchDarkly").id, self.s.find(place(self.s, "LaunchDarkly rollout order")).parent_id)
        name = place(self.s, "secops-receipt-html-sanitize", text="The flag secops-receipt-html-sanitize is off in production.")
        self.assertEqual(self.s.find("LaunchDarkly flag").id, self.s.find(name).parent_id)

    def test_a_new_one_word_generic_thing_is_refused(self):
        for n in ("watcher", "rollout"):
            with self.assertRaises(StoreError):
                place(self.s, n)
        self.assertEqual("LaunchDarkly", place(self.s, "launchdarkly"))    # an existing one is fine
        self.assertEqual("Nylas", place(self.s, "Nylas"))                  # a proper name is not a generic word


class Claims(unittest.TestCase):
    def test_a_claim_on_a_parent_path_lands_on_the_child(self):
        s = fresh_store()
        s.add_thing("LaunchDarkly")
        r = submit_claim(s, claim("LaunchDarkly > pdf-converter-service-enabled", "It is on for every org."))
        self.assertEqual("pdf-converter-service-enabled", r.state.thing_name)
        self.assertEqual(s.find("LaunchDarkly").id, s.find("pdf-converter-service-enabled").parent_id)

    def test_a_claim_on_a_new_generic_word_is_refused(self):
        with self.assertRaises(ClaimError):
            submit_claim(fresh_store(), claim("rollout", "The rollout goes one org at a time."))


class ExtractorPrompt(unittest.TestCase):
    def test_known_things_show_their_parent_and_the_prompt_asks_for_paths(self):
        s = fresh_store()
        s.add_thing("LaunchDarkly")
        s.add_thing("lob-webhook-require-signature", parent="LaunchDarkly")
        p = Extractor(s, call=lambda _p: "{}").prompt({"id": 1, "content": "turned on lob-webhook-require-signature today"})
        self.assertIn("lob-webhook-require-signature | thing | under LaunchDarkly", p)
        self.assertIn("LaunchDarkly | thing", p)
        self.assertIn('"LaunchDarkly flag > ', p.split("KNOWN THINGS (")[0])


class PrWriter(unittest.TestCase):
    def test_a_pr_whose_title_names_a_known_ticket_is_filed_under_it(self):
        s = fresh_store()
        s.add_thing("CRM-3340")
        payload = {"state": "OPEN", "isDraft": True, "title": "CRM-3340 ExportManager one-shot claim", "statusCheckRollup": [], "headRefOid": "abc1234"}
        observe_pr(s, "VirtuousV2", 3428, payload)
        self.assertEqual(s.find("CRM-3340").id, s.find("VirtuousV2 PR 3428").parent_id)

    def test_a_pr_filed_under_its_repo_moves_to_its_ticket_once_known(self):
        s = fresh_store()
        payload = {"state": "OPEN", "title": "CRM-3340 ExportManager", "statusCheckRollup": [], "headRefOid": "abc1234"}
        observe_pr(s, "VirtuousV2", 3428, payload)
        self.assertEqual(s.find("VirtuousV2").id, s.find("VirtuousV2 PR 3428").parent_id)
        s.add_thing("CRM-3340")
        observe_pr(s, "VirtuousV2", 3428, payload)
        self.assertEqual(s.find("CRM-3340").id, s.find("VirtuousV2 PR 3428").parent_id)


if __name__ == "__main__":
    unittest.main()
