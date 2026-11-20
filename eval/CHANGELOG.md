# Recall changes measured against the baseline

Every change here was measured with `python eval/run_eval.py` on the frozen snapshot `snapshots/shadow-20261120-092640.db` (see BASELINE.md), hook
path unless noted. Numbers are for the ORIGINAL 55 cases; the cases added later are reported separately so the baseline stays comparable.
"strict" and "lenient" are hit-any. Stale = cases (of 15 with a check) that injected a must-not state.

## 2026-11-20 recall-dev (sk-improve agent)

Branch `recall-dev`, commits 8483041..7db1aed on top of 00cd05c (the baseline as found). Old `main` was 279e0c0.

| Step | Strict | Lenient | Empty | Stale | Mean tok | Notes |
|---|---|---|---|---|---|---|
| Baseline | 0.236 | 0.600 | 0.236 | 4 | 109 | |
| (a) identifiers + same-PR supersession | 0.345 | 0.691 | 0.200 | 2 | 122 | live-pr 0.18 -> 0.73 |
| (d) people by name (asked-about only) | 0.382 | 0.727 | 0.164 | 2 | 124 | people 0.14 -> 0.43 |
| roll-up ("which PRs are red") | 0.400 | 0.745 | 0.145 | 2 | 132 | live-pr 0.82, none empty |
| (c) intents: ship/prod/last release, waiting on me; plan-overtaken-by-merge | 0.473 | 0.818 | 0.091 | 1 | 138 | unknown-unknown 0.40 -> 0.70 |
| one very rare shared word (df <= 5); counts/years never name a PR | **0.491** | **0.836** | **0.073** | **1** | 141 | |
| topic tree, `sk topic`, session topic index | 0.491 | 0.836 | 0.073 | 1 | 141 | per-prompt topic expansion tried and dropped |

Final by category (strict / lenient): live-pr 0.18/0.27 -> 0.91/0.91; unknown-unknown 0.40/0.50 -> 0.80/0.90; people 0.14/0.57 -> 0.43/0.86;
decisions 0.21/0.79 -> 0.21/0.79; how-tos 0.23/0.77 -> 0.23/0.77. hit@1 0.18 -> 0.36, MRR 0.21 -> 0.41. Noise share 0.52 -> 0.48.
`sk recall` mode: strict 0.35 -> 0.55, lenient 0.62 -> 0.82, stale 3 -> 2 cases.

Added cases (8; base code in brackets): 4 recall cases, strict 3/4, lenient 4/4 [0/4]; 4 guards (a count "3000 rows", a year "2026", a person named
without a question about them, "merged but not deployed"), all clean [base leaks two '#3000' states on the count guard].

Cost: mean injected tokens 109 -> 141 (p90 208 -> 212). Live hook process wall-clock on the snapshot (16 prompts, `inject_hook.py` end to end):
median 964-983 ms before, 1002-1071 ms after. SessionStart block 14131 -> 14481 chars (+ the topic index line).

### What changed in the live path (`retrieve.select`, `session.session_block`)

1. **Identifiers** (`identifiers.py`). "3342", "#3342", "PR 149", "build 13801", "CRM-3150" name every thing carrying that number (a CRM- number
   only the ticket's things). Bare numbers need 4+ digits, not a year (1990-2039), not followed by a count noun ("3000 rows"); a thing named only by
   a number ("#3000") is named only this way. A name "<words> #<n>" ("SendGrid webhook #3342") makes "<words>" another name for that PR when it is
   unambiguous and rare (<= 8 states mention it); for a "did X ship" question common phrases count too ("datepicker" -> #3340).
2. **Same-subject supersession for numbered things** (`entity_replaced`). Things carrying one PR number are one subject: its `work` notes recorded
   before its `pr.state` became merged/closed are dropped, and only its newest work note shows. A work note whose text names PRs that ALL merged/closed
   after it was written (a release plan, a merge order) is dropped wherever it is filed. Rules, stable facts and keyed values are never dropped.
3. **People by name.** Person/agent things can be named again (a unique capitalized first name counts: "Jen" -> Jen Gandy). Their rule/stable states
   stay out (session core); their work states show by name only when the input asks about them ("who's Jeremy", "what does Jen work on"); otherwise
   they need meaning as before.
4. **Roll-up.** "which PRs are red / drafts / approved" lists that keyed value for every open PR still being observed (pr.state confirmed within 6 h
   of the newest PR observation), up to 8, outside the 3-thing cap.
5. **Intents** (`intents.py`). "did X ship / is X in prod / gone from prod / what went out in the last release" add the newest release thing's states
   ("Build 7.15.95.13801"). "is anything waiting on me / what do I need to do" lists work notes saying something is pending for John, from the last
   72 h of the store. A condition ("merged but not deployed yet") is not a ship question.
6. **Very rare word.** A state sharing one word that is in at most 5 standing states qualifies at floor - 0.06.
7. **Topics** (`topics.py`). A tree derived on read from `parent_id` plus name containment. `sk topic <name>` lists the newest standing states under a
   topic and its sub-areas (overtaken notes left out); `sk topic` alone prints the index. The session block gains one line naming the 20 biggest
   topics (~350 chars).

Tried and rejected (no gain or a loss): crude stemming of rare words (strict 0.473 -> 0.436); lowering the same-subject cosine to 0.78/0.76 (strict
down to 0.436/0.418: the eval's primary state is often the older twin); per-prompt topic-tree expansion (no change, +40 ms).

### Rollback

- Per feature, no code change (shell env or the hook `env` in settings.json): `STATEKEEPER_IDENTIFIERS=0`, `STATEKEEPER_PEOPLE_BY_NAME=0`,
  `STATEKEEPER_ROLLUP=0`, `STATEKEEPER_INTENTS=0`, `STATEKEEPER_VERY_RARE=0`, `STATEKEEPER_TOPIC_INDEX=0`.
- All of it: in `C:/repos/statekeeper`, `git checkout 279e0c0 -- src tools tests` (the old main) and commit, or `git revert 8483041^..7db1aed`.
  The store itself was not written by any of this (no schema or data change); a backup was taken first:
  `~/.claude/statekeeper/backups/shadow-20261120-094512.db`.

### Known gaps (next)

- Decisions and how-tos did not move strict (0.21 / 0.23): the store holds the same rule under several ids and the eval's primary is often an older
  `John Ackerman` state that a newer restatement replaces. That is a dedupe/canonical-state problem for the updater, not retrieval.
- Wrong referent: "roll back the move off Persistence" still injects the rollback of the move INTO Persistence (#2568, cosine 0.81 vs 0.73).
- The working note is still hand-written and 41 h stale in the snapshot; "waiting on me" and the roll-ups cover part of what it carries.
