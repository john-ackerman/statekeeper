# Recall changes measured against the baseline

Every change here was measured with `python eval/run_eval.py` on the frozen snapshot `snapshots/shadow-20261120-092640.db` (see BASELINE.md), hook
path unless noted. Numbers are for the ORIGINAL 55 cases; the cases added later are reported separately so the baseline stays comparable.
"strict" and "lenient" are hit-any. Stale = cases (of 15 with a check) that injected a must-not state.

## 2026-11-23 hierarchy filing, re-parent, conversation import gate (sk-improve2)

Measured on a verified copy of the live store taken 2026-11-23 11:42 (not the frozen snapshot: the hierarchy needs today's things), all three
columns with the same final code. Audit set = `cases-audit-20261123.json`, now 13 cases (3 new `hier-*` cases: one flag, two flags, parent only).

| Store | cases.json hook hit@all / hit@1 / lenient | recall hit@all / hit@1 | audit hook hit@all / hit@1 | audit recall hit@all / hit@1 | audit stale cases |
|---|---|---|---|---|---|
| copy as found | 0.509 / 0.400 / 0.745 | 0.582 / 0.418 | 0.462 / 0.231 | 0.462 / 0.154 | 2 |
| + `tools/reparent.py --apply` (562 moves, 3 flag merges) | 0.509 / 0.400 / 0.745 | 0.582 / 0.418 | 0.462 / 0.385 | 0.538 / 0.308 | 1 |
| + 60-fragment conversation import sample (89 written, 43 corroborated, $0.17) | 0.509 / 0.400 / 0.745 | 0.582 / 0.418 | 0.462 / 0.385 | 0.538 / 0.308 | 1 |

Rejected on the way (each cost cases): treating any thing with a child as generic (the board watcher lost its own states once "board watcher
Monitor" was filed under it: only a container with more than 6 children is generic now); excluding a sibling spelled almost the same ("board-watch"
beside "board watcher"); un-naming a name said inside a longer name even when it is above that thing ("DocRaptor" in "DocRaptor fallback" lost
dec-docraptor-removal and dec-receipt-model lenient hits) or when the longer name was only resolved from a number ("PR 3266" vs "VirtuousV2 PR
3266" from "CI for 3266" lost pr-3266-ci and pr-3313-copilot).

Full conversation import on the live store (2026-11-23 12:30-14:11, after the ff to 116256f and `reparent --apply`: 564 moves, 3 merges):
2509 fragments, 4656 states written, 3238 corroborations (the fragment added as evidence), $8.76. **Recall got worse** (copies before/after):
cases.json hook hit@all 0.509 -> 0.382, recall 0.582 -> 0.473; audit recall hit@1 rose 0.308 -> 0.385 but hook hit@all stayed 0.385. The 60-sample had
not shown it. Cause seen in the misses: identifier-only states on numbered things ("#3337, #3338 and #3340", "Build 7.15.95.13801") count as
specific named states (score 1.0+) and displace the keyed and standing states for the same number ("is 3340 in prod yet", "last prod release");
"board watcher" went empty. All 4656 imported states were closed (reason "conversation import held back ...", `reopen` restores them); after the
close: hook hit@all 0.509, recall 0.564 (remaining gap is live drift: two asks closed by the board writer in between, and one how-to swap).
Next attempt needs the name-only tier to rank below every non-imported state of the same thing and never take a cap slot from one.

Second attempt (667ab45): the unreviewed tier is appended after every other qualifying state and left out of state counts, word rarity,
replacements, intents (newest release, asks, PR roll-up, waiting on John), PR phrases and canonical picks. Live copies (14:31), closed / 300
fragments reopened / all reopened: cases.json hook hit@all 0.509 / 0.509 / 0.509, recall 0.564 x3; audit hook 0.385 x3, recall 0.385 x3 (the first
pass of the fix, without the intents/phrase exclusions, still lost 3 cases on the full set: hook 0.455). All 4656 reopened on live at 14:57
(backup shadow-20261123-145705.db); live after: hook 0.509, recall 0.564, audit 0.385 / 0.385, the same as before the reopen.

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

## Round 3 (recall-dev-3, 2026-11-20 evening): observers for the working-note gaps, pending items as states, session-core credit, note-off trial

Store backed up first: `~/.claude/statekeeper/backups/shadow-20261120-111309.db` (integrity ok). Nothing superseded or retired among states that existed
before this round; the only retire is #4136 -> #4137, a duplicate this round wrote itself (below).

### 1. Two observers (live now: written by `tools/observe_all.py`, nightly and the 20-minute shadow-hook run once main has this commit)

- `writers/board_asks.py`: board `/api/asks` (read only) -> keyed `ask.state` on "board ask <id>": `open · decide · since 2026-11-16 · <title, 110 chars>`.
  Titles only (never the body), at most 40 open asks per read (newest first), the value carries the creation DATE so an unchanged ask only confirms.
  An ask that is done gets `done ...` once; one that vanished from the board gets `gone ...` once. 22 open asks were written on the first live read
  (11 of them are the board's own `auto-*` "re-run CI" chores; the roll-up lists the others first).
- `writers/watcher_health.py`: heartbeat file `C:/repos/_handoffs/orch-watch-heartbeat` (epoch seconds; mtime as fallback), the pause flag
  `orch-watch.paused`, and whether the board answers `/` -> keyed `watcher.status` on "work-board watcher":
  `running | heartbeat stale | not running | paused (idle flag set, watcher not expected) | no heartbeat file · board server answering/NOT answering · last
  heartbeat Nm ago`. Same state word = confirm only. Thresholds: fresh <= 20 min, dead > 180 min. Read from this machine's handoffs dir (override
  `STATEKEEPER_HANDOFFS_DIR`).
- Wiring: `observe_everything(get_board_asks=, watcher=)`; the board being down is quiet for asks (the watcher state says so), any other failure is
  recorded in `errors`, never fatal. `session.whats_running` gains "Open asks on the board (N)" (top 8, the board's `auto-*` chores last, "... and N
  more") and one "work-board watcher: ..." line; both only when re-read within STALE_HOURS (6), so a dead observer or board shows nothing instead of
  a stale claim. Roll-up cost: about +1.1k chars when asks are many.
- Switches: `STATEKEEPER_BOARD_ASKS=0`, `STATEKEEPER_WATCHER=0` (observe_all), `STATEKEEPER_RUNNING=0` (the roll-up section).
- Tests: `tests/test_board_asks_watcher.py` (9 tests: title-only, confirm vs final value, cap, quiet board-down, roll-up, watcher states by age/pause/server,
  missing directory). Suite: 215 tests, OK.

### 2. The working note's pending items, verified, as states

The current note is the 2026-11-18 22:40 one (42 h old). Each item checked against reality before copying:

| Note item | Reality (2026-11-20) | Done |
|---|---|---|
| install-supervisor-task.ps1 pending for John | schtasks lists no supervisor task; script exists | #4139 written (the 11-18 #4022 falls out of the 72 h window tomorrow) |
| board-health hook snippet pending | no board_health in ~/.claude/settings.json; snippet exists | #4140 |
| AntiXss worktree needs commit | `.claude/worktrees/xss-fix`: AntiXssSanitizingFilterTests.cs modified (2+/3-), uncommitted; the "BOM restore" detail not verified | #4141 (BOM left out) |
| #3329 base merge | 3329 merged | moot, not written |
| held PDF merges (#2132) | #2132 MERGED; #2129 open approved, #2130 open no review (the PR writer tracks both) | moot, not written |
| Tim's #3329 questions | 3329 merged | moot |
| #3340 not in prod | #3340 merged 11-18 21:22Z; whether a later release shipped it is NOT verified (#4001 says not in build 13801) | not written |
| #3320 stacked on #3266 | #3320's base is the CRM-2970 branch, open, approved; stacking claim not verified | not written (PR states are tracked) |
| #3375 demo ask | no ask for it on the board; PR open approved, 16 checks green, demo files exist | moot |
| CI reruns queued | the board now carries them as `auto-rerun-*` asks | carried by the asks observer |
| watcher armed / re-arm | board health: idle pause flag set | carried by watcher.status |
| jobs staging-slot drain (option 3), lockBehavior, "John asks Jen" about the storage 403 | no state, file or PR found to verify any of them | NOT written (unverified); lockBehavior is #3988 |

All three new states are `proposed/claimed` (agent writes); they show in the roll-up's "Pending for John" (verified) but not in per-prompt recall until
corroborated.

### 3. Session-core credit in the eval

`run_eval.py` now also reports, beside every old number, hit@all/full "with credit for facts already in the session block": an expected state counts
when its text is in `session_block` with the note OFF (core + topic index + tracked + roll-up). Per row: `in_session_block`, `core_hit`, `core_full`.
Old metrics are unchanged and still printed first.

### 4. Note-off trial: prepared, NOT enabled

**What to set.** `STATEKEEPER_SESSION_NOTE=0`, read by `session.py` at import, so it must be in the SessionStart hook's environment. The hooks
(`adapters/claude_code/inject_hook.py`, `subagent_hook.py`, `precompact_hook.py`) are launched by Claude Code and inherit the `env` block of
`~/.claude/settings.json` (today lines 2-7: `CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS`, `NTFY_TOPIC`, `STATEKEEPER_INJECT`, `STATEKEEPER_CURATE_AT`). Add
`"STATEKEEPER_SESSION_NOTE": "0"` there; it applies from the next session start. (Changing settings.json is John's step; I did not touch it.)
`sk note` keeps working; the note stays in the store and is still the compaction-time fallback.
**Rollback:** delete that line; the note is back at the next session start; nothing is deleted either way.

**What to log as a miss.** Any time during the week a session (or John) has to ask for, or rediscovers, something the note used to carry: write one
line to `C:/repos/_handoffs/note-off-misses.md` as `date | what was needed | where it was found (store id / file / John) | was it in the 11-18 note?`.
The store's own miss log (`python tools/misses_report.py`) also records recall misses the hooks see; use it for the same week.

**How to judge** (at day 7): (1) number of note-needed misses; target <= 2 that were not already stale in the note; (2) no "what is pending for me" question
John had to answer by hand that the roll-up (Pending for John / Open asks / agents) should have shown; (3) session start about 1.5k chars shorter
(note-off 14.5k vs 16.0k with the note on, measured on the live store 2026-11-20). If the misses cluster on one kind of item, write that kind as states
(`sk add <thing> "... is pending for John"`) rather than turning the note back on.

**Dry run (live store, 2026-11-20, `session_block` with the note off, 14.5k chars) against the note's still-true items:**

| Still-true item | In the note-off block? |
|---|---|
| Build 13801 shipped | yes (Last release) |
| Supervisor task / board-health hook pending for John | yes (#4139/#4140/#4022/#4023 under Pending for John) |
| xss-fix worktree needs a commit | yes (#4141) |
| Open decisions on the board (3313, 3349, 3342, 3376, kickoff, ReDoS ...) and CI re-run chores | yes (Open asks, 22) |
| Watcher state (paused by idle flag, board answering) | yes (watcher line) |
| Running agents | partly: board-registered agents only; transcript agents have no task |
| Never merge / ready / reply to humans / approve prod gates | yes (core) |
| "Agents push only via gh-safe" as a standing rule | NO: only gh-safe tips exist (#3687, #3793, #1450); it is a core rule, so John's say-so to add |
| QA login tool path | not in the block (it is in MEMORY.md, loaded every session, and recall #3959) |
| Statekeeper cutover applied / rollback; LD flags created off; board To-do grouping | not in the block (history; recall finds #4002/#4004, #2156) |
| Jobs drain idea, lockBehavior, "John asks Jen" | not in the block; the first and last could not be verified at all; lockBehavior is #3988 (recall) |

Gap list: one real gap (gh-safe rule). The rest are history, loaded elsewhere, or unverifiable.

Numbers (snapshot 17:02, 55 original cases, hook mode; same code path as before, old numbers unchanged): strict hit 0.582, lenient 0.818, stale 0.
With credit for the session block (note off): hit@all 0.582 -> 0.618 (+2: `dec-prod-gates` via #3994 "John runs every prod gate himself", `ppl-dictation` via
#340/#1825); full 0.582 -> 0.618. 16 cases have an expected state in the session block; 14 of them the hook also found. Recall mode: strict 0.655 / lenient 0.800
(unchanged). Three new cases were added (below), so "added" is now 7 cases: hook 0.429, recall 0.571.

### Real recall failure: "watch the PDF converter deploy" (reported by the coordinator, 2026-11-20)

Cases added (`howto-pdf-cd-pipeline`, `howto-pdf-deploy-prod`, `howto-watch-pdfconverter-cd`, expect #1486 or the new how-to #4137/#4138). All three MISS in
hook mode (one injects nothing) and two in recall mode; they are in the eval as honest misses.

Why the hook omitted it (probe on the 17:02 snapshot and a copy of the live store; `eval/probe.py`, `retrieve.select`):
1. **Filing.** #1486 is filed under the thing "#2026" ("#2026 is deployed: ... pdfconverter-api-cd (pipeline 139, Virtuous Platform) run 74020 succeeded"), so a
   prompt naming the pipeline or "PDF converter" never names its thing; it can only qualify by meaning.
2. **Floor.** By meaning #1486 scores 0.729 / 0.671 / 0.782 on the three prompts against the hook floor 0.72: the second is under it and never qualifies.
3. **Ranking and caps (the main one).** A state of a *specific* named thing (<= 6 states) is scored 1.0 + cos/10 whatever it says, above every
   meaning-only match (< 1.0). "PDF converter" and "pdf-converter" (spelled in the prompt; few states) put #649 (an agent changing maxReplicas), #2016 (a
   replica formula) and #4095 (non-root uid) in first, and the hook's cap of 3 things / 5 states cut the rest ("13 omitted" includes `pdfconverter-api-cd`
   itself, whose own #3760 was also dropped as older than #3761 "PR #2092 ... auto-deployed to prod by CD run 74020").
4. The org/project/commands were never stated as a how-to anywhere (`sk search "dev.azure.com virtuoussoftware"` and `"az devops invoke timeline"` found
   nothing); #1486 only holds the pipeline id and project.

Tried: score a specific-named state by its meaning (cos + 0.03) when it is also above the floor, so a name alone cannot outrank a closer state. Result on the
17:02 snapshot: strict hit unchanged (0.582), lenient unchanged (0.818), hit@1 0.436 -> 0.418, MRR 0.496 -> 0.487, and #1486 still not surfaced.
Rejected and reverted (not in the commit). A principled fix needs the filing (#1486 under the pipeline thing, a supersede = a later reviewed step) or the
updater linking a state to every thing its text names.

Added to the live store (stable how-to under "pdfconverter-api-cd", commands re-run today: run 74594, pipeline 139, stage timeline): #4137 (list runs: org
https://dev.azure.com/virtuoussoftware, project "Virtuous Platform", --pipeline-ids 139) and #4138 (per-stage result via `az devops invoke --area build
--resource timeline`). `sk add` wrote a first, too-long version (#4136) despite its "long" warning; I retired my own #4136 into #4137 (the one retire this
round, on a state created minutes earlier). Both new states are `proposed` (agent claims), which the per-prompt hook does not show; with the two
activated on a copy of the store, the exact-name prompt ("watch the pdfconverter-api-cd run") surfaced #4138 and the other two did not (cap again).
