# B. Design notes: shared store, learning, consolidation, misses

Read A first. These sections are design notes with leans, not a finished spec.

## 10. Shared store: identity as state, provenance, conflicts (open design questions, with leans)

Decisions taken: one central store; every session and agent is an equal writer; the agent itself and other people are ordinary things whose state follows the same rules (A section 2); findings live in the shared store, so handoff reports should not be needed; a handoff stays an allowed option and is scored by the outcome log (A section 15).

1. **Provenance of each state.** Each state records `writer` (a rule name, an agent, the owner), `session_id`, `learned_how` (read from an artifact, inferred, told by the owner, claimed by an agent), the evidence references, and a verify handle.
   Lean: required columns, not free text; a state without a writer cannot be stored. Session id is the harness's, never the agent's own claim.
2. **Conflict between sessions** (two sessions assert different values for the same thing and key). Lean: keep both rows, mark the pair `disputed`, surface "A says X (session s1, verified 2h ago), B says Y (session s2, claimed)" in the state block; default winner = the latest *verified* observation, else
   the higher trust level, else the later write; the loser stays as history. A verified read (a rule or a live check) always breaks the tie; two agent claims never settle each other.
3. **Concurrent writes.** Lean: one SQLite file in WAL mode with the store as the only writer API (short transactions, busy timeout), so no coordination protocol is needed on one machine; revisit only if state must be shared across machines.
4. **Who can change core / owner-confirmed state.** Lean: only the owner or an explicit owner-confirmed write; an agent proposing a change creates a claim that disputes it. No agent can lower another state's trust.
5. **The agent's own state (ways it works, thinks, decides).** Lean: ordinary states on a thing for the agent, `Kind = rule` for standing ways of working, surfaced in the small standing section of every turn that touches their scope; the agent writes them when the owner states them or when it records a decision about its own conduct; the owner can correct them.
6. **State about other people.** Lean: things with kind `person`; states are learned-how `told` or `observed`; never surfaced outside the people it concerns without a reason; erasure (A section 8) applies per person on request.
7. **"The state of an area" as the answer to a question.** Lean: retrieve by scope walk (the area's thing and its children) plus meaning, ordered by last-changed, with each state's freshness label; the asking session never sees who wrote it unless it asks.
8. **Structured claim format for agents.** Lean: a small JSON line per claim `{thing, statement, key?, evidence[], learned_how, confidence?, verify?}` written through the store API (not a file the agent edits), so a claim without evidence is rejected at the boundary.
9. **Which session wrote what when the harness reuses ids.** Lean: store the harness session id plus the writer name; treat both as provenance strings, not identity.
10. **Where the central store lives** if the owner works from more than one machine. Lean: one machine, one file, for now; the Store interface (`src/statekeeper/store.py`) is what a later remote store implements.


## 11. Learning from outcomes: log, parameters, and a staged local model (2026-11-16)

**Built now (stage 0 base).** `outcomes` (append-only; kinds surfaced, omitted, used, contradicted, verified_right, verified_wrong, missed; each row keeps the parameters in force) and `param_history` (append-only; `set_param`, `get_param`, `revert_param`). `surface(..., log_turn=ref)` logs surfaced and omitted. `outcomes.label_turn` turns the agent's later output into used (surfaced) and missed (omitted) by thing-name plus distinctive-word overlap. Deterministic writers log verified_right / verified_wrong when an outside read agrees or disagrees with a standing state. `learn.summarize` and `learn.suggest` read the log and return suggestions; they apply nothing. `tools/outcome_report.py` prints the report.

**Staging.** Each stage starts only when the previous one is measured and shows headroom.

| Stage | What | Data it needs | What the log will realistically hold | CPU per call |
|---|---|---|---|---|
| 0 | Parameters only: relevance floor, extractor reject thresholds, per-writer trust ceiling, per-kind half-life, set by rules over counts (`learn.suggest`) | tens of labelled outcomes per parameter (20 verified per writer, 10 observed changes per volatility, 40 labelled surfacings) | Replay data alone gives surfaced and omitted only (94 and 19 on 30 turns) and nothing labelled. Verified outcomes accrue only as writers (PR/CI first) run, so trust and half-life suggestions are weeks away; the floor needs the used/missed proxy over live turns | negligible (counts) |
| 1 | A small local model over features we already have: logistic regression or a small gradient-boosted tree for "is this extracted fact worth storing", "will this state be needed", "should this surface". Features: embedding cosine, match reason, trust, kind, age, writer, evidence count, times surfaced before, words of overlap with the input. No text generation | roughly 500 to 2,000 labelled examples per question, with both classes (a few hundred positives at least) | realistic after months of live use with the proxies: surfaced/used labels grow by tens per day of use; "worth storing" labels need the owner's occasional accept/reject or the used rate of stored facts later, which is slow. Expect stage 1 to be data-starved until then, so train only on the questions that have both classes | microseconds to low milliseconds on CPU, no network, no per-turn Haiku call |
| 2 | Anything neural-adapter-like (a small trained head or adapter over embeddings, a fine-tuned reranker) | tens of thousands of labelled pairs, plus a held-out set large enough to see a gain over stage 1 | not reachable from one user's log in the near term; consider only if stage 1 shows clear headroom (a measurable gap between its ranking and used/missed outcomes) | tens of milliseconds for a tiny head on CPU; a fine-tuned reranker is more and needs justification |

**Why not a model call per turn:** a Haiku call per surfaced candidate would roughly double context cost and add seconds of latency; stages 0 and 1 are local and cheap by construction. A model call stays for extraction only (already the case), off the per-turn path.

**Guardrails.** A learned score can only reorder or filter what retrieval already found; it cannot write states, change trust above a writer's ceiling, or touch core state. Every learned parameter or model version is a row with a revert; the held-out set of turns judged by the owner (A section 15) is the regression gate before any change is kept.


## 12. Consolidation as an ongoing, validated process (2026-11-16)

The store must not fill with the same fact many times, and it must not lose distinct facts to over-eager merging. So consolidation is a recurring pass, not a one-off clean-up.

**What a pass does** (`consolidate.run_pass`, built and tested, not scheduled): (1) embed anything new; (2) same-state detection: proposed extracted/claimed states whose meaning matches another standing state at or above `dedupe_threshold` (default 0.95; at 0.90 distinct facts merged, measured); (3) merge: the newer duplicate is rejected with "duplicate of #N" (history kept), its evidence is copied to the survivor, a `same` link is recorded; (4) linking: pairs between `related_threshold` (0.85) and the duplicate threshold become `related` links, which retrieval can later use as "see also". Each pass is capped (50 merges, 200 links), idempotent, and never touches owner/verified/derived states.

**Reversible.** `Store.reopen(state, reason)` undoes a merge; a reopening is an event and is counted by validation. The two thresholds live in `param_history`, so changing one is a logged, revertible row.

**Validation from outcomes, not opinion** (`consolidate.validate`, `learn.suggest`): per merge, did a person reopen it; did a miss get logged against the survivor (a restated input matched it but it did not surface, A section 15); did the merged state's evidence still support the survivor. A high reopened fraction (with at least 20 merges) suggests raising `dedupe_threshold`; many merges with no reopenings and no misses suggest the threshold is safe but never lower automatically. Owner-judged merge pairs held out for the purpose (the 33 merges at 0.95 are the first sample) stay the regression gate.

**Cadence when wired:** after extraction runs and nightly; a pass that would merge more than 50 states stops and reports instead, since a sudden spike means the threshold or an extractor changed.

## 13. Automated detection of misses (2026-11-16)

Signals that the store failed the user, found without anyone judging, logged as `missed` outcomes (`misses.log_misses`): the user restates something within a day; a restated input matches no stored state (an extraction miss); it matches a stored state that did not surface (a surfacing miss); a state changed after its thing last surfaced and was then only omitted. Live turn text for this comes from the shadow adapters (`turn_texts`). Measured on one user's 371 past messages: at cosine 0.88 the restatement detector over-fires on topical similarity of long dictated messages (31 hits, about a quarter genuine); at 0.93 it finds 3, all genuine repeats. Expect low recall until live data accrue; more signals planned: the user re-asking a question the agent already answered, and the user correcting the agent ("no, I meant...") as `contradicted`.

## 14. Every injected state must bear on the input; stale states are closed (2026-11-18)

A judged comparison on 23 real turns found three defects in the thing-first surfacing: (1) states that were no longer true kept being injected, because nothing short of a same-thing replacement retired them; (2) naming a generic thing (a repo, a tool) pulled in its newest states whatever the prompt asked; (3) an older state about a subject was shown beside a newer one on another thing. Changes:

- **Closing.** `Store.close_state(id, reason, replaced_by=None)` retires a standing state that is no longer true. With `replaced_by` the replacement may be about another thing (a fact often moves, e.g. from a project to the person who changed it); without one the state points at itself (`superseded_by = id`, read as "closed"). Every standing-state query already excludes both; the reason and an event are kept; `reopen` undoes it. `tools/curate.py` is the command line for find / show / close / reopen / add.
- **State-first selection** (`retrieve.select`). A state qualifies only if it bears on the input: a keyed current value (a PR's state, CI, review) of a thing named exactly; any state of a *specific* named thing (at most 6 standing states as subject or object, nothing filed under it); a state close in meaning (cosine at or above the floor); a generic named thing's state that is nearly that close *and* shares a rare word with the input; or a state sharing two (three) rare words that is within 0.08 (0.12) of the floor. Rare words are at least 5 letters or carry a digit and occur in at most 1% of standing states. A near spelling counts as naming only when within one edit; labels with digits are never matched fuzzily (3345 is not a typo of 3351). A pasted GitHub PR link names "<repo> PR <n>". Agents and people are never surfaced because their name was said, but what was learned about them surfaces by meaning (their rule/stable states are in the session core and are not repeated).
- **Newest per subject.** Of two qualifying states about the same subject (cosine between them at or above 0.80, or one likely replacing the other on the same thing) only the newer is shown.
- **Shorter lines.** One trust marker and one short evidence pointer per state (`[unreviewed · msg 618]`); a thing's keyed values share one line with the oldest verification as the marker and one re-check command.
- **Agents.** When the input asks how agents are doing ("agents" near a status word), one line summarises the agent.status states that changed in the last six hours, followed by any standing rules about agents.
- **Observer.** PRs the store still holds as open that have left the open-PR list are read once more, so a merged PR stops reading "open".
