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

## 2026-11-20 recall-dev-2 (sk-improve2 agent)

Branch `recall-dev-2` on top of e67b0d3 (main as found). Developed in the worktree `C:/repos/statekeeper-dev-2`; the live hooks were not
touched until the fast-forward. Store backed up first: `~/.claude/statekeeper/backups/shadow-20261120-104225.db`. No store writes by any of
this (the board-agents observer adds keyed states once it runs from the live copy).

### Numbers (hook path, original 55 cases, snapshot 2026-11-20 09:26)

| | Strict | Lenient | Empty | Stale | Mean tok | Median ms (eval) |
|---|---|---|---|---|---|---|
| Before (e67b0d3) | 0.491 | 0.836 | 0.073 | 1 | 141 | 971 |
| Canonical states + retirement | **0.582** | 0.818 | 0.073 | **0** | 143 | 987-1037 |

By category, strict / lenient: decisions 0.21/0.79 -> 0.43/0.79; how-tos 0.23/0.77 -> 0.31/0.77; live-pr 0.91/0.91 -> 0.91/0.91;
people 0.43/0.86 -> 0.43/0.71; unknown-unknown 0.80/0.90 -> 0.90/0.90. `sk recall` mode: strict 0.55 -> 0.655, lenient 0.82 -> 0.80.
Added cases 0.75 strict (unchanged), lenient 1.0 -> 0.75 (add-ppl-adam: #278, proposed, now gives way to its accepted twin #2774); guards clean.

**Read this before the strict gain:** a cluster-aware re-grade (a shown state counts when `canonical.restates` says it is the same fact as an
expected one) gives 0.582 strict / 0.836 lenient for BOTH runs. The baseline already showed a twin of the expected state in those five cases
(dec-stack-vs-fanout, dec-parent-points, dec-data-script-validate, how-launchdarkly-read, unk-kill-switch); the change makes the copy shown
the one closer to the source, with attribution ("John set a pointing rule on 2026-11-12 ..."), which is the copy the eval's author picked. The
two lenient losses are the same thing the other way round (ppl-john-adhd #2187 -> its twin #567; add-ppl-adam). So: better copies, no new facts.
The stale leak fixed (how-rollback-cutover) is a real change. The remaining decision/how-to misses are not restatements: the expected state
never qualifies (different words, or the answer is already in the session core, e.g. dec-prod-gates: "John runs every prod gate himself").

**Re-snapshot (task 4).** New snapshot `snapshots/shadow-20261120-110241.db` (17:02 UTC, last PR observe 16:41). `eval/remap_cases.py`
carries every expected id that is no longer standing to what replaced it (9 cases moved, all keyed PR values); the moved PR facts were
re-verified with gh (`reverified` in `eval/cases-20261120-110241.json`: 3342 is no longer a draft, 3343 merged, 3266/3320 red, 3313 approved).
Run: `python eval/run_eval.py --db eval/snapshots/shadow-20261120-110241.db --cases eval/cases-20261120-110241.json`.

| Snapshot | Code | Hook strict / lenient / stale | Recall strict / lenient |
|---|---|---|---|
| 09:26 (original cases) | e67b0d3 | 0.491 / 0.836 / 1 | 0.55 / 0.82 |
| 09:26 (original cases) | this round | 0.582 / 0.818 / 0 | 0.655 / 0.800 |
| 17:02 (remapped cases) | e67b0d3 | 0.491 / 0.836 / 1 | 0.545 / 0.818 |
| 17:02 (remapped cases) | this round | 0.582 / 0.818 / 0 | 0.655 / 0.800 |

Hook latency, `inject_hook.py` process end to end on the 17:02 snapshot copy (20 prompts each): e67b0d3 median 931 / 946 ms (two runs),
this round 971 ms (p90 968-978 -> 999 ms): about +30 ms.

The numbers did not move with the snapshot: the live-PR cases follow the remapped keyed values. Session block 14.3k -> 14.7k chars on the new
snapshot (the roll-up), with the note still in.

### What changed in the live path

1. **Canonical states** (`canonical.py`, after the existing newer-wins passes in `retrieve.select`). Two standing unkeyed states restate each
   other when they share a source (`legacy:N` = `persistence:N`, a message, a file, a parent state) and are close (cosine >= 0.75), or have no
   shared source and are very close (>= 0.85, more overlap); their IDF-weighted content-word Jaccard is >= 0.50 (0.60) and the shorter one's
   words are >= 65% in the other; they carry the same numbers; neither negates what the other says plainly; and neither names an identifier
   value the other does not say (admin-qa... vs admin..., ReceiptProcessorBase vs ExportManager, persist-fb-13 vs persist-fb-<n>). A cluster is
   the canonical plus what restates IT (not a chain). The canonical: accepted over proposed, filed under the thing the input names, learned most
   recently, verified, closer to the source (not rewritten from another state), carries more of the cluster's words, newer id. It is shown once
   at the best rank any copy earned, tagged "same fact as #N"; the rest drop. A cluster whose canonical is in the session core (or was shown
   earlier in the session) is dropped whole. Cost: ~10 ms (evidence lookup) + ~3 ms index + ~8 ms per prompt.
2. **Retirement** (`retired_things`, `overtaken_by_retirement`). A thing whose own state says "<thing> is frozen / retired / replaced by / kept
   only as history": its states recorded before that give way to the declaration, unless the input asks about history. Today this matches one
   thing (Persistence store); "the standalone-service plan is superseded" filed under "receipting" does not retire receipting. ~9 ms.
3. **`sk dupes [thing] [--json]`** lists every cluster as a supersede proposal with its evidence (shared sources, cosine, overlap, why this copy
   is kept); **`sk retire <id> --into <keep>`** applies one by hand. Nothing auto-supersedes. On the 2026-11-20 17:02 snapshot: 234 high and
   68 medium restatement clusters (306 states would retire if all were accepted) and 20 low "updates" (a number or a negation differs).
   Read precision: 25 of 26 sampled high ones are the same fact (the one weak pair, a lesson vs the incident it cites, is now medium);
   13 of 14 medium ones (the miss: "Claude curates its store" vs "John curates his store", a subject swap the guards do not see).
   The proposal format is the one a later Haiku updater would emit: `eval/UPDATER.md` (with a cost estimate).
4. **What's running** (`writers/board_agents.py`, `session.whats_running`). observe_all reads the work board's agents (read only) into keyed
   `agent.status` states on "board agent <id>"; the SessionStart block gains "What's running": running/waiting board agents with their card,
   the newest release, and pending-for-John notes from the last 72 h (~400 chars). `STATEKEEPER_SESSION_NOTE=0` leaves the hand-written note
   out (default still on; see the cutover proposal below).

Tried and rejected: transitive clusters (a rule and an instance of it chain through a third wording); as-of dates for "newest" (a rewrite drops
the as-of, so the original looked older); running the collapse before the newer-wins passes (a canonical then outlived a newer state its copy
had lost to; the eval lost how-qa-login); directional parsing of "A-to-B" names for the wrong-referent case (one thing in the store has such a
name, so it would be a rule written for one eval case).

### Wrong referent ("how do I roll back the move off Persistence")

Partly fixed, in a principled way: the Persistence store's own newest state says it is frozen (#4006, 2026-11-18), so the 2026-11-09 recipe for
rolling back the move INTO Persistence (#2568) is history and the hook now shows #4006 in its place (stale leak gone). The right answer (#4004,
the cutover rollback) still does not surface: nothing in the store says "the move off Persistence" is the Persistence-to-Statekeeper cutover,
and the wording is too far in meaning (cosine 0.73 vs floor 0.78). Two ways to close it, both outside this round: an alias
(`sk alias "Persistence-to-Statekeeper cutover" "move off Persistence"`, John's call; not added because it would be fitted to the eval case) or
the updater linking a fragment's concepts to things. A direction parser was rejected (above).

### Rollback

- Per feature, no code change: `STATEKEEPER_CANONICAL=0` (collapse and retirement), `STATEKEEPER_RUNNING=0` (the roll-up section),
  `STATEKEEPER_BOARD_AGENTS=0` (observe_all stops reading the board).
- All of it: in `C:/repos/statekeeper`, `git reset --keep e67b0d3` (main as found) or `git revert e67b0d3..<head>`. The board-agent states
  the observer writes are ordinary keyed states under "board agent <id>"; they can stay (nothing reads them with the roll-up off).

### Working-note retirement: gap list (note of 2026-11-18 22:40 UTC vs snapshot 2026-11-20 17:02 + one board read)

Each claim of the current note, and whether the session block WITHOUT the note (core + topic index + tracked now + what's running) or `sk recall`
with a natural prompt carries it now. Method: `STATEKEEPER_SESSION_NOTE=0`, the board agents observed once into a copy, 19 prompts.

| Note claim | Carried by | Status |
|---|---|---|
| Build 13801 shipped (datepicker stack except #3340; identity migration applied) | roll-up "Last release"; recall #4000/#4001, #3997 | covered |
| Statekeeper cutover applied, rollback script | recall #4002/#4004 | covered (recall only) |
| LD flags created, off | recall #2156 | covered (recall only) |
| Board To-do grouped by kind; file helper (7789); reliability work live | recall #4013 (To-do); helper/reliability not found | partial |
| Watcher: Monitor id, armed ~16:40 CT, how to re-arm | how: recall #4020/#4021; whether it is armed NOW: nowhere | gap (live watcher state; the note's value is 42 h stale anyway) |
| Rules: never merge/ready/reply to humans/approve prod gates; John merges | session core (John Ackerman rule states) | covered |
| Agents push only via gh-safe | core/recall not checked to the word | probably covered (gh-safe rules in store) |
| Running agents and what each is for | roll-up from board agents (only agents registered on the board); transcript agents show as "agent-a...: working" with no task | partial: unregistered agents have no task |
| Waiting on John: supervisor task, board-health snippet | roll-up "Pending for John" (#4022/#4023) | covered |
| Decisions: #3329 base merge, #3320 merge order | PR states (3329 merged since) ; no pending-decision state for 3320 | gap (pending decisions are not stored as such) |
| CI reruns queued (builds 74170-74176) | tracked-now PR CI values | covered (superseded by live values) |
| #3340 not yet in prod | recall #4001 | covered |
| #3375 demo ask on the board | PR state only; the board ask is not observed | gap (board asks are not read) |
| Tim's #3329 questions answered, reply with John | not in store | gap (moot: 3329 merged) |
| Jobs staging-slot drain idea (option 3, dev plan next) | recall #3968 (the QA proof), not the choice | partial |
| lockBehavior runLatest deferred to the team | recall #3988 | covered |
| Held PDF merges (#2132) until QA gate #2129/#2130 reviewed | tracked-now shows WS 2129/2130 open; the hold itself is not stored | gap |
| crm-web-api storage 403 predates deploy; John asks Jen | recall #3978; "John asks Jen" not stored | partial |
| AntiXss worktree needs BOM restore + commit | not in store | gap |
| QA login tool path | recall #3959, core | covered |

Summary: 11 covered, 4 partial, 6 gaps. The gaps are of two kinds: (a) live state no observer reads yet (whether the watcher is armed; board asks;
what an unregistered agent is doing), and (b) pending items that were only ever written in the note (held merges, a worktree needing a commit,
a decision awaiting John). (a) needs observers; (b) needs the note's writer to write them as states instead (`sk add <thing> "... is pending
for John"` already flows into the roll-up).

### Cutover proposal (not applied)

1. Close the (a) gaps first: observe board asks (open asks become keyed `ask.state` values; the roll-up lists the ones waiting on John) and the
   watcher (`board.sh health --json` into a keyed `watcher.status`).
2. Change the note-writing habit: anything pending goes in with `sk add <thing> "<what> is pending for John ..."` (it shows in "Pending for John"
   for 72 h) instead of only in the note.
3. Then run one week with `STATEKEEPER_SESSION_NOTE=0` in the SessionStart hook's env (settings.json `env`), keeping `sk note` working, and
   compare: any time a session asks for something the note had, log it as a miss (`sk` misses report).
4. Rollback: remove the env var (the note is back on the next session start); nothing is deleted.
   Size: the block drops from ~14.9k to ~12.8k chars (note -2.1k, roll-up +0.4k).
