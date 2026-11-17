# C. Incremental adoption: slices that each stand alone and are testable on replay

How to bring state-first recall in step by step, next to whatever recall system an agent already has (called "legacy" here: a store of facts or
fragments that is searched and injected on each prompt). Every slice has its own replay measure; nothing in the replay tooling scores itself, a person judges.

Replay tooling: `tools/replay.py` re-runs real past inputs through state-first retrieval and writes an HTML sheet to judge by hand (optionally beside what the legacy system surfaced);
`tools/misses_report.py` runs the automated miss detectors; `tools/extract_sample.py` measures the extractor's reject rate on a seeded sample.
Input for all of them comes from an export of the earlier store (`tools/import_legacy.py` documents the format).

## Slice 1: import and state-first retrieval

Import the legacy facts one way (`tools/import_legacy.py`): accepted facts become standing derived states, proposals stay proposals, rejected stay rejected, superseded stay superseded.
Then retrieve by state: the state that made a thing relevant is shown first, proposals are searchable by meaning too, and the floor for meaning matches is a tunable parameter
(`relevance_floor`, default 0.72). Replay a fixed set of real past inputs, mark each surfaced entry good or bad, and compare hit rate (turns that surfaced a needed state), precision per entry and
the number of turns correctly left empty against the legacy column. The floor is part of the change: most of a precision gain usually comes from the higher floor and the spelling guard (slice 2), the hit-rate gain from embedding proposals.
Not shown by replay: that an agent behaves better with this context than with fragments.

## Slice 2: spelling guard

Reject a near-spelling match that is a truncation or prefix of a label ("claud" matching Claude). Measure the wrong entries removed on the same replay and check that real near-spellings stay.

## Slice 3: deterministic state from structured inputs

Writers (`src/statekeeper/writers/`) record PR state, CI state and agent status as verified keyed states with a last-verified time, so a state is shown as "verified Nm ago" or stale rather than assumed current.
Measure on past turns with structured inputs: the state a turn would have shown versus the truth read from the artifact; the stale-shown rate must be 0 for live keys.
Open question: whether to write live external state into the store as history (the gain is "what was the CI state when we merged"; the cost is write churn). Lean: transitions only, never polled values.

## Slice 4: commitments and the end-of-turn check

An offline detector finds commitments in an agent's final message (`src/statekeeper/commitments.py`, `tools/commitment_replay.py`); open commitments can be checked against artifacts (`closure_tied.py`).
Replay over stored final messages: hand-judged precision of "this is a commitment" (target 85%) and recall of the "unfulfilled" call-out. Surface first as a one-line note in the next turn's block; block at turn end only after precision is shown.

## Slice 5: free-text extraction

The extractor retries once on invalid JSON, skips the agent's own status lines and ignores "likely replaces" hints within one source. Re-run `tools/extract_sample.py` on a seeded sample before any scheduled run
and compare the rejected share of facts. Auto-applying the person's own decision messages stays off until a sample shows at most 1 in 10 rejected.

## Slice 6: demote raw fragments

Once state-first recall is live and measured, stop injecting fragments when a state block exists (keep search and show on demand). The gate: on the replay set, did any turn lose something it needed that the fragments used to give?

## Open questions, with leans

1. Enable slice 1 live? Lean: yes, shadow first (see E), since it changes injected context.
2. Is a scope tree (parent links) wanted now? Lean: after slice 3 shows its value.
3. Who judges replays? One judge limits what the numbers are worth; a second person judging 20 turns once doubles it.
4. Erasure and retention for state derived from sensitive inputs, before any persistent live rollout. Lean: design it before slice 3 goes live.
