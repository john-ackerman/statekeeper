# Small-model updater: design note (not built)

John's idea: given a new fragment (a message, a note, a transcript turn), a Haiku-class model finds the concepts it affects and proposes
**add / confirm / supersede** against the store. This note fixes the proposal format now, so the rule-based proposer (`sk dupes`,
`statekeeper/canonical.py`) and a later model emit the same thing and one review path handles both.

## Proposal format (one JSON object per line)

```json
{"op": "supersede", "relation": "restates", "keep": 179, "retire": [2958],
 "evidence": {"2958": {"shared_sources": ["persistence:700"], "cosine": 0.858, "jaccard": 0.54, "containment": 0.82,
                       "contradicts": false, "numbers_agree": true, "same_thing": false}},
 "why_keep": "closer to the source than #2958 (#2958 was rewritten from another state)",
 "confidence": "high", "texts": {"179": "[John Ackerman] ...", "2958": "[data script validation] ..."}, "proposer": "rule:canonical"}
```

| field | meaning |
|---|---|
| `op` | `add` (new state: also `thing`, `text`, `kind`, `volatility`, `evidence_ref`), `confirm` (`state` id, `evidence_ref`: the fragment says it again), `supersede` (`keep` + `retire`) |
| `relation` | for supersede: `restates` (same fact, other wording: retire loses nothing) or `updates` (same subject, newer value: retire is history) or `contradicts` |
| `evidence` | per retired id, the checkable numbers. A model proposer adds `"quote"`: the fragment words that justify it, verbatim |
| `confidence` | `high` / `medium` / `low`; the apply step only ever auto-applies `add` and `confirm`, and `supersede` only after review |
| `proposer` | `rule:canonical`, `model:claude-haiku-5-5`, ... so precision can be measured per proposer |

Applying a reviewed supersede today: `sk retire <id> --into <keep>` (closes the old state as replaced, kept as history; reversible with
`store.reopen`). `sk dupes --json` emits this format; `sk dupes` prints it for a person.

## The model step (to build next)

1. Fragment in (already stored as evidence by the importer/extractor).
2. Candidates, cheaply and without the model: `surface(...)` over the fragment with the recall floor (the same selection the hook uses) plus
   `canonical.Index.mates` of each hit: about 10-20 states, each `#id [thing] text`, plus the names of the 20 biggest topics.
3. One Haiku call: "for each fact in the fragment: is it already stated (confirm #id), a new wording of #id (restates), a newer value of #id
   (updates), contradicting #id, or new (add, with thing)? Quote the fragment words for each." Structured output, `strict` schema.
4. Deterministic checks before anything is written: every `#id` must be in the candidate list; a `restates` must also pass
   `canonical.Index.restates` (or is downgraded to `medium`); an `updates` needs the fragment to be newer than the retired state.
5. `add`/`confirm` are written (they only add evidence or a state); `supersede` goes to the review list.

## Cost per fragment (estimate, not measured)

| part | tokens |
|---|---|
| instructions + schema | ~1,200 in |
| fragment | ~300-800 in |
| candidates (15 states x ~50) + topic names | ~950 in |
| proposals out (3-8 items x ~60) | ~200-500 out |
| **total** | **~2,500-3,000 in, ~200-500 out** |

Prices from the claude-api skill's model table (cached 2026-11-03), **not verified against the live pricing page**:

| model | in $/MTok | out $/MTok | per fragment (3,000 in / 500 out) | per 150 fragments/day |
|---|---|---|---|---|
| `claude-haiku-5-5` (prompts <= 100K tokens) | 0.10 | 0.50 | ~$0.00055 | ~$0.08 |
| `claude-haiku-4-5` | 1.00 | 5.00 | ~$0.0055 | ~$0.83 |

- Message Batches halve both (the nightly job could batch): ~$0.0003 per fragment on Haiku 5.5. The 50% batch discount is from the skill text,
  unverified for Haiku 5.5 specifically.
- Prompt caching likely does not help: the shared prefix (~1,200 tokens) is probably under Haiku's minimum cacheable prefix (model-dependent,
  512-4,096 tokens per the skill; the Haiku 5.5 value and its cache read/write prices are unverified).
- Today `extract.call_haiku` runs `claude -p` headless (`MODEL = "claude-haiku-5-5"`), which bills the subscription, not the API; the dollar
  figures are what the same calls would cost on the API.
- The 150 fragments/day volume is a guess (unverified); measure with the importer's own counts before relying on it.

## Why the rule proposer first

The rules (`restates`: shared source or very close meaning, IDF word overlap, no contradiction, same numbers, no substituted identifier) catch
the dominant duplicate shape for free: the Persistence import extracted a fact and the decompose job later rewrote the same fragment. On the
2026-11-20 snapshot they find ~300 high/medium clusters; a sampled read of 26 found one weak pair (a lesson and the incident it cites), now
`medium`. What rules cannot see (a decision restated in other words weeks later, "the move off Persistence" meaning the cutover) is where the
model step earns its cost.
