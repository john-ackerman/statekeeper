import json
import os
import tempfile
import time
import unittest

import support  # noqa: F401
from support import fresh_store
from statekeeper.writers.agent_status import normalize, observe_agent, status_from_transcript


def write(path, entries, age_seconds):
    with open(path, "w", encoding="utf-8") as f:
        for e in entries:
            f.write(json.dumps(e) + "\n")
    t = time.time() - age_seconds
    os.utime(path, (t, t))


TOOL = {"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "Bash", "input": {}}], "stop_reason": "tool_use"}}
DONE = {"type": "assistant", "message": {"content": [{"type": "text", "text": "done"}], "stop_reason": "end_turn"}}


class Reading(unittest.TestCase):
    def test_status_comes_from_the_transcripts_last_entry_and_age_not_from_what_the_agent_says(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "agent-abc.jsonl")
            write(p, [TOOL], 20)
            self.assertEqual("working", status_from_transcript(p)["status"])
            write(p, [TOOL], 3600)
            self.assertEqual("silent", status_from_transcript(p)["status"])
            write(p, [TOOL, DONE], 3600)
            self.assertEqual("finished", status_from_transcript(p)["status"])
            write(p, [TOOL, DONE], 5)
            self.assertEqual("finished", status_from_transcript(p)["status"])
            self.assertEqual("agent-abc", status_from_transcript(p)["agent"])

    def test_the_text_is_stable_while_the_status_is_unchanged(self):
        self.assertEqual(normalize({"agent": "a", "status": "working"}), normalize({"agent": "a", "status": "working", "last_activity": "2026-11-16T20:00:00Z"}))
        self.assertEqual("working on TICKET-1", normalize({"agent": "a", "status": "working", "task": "TICKET-1"})["agent.status"])


class Observing(unittest.TestCase):
    def test_a_status_change_supersedes_the_old_status_and_an_unchanged_one_only_moves_last_verified(self):
        s = fresh_store()
        clock = iter(["2026-11-16 20:00:00.000+00:00", "2026-11-16 20:01:00.000+00:00", "2026-11-16 20:30:00.000+00:00"])
        observe_agent(s, {"agent": "agent-abc", "status": "working"}, lambda: next(clock))
        same = observe_agent(s, {"agent": "agent-abc", "status": "working"}, lambda: next(clock))
        gone = observe_agent(s, {"agent": "agent-abc", "status": "finished"}, lambda: next(clock))
        self.assertEqual(([], ["agent.status"]), (same["changed"], gone["changed"]))
        thing = s.find("agent-abc")
        self.assertEqual(["finished"], [x.text for x in s.states_of(thing.id)])
        self.assertEqual(2, len(s.states_of(thing.id, history=True)))
        self.assertEqual("live", gone["agent.status"].volatility)


if __name__ == "__main__":
    unittest.main()
