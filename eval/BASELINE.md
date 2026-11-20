# Statekeeper recall baseline (2026-11-20)

Goal: Claude is shown the CURRENT state of whatever is being discussed, reliably enough that the hand-written working note becomes unnecessary.
This is the measurement to beat before any change to recall, embeddings or decomposition.

## How it was measured

- Store: a verified online backup of `~/.claude/statekeeper/shadow.db` taken 2026-11-20 09:26 CT (`eval/snapshots/shadow-20261120-092640.db`, git-ignored via `*.db`): 3037 retrievable states, 10285 vectors. Ids in `cases.json` refer to this snapshot. The eval copies it again to a temp file per run; nothing in the live store is written.
- Path: `shadow_hook.prompt_context()`, the function the UserPromptSubmit inject hook calls, with the hook's parameters (floor 0.78, 3 things / 4 states, 1800-char budget, person/agent kinds and the working note excluded), a fresh session per prompt (no recent-skip, no topic carry-over), 4000-char cap, real embedding server (bge-small on :8081). `recall` mode is `sk recall` (looser: 5 things / 8 states). The tool-cue append in `inject_hook` is not exercised.
- 55 cases in `cases.json` (live PR 11, decisions 14, how-tos 13, people 7, unknown-unknown 10), each with the expected state ids, optional must-not-surface ids, and how the truth was verified (gh, file existence, e2e config grep, curl of the embed server, or "store only"). 15 cases carry a stale-state check.
- Grading: strict = an expected (primary) state is injected. Lenient = a primary state or a hand-adjudicated `also_ok` state (a relevant restatement or partial answer found in the first run). The adjudication is mine, not John's; lenient is an upper bound.

## How to run

```
python eval/run_eval.py --gh --telemetry            # both modes, writes eval/results.json (~4 min, one embed call per prompt)
python eval/run_eval.py --mode hook --verbose       # per-prompt injected states, + expected, ! stale, - other
python eval/run_eval.py --validate-only             # check case ids against the snapshot
python eval/run_eval.py --db <other.db>             # ids only valid for the snapshot; use regex groups in cases for a live store
```

Raw baseline: `eval/baseline-results.json`.

## Baseline numbers (hook path)

| Metric | Strict | Lenient |
|---|---|---|
| hit@1 | 0.18 | 0.45 |
| hit@3 | 0.22 | - |
| hit@any (an expected state injected) | **0.24** | **0.60** |
| MRR | 0.21 | 0.51 |
| Every expected group satisfied | 0.24 | - |
| Prompts that inject nothing | 0.24 | |
| Stale state injected (of the 15 cases that have a check) | 4 cases (27%) | |
| Superseded/closed state injected | 0 (retrieval already filters them) | |
| Injected states per prompt / ~tokens (chars/4) | 1.65 / mean 109, p90 208 | |
| Injected states that are noise | 52% (strict-noise 80%) | |

By category (hook, strict / lenient hit-any): decisions 0.21 / 0.79, how-tos 0.23 / 0.77, live PR 0.18 / 0.27, people 0.14 / 0.57, unknown-unknown 0.40 / 0.50. Live PR state is the worst category: 4 of 11 inject nothing, and none of the "current state of a PR" prompts that name only a number get the PR's own state.

`sk recall` path (looser): strict hit-any 0.35, lenient 0.62, empty 5%, 4.5 states / 256 tokens per prompt, noise 81%, stale in 3 of 15 cases.

Live telemetry from the store's own logs (829 real injections since 2026-11-16): 34% inject nothing, mean 825 chars, p90 1736; a turn where something was surfaced used one of those states in 31% of turns (169 of 547).

## SessionStart block

- 14,131 chars (~3.5k tokens) every session: 9.4k core (agent/person rule+stable states), 2.1k working note, 2.5k tracked-now (19 PRs).
- Working note was last changed 2026-11-18 22:40 UTC: 41 h old at eval time. It still presents as pending: #3329, #3343 and #3346 (merged 2026-11-20), #3340 "not yet in prod" (true, but only because the store says so), and a Monitor re-arm due 11-18 ~16:40 CT. Of the 12 VirtuousV2 PR numbers it mentions, 5 are merged on GitHub (3329, 3330, 3340, 3343, 3346).
- Tracked-now: 4 of 19 PRs disagree with GitHub 15 minutes after the snapshot (3 merged 10 minutes after the last 20-minute observe pass; #3342 was marked ready after it). Each carries a "STALE" tag only after 6 h, so a 20-minute-old value reads as current.
- The working note is excluded from per-prompt recall by design, and most of what it holds (what is running, what waits on John) is not decomposed into recallable states.

## Failure patterns

1. **A bare PR or ticket number does not resolve to the thing.** PR states are filed under "VirtuousV2 PR 3342"; "is 3342 still a draft", "where are we on CI for 3266", "Copilot left a message on 3313" and "which PRs are red right now" all inject nothing. Real prompts at 15:24 and 15:26 UTC on 2026-11-20 about 3313 and 3349 also injected nothing (shadow_injections), while 3349 has current pr.state/pr.ci/pr.review states and CRM-3152 notes.
2. **Proposals and superseded-in-practice states outrank the current fact.** "did the datepicker ship" injects #1448 ("release plan proposes...") and an unrelated build-spec note; the answer (#4000 "build 13801 has shipped", #4001 "#3340 not in it", PR 3340 merged) is filed under "Build ..." and "#3340", never named in the prompt. "what's the rule on merging" injects #1277 (a stale per-PR merge-order note, proposed trust) and not John's rule (#1265/#227). A `work` state keeps competing after reality moves on; nothing checks a state's age against newer states on the same subject.
3. **Different words, same state, empty or wrong.** "is the Eonasdan thing gone from prod", "what went out in the last prod release", "is anything stuck waiting on me", "what happened to the old memory system" all inject nothing although #4000/#4001/#4022/#4023/#4006 exist. "how do I roll back the move off Persistence" injects #2568, the rollback of the earlier migration INTO Persistence (a wrong referent). On 2026-11-20 the first Statekeeper-recall prompt returned "receipting plan fragments" (#3266, an id that coincides with a PR number) and "Persistence working model".
4. **Wrong state ranked above the right one.** "how do I log in to QA": #3658 (an unverified admin URL) first, #788 ("agent may view credentials", in tension with the never-type-passwords rules) third, the correct #3959 fourth. Two stores of the same fact (e.g. 3109/3065/130 for pointing, 2958/2960/179 for script validation) fill the 4-state cap with restatements; a stale twin can outrank a good one (#3913 "10% of high-risk" is newer by id than #3852/#3853 but older in time).
5. **Short facts, no current-state shape for what is not a PR.** Person/agent things hold 136 `work` states and only 13 rule states; per-prompt recall excludes those kinds by name and the session core only takes rule/stable, so "who has to review a critical PR" (#580 Adam), "what does Jen work on" (#626) and "who's Jeremy" inject nothing (people 0.14 strict). And the session core spends ~9k chars every session on states that are mostly already known, while live PR/CI values reach the model only as a 20-minute-old snapshot with no per-prompt refresh.

(Observed from the numbers above: patterns 1-3 are measured in the 55 cases; the "why" in each is my inference from the injected text and `retrieve.py`, not yet traced with a debugger.)

## Recommended first change (not implemented)

Make identifier references resolve (pattern 1): normalise bare PR/ticket/build numbers in the prompt ("3342", "#3342", "CRM-3150", "build 13801") to their things (and aliases) before selection, and let a named keyed thing surface its current `pr.*`/`build.*` values even at low semantic score. It is the cheapest change, it targets the worst category (live PR 0.18 strict, 4 of 11 empty, John's own real empty prompts), and it can be verified against `cases.json` without touching embeddings. Second: a "newer fact on the same subject wins" pass (patterns 2 and 4) so proposals and older restatements stop outranking current states; third: alias or entity linking so "Eonasdan"/"last prod release" reach the build and PR states (pattern 3). Do not tune the embedding model first: lenient hit-any is already 0.60, so most failures are selection and shape, not vocabulary.

## Caveats

- Ground truth is the store plus point-in-time gh/file checks; where a fact is "store only" the case says so. PR state moves: 3329/3343/3346 merged 10 minutes after the snapshot, 3342 was marked ready after it. Re-snapshot and re-verify before comparing against a later store.
- The first baseline run is what informed the `also_ok` adjudication, so lenient numbers are optimistic. Strict ones are conservative because the store restates facts under many ids.
- The wall-clock `STALE` and working-note-age figures are as of 2026-11-20 ~16:30 UTC.
- 55 cases is small: a change of a few points per category is noise.
