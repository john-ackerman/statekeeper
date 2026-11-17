# A. Problem definition: a current-state layer for agents

Revision 3, 2026-11-16. This document says what must be true, not how it is built. Any measured numbers in the documents are indicative, from small samples judged by one person. "The owner" means the person who runs the agent and has the final say over what it treats as settled.

## 1. Goal

1. **When an agent is shown an input, it also sees the current, reliable picture of what it is aware of that the input bears on**: beliefs, how work stands,
   commitments, understanding of systems and people.
2. **The picture stays right as things change**, shows when a thing changed and why, and says how much to trust it right now.
3. **It is built without the agent curating it**: every input is an input to it.
4. **What the agent says is checked against it before it reaches the person** (an open commitment unfulfilled at the end of a turn is called back).
5. **Raw history is evidence, not recall.** Transcripts, fragments, logs stay stored and searchable on demand; they are never injected because they merely sound similar.
6. **One shared picture across all sessions and agents**, with provenance, so an agent's claim is never mistaken for a verified fact.
7. **Equally practical and with the same potential as a memory of the agent itself**, stated as state tracking: what the agent knows, how it works, who it has dealt with, what it owes, kept current and checkable.

## 2. What "state" is, and the kinds of it

State is the current value of something a mind tracks, with when it changed, when it was last verified, where it came from, and how sure to be.
Concepts (names) are not state; they are the keys. The model's weights already know what a datepicker is, not that this repo's was rejected in Admin on 2026-11-16.

**There is no separate machinery for the agent itself or for other people.** What a memory system would file as "identity" or "relationships" is ordinary state with the same kinds, rules and
fields as everything else: state about the *agent* (ways it has chosen to work, to think, to decide; its standing rules; what it has learned about its own habits) and state about *other people* it has
interacted with (how they like to work, what they own, what was agreed). The agent and each person are things in the scope tree like a ticket or a repo. The owner-confirmed core is just the most stable end of
the same spectrum (it changes only by the owner's say), not a different mechanism.

Kinds differ in how they change and how they go stale. A single "age" rule is wrong for all of them.

| Kind | Examples | Changes | Goes stale by | Persisted? |
|---|---|---|
| **Core, owner-confirmed beliefs** | who the owner is, standing preferences and rules, agreed priorities | only by the owner's say | never by age; only an owner correction | yes, protected |
| **Understanding of an artifact** | how a module behaves, what a system does, where a thing lives | when the artifact changes | the artifact moving: for code, commits since the verified commit on the paths the belief covers | yes, with a verify handle |
| **In-flight work and commitments** | "PR #3337 awaits CI", "I owe the design by Friday", open/fulfilled/abandoned | by events and by the agent's own acts | the next event; commitments by deadline or by the turn ending | yes |
| **Relationships and preferences** | how a person likes to be addressed, who owns what | slowly, by being told | contradiction; otherwise long-lived | yes |
| **Mechanical / ephemeral** | the tail of stdout/stderr, still-running processes, ports, context size | every moment | immediately | **no: injected by hook from a live read, never stored** |

Two timestamps, never one: **last changed** (the value became what it is) and **last verified** (someone or something confirmed it still holds).
Staleness is then a function of **volatility** (how fast this kind of thing changes), **verified-age**, and whether the underlying artifact has moved,
not of the age of the record. A belief last changed in March and verified yesterday is fresh; one verified in March about a hot file is not.

## 3. Invalidation: watch the artifact, decay only as a fallback

Where the thing a belief is about can be watched, watch it: for code, the git history since the verified commit on the paths the belief covers; for a PR or CI, a
live read; for a ticket, its tracker. A change in the watched artifact marks the belief *unverified* (not wrong) until re-verified or superseded. Time-based decay
applies only where nothing can be watched, with a per-kind window. A decayed or unverified state is shown as such, never silently as current.

## 4. Confidence and hedging are derived, not asserted

How sure the layer sounds comes from: verified-age, the state's volatility, **how it was learned** (read from the artifact; inferred; told by the owner; claimed by an
agent), whether **other parties are changing the thing**, and whether anything contradicts it. The surfaced line carries the derived label ("verified 2h ago",
"agent-claimed, unverified", "owner-stated"), so the consuming agent can hedge without being told to.

## 5. Shared across all sessions and agents: one store, all of them equals

Every session and every agent works on **one central store as an equal** ("all sessions are the same agent"). There is no per-session memory, and handoffs should not be needed (a handoff remains an allowed option; whether it is still useful is decided from measured outcomes, section 15): when an agent finishes
researching an area, its findings simply *are* the current state of that area, and when the owner later asks any session about it, the state is fetched automatically with the question.
Reports remain only as human-facing artifacts (a commit, a Notion page); the store is the working truth. That needs:
- **Writers and provenance** on every state: who or what wrote it (a rule, an agent, the owner), from which source event.
- **Trust levels**: owner-stated > verified by a rule or a live read > derived from an artifact > an agent's claim. **An agent's "green" is a claim, not a verified fact**: it is stored as
  a claim with its source and shown as one until a read confirms it.
- **Conflicts**: two writers disagreeing about one state keep both and surface the disagreement. Default resolution: the latest *verified* observation wins; otherwise higher trust, then recency. Nothing is silently overwritten. (Open questions with leans are in B section 10.)
- No agent can lower a state's trust level by rewriting it; supersession is an event with an author.

## 6. The primary write path is the agent's own work

Most new knowledge arrives as the result of work: an investigation, a review, a build. Because all sessions share one store (section 5), an agent **writes its findings as state claims with sources as it learns them**
(thing, statement, evidence, confidence, how learned), not as a report for another session to read later. A prose report, if any, is for the human and is derived from the state, not the carrier of it. Free-text extraction by a model is the fallback for inputs that carry no structure, not the main path.

## 7. System-of-record principle

Linear, GitHub, the board, git and the like own most facts. The layer holds **beliefs about them plus a verify handle** (where to re-read it: a command, a URL, a query), not a mirror
of them. A mirror drifts the moment the source moves; a belief with a handle can be re-verified, and a changed source invalidates it (section 3).

## 8. Owner-confirmed core, erasure and correction

- A small core of beliefs is confirmed by the owner and protected from automatic change.
- Anything derived from an input must be **erasable with its source**: deleting a source removes or demotes the states derived only from it, and the event log records that it happened (without the content).
- **Correction**: The owner can say "that is wrong"; the state is superseded with the correction as source, the wrong one is kept, labelled rejected, so it is not re-learned.

## 9. Scope tree, token budget, compression

Things nest (a tool run under a ticket under a project under a priority); an update to a leaf is visible to the scopes above it. At recall, the budget is spent
leaf first: at most a few states, each with its freshness label; **ancestors are summarised up the tree** (counts and the changed child, computed at read time from the children
so no second copy can go stale). The total injected is capped (target 2.5k characters) and says what it omitted.

## 10. What must be true for recall to be useful

- It surfaces the state the input bears on, **including state the input does not name** ("our cores got upped to 300" must find the core-quota decision), and nothing else.
- The newest value of a state is shown, marked where it replaces an older one the model may also remember.
- It costs the turn little and cannot fail it.
- It never injects raw fragments that merely match. It is honest about uncertainty (section 4).

## 11. Non-goals

Re-implementing the model's world knowledge; a knowledge graph for its own sake; human review of every fact (review is the exception path); replacing the evidence store; mirroring systems of record.

## 12. Failure modes to design against

1. **Retrieval that finds names, not state**: a relevant state is missed because only accepted facts are searchable while most extracted facts stay proposals.
2. **Surface-word matches**: a truncation or prefix of a name ("claud" in an email address) reaching an unrelated thing; generic hub entries surfacing on unrelated turns.
3. **History dominating**: stored raw fragments filling the recall slots.
4. **Pollution**: the agent's own status lines ("the builder is still finishing") extracted as facts.
5. **Stale above fresh**: an older accepted belief shown above the proposal that replaces it.
6. **Dropped commitments**: "I'll build it next" and the turn ends.
7. **Extraction cost and fragility**: seconds per message by a headless model call; invalid JSON occasionally stops a batch.
8. **No verification trail**: nothing records when a belief was last checked, so age is the only staleness signal.

## 13. Measurable success criteria (replay of stored turns, plus live counters)

| Measure | How | Target |
|---|---|---|
| Needed-state hit rate | share of turns where a needed state is surfaced; hand-judged on 30 to 50 replayed turns | at least 85%, held at higher precision |
| Surfaced precision | relevant / surfaced entries | at least 80%, at most 3 entries a turn |
| Wrong-surface rate | wrong entries per turn | at most 0.2 |
| Stale-shown rate | surfaced states that a newer or invalidating event supersedes, unmarked | 0 |
| Unverified-labelled rate | states shown with a verified-age or "unverified" label where required | 100% |
| Agent-claim-as-fact rate | claims shown without a claim label | 0 |
| Latency per turn | p95, warm | under 0.5 s end to end |
| Injected size | characters from the state side | at most 2.5k state, 0 raw fragments |
| Free-text extraction | seconds per input, off the hot path | structured inputs 0 s |
| Dropped commitments | commitments open at turn end that the Stop pass missed, on a replay of 50 turns | 0 |

## 14. Known limits (not verified)

- Relevance was judged by one person on a small sample; a second judge would make the numbers worth more.
- Whether agents behave better with surfaced state than with surfaced fragments.
- Claude Code hook payloads for tool events (PostToolUse) as an input source: not tested for each tool's payload.
- Git-history invalidation (section 3) and structured agent claims (section 6) are designed from the requirements, not yet measured in use.

## 15. The store learns from reality (added 2026-11-16)

The store must improve automatically from what actually happens, not from a person judging test series.

- **Outcomes from day one.** For every state that is surfaced, log what happened to it: surfaced, omitted (dropped by a cap or budget), used, contradicted, verified_right, verified_wrong, missed (a state changed or was needed and did not surface). A handoff is just another artifact: it can be scored the same way (source `handoff:<ref>`), so whether handoffs are still useful is measured, not assumed.
- **Ground truth is outside the system**: git, PR and CI reads, artifact checks through verify handles, the agent's later behaviour. The store never grades itself; a signal that only the store produced does not count.
- **A fixed held-out set of turns judged by the owner** is a regression benchmark only. It is never used to tune and never grows from tuning runs.
- **Learn parameters, not weights, first**: extractor thresholds, the relevance floor, per-writer trust ceilings, per-kind volatility half-lives. Every change is a row (who, why, previous value), small, and reversible with one call.
- **Measure before automating.** Feedback is sparse and delayed: a suggestion needs a minimum amount of evidence (`learn.py` thresholds), is reported first, applied by a person, and automated only once the same suggestion has proved right repeatedly.
- **Staged option for a small local learned component** is in B section 11.
