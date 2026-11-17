# F. The bar: when statekeeper's injection replaces the legacy system's per-prompt injection

Scope of the swap: ONLY the per-prompt automatic injection. The legacy system stays installed as the searchable history store, keeps its session-start core and working note and any watcher output until those are separately replaced.
Shadow logging keeps running after the flip, so the comparison and the outcome log do not stop.

## The bar (three conditions, each concrete)

**(a) What is injected is current, and says how current.** Every injected state carries its freshness: a live state (build, PR CI, agent status) shows "verified Nm/Nh ago" and, past 6 hours without re-verification,
"STALE, last verified Nh ago; re-check: <the exact command>" instead of being stated as current; proposals are labelled proposed; agent claims "agent-claimed, unverified". Superseded and rejected states are never injected.
Built and tested (`retrieve._freshness`, tests `Currency`). Still to see in real use: a stale label catching something genuinely stale (it will appear in the logs).

**(b) Judged more useful than the legacy injection on real turns.** The owner's call, on the sheet written by `tools/parity_sheet.py` (real turns side by side, one click each, plus a "stale or wrong" mark).
Suggested pre-registered bar: statekeeper rated more useful or about the same on at least 70% of turns, rated stale/wrong on none, and never rated worse on a turn where the legacy injection was rated useful.
Measured, not judged: injection size (tokens) and overlap of the things surfaced (`tools/parity_report.py`). Different systems often find different things, so the sheet is the only way to know which is right.

**(c) Quiet when nothing relevant exists.** statekeeper prints nothing when no thing is named or close in meaning. Settings that matter: the meaning floor, at most 2 things, a character cap, never the agent or people
(they belong to the session-start core), never the working note, and never a state already shown in the last 3 turns of that session. Measure the share of prompts left quiet against the legacy system's, and judge on the sheet whether the quiet ones were rightly quiet.

## What is built for the flip

- `adapters/claude_code/inject_hook.py`: synchronous UserPromptSubmit hook, inert unless `STATEKEEPER_INJECT=1` (default off). Prints additionalContext prefixed `[statekeeper]`, never fails a turn, logs what the shadow adapter logs. When on, the shadow adapter stops surfacing (no double counting).
- `python tools/install_shadow_hooks.py --inject` registers it (merge only, timestamped backup, validated). Registering changes nothing until the flag is set.
- Rollback, one line: `STATEKEEPER_INJECT=0`. Full removal: `python tools/install_shadow_hooks.py --remove`, or restore the timestamped settings backup.

## The legacy side of the flip (specific to your legacy system; verify)

To avoid injecting both, the legacy per-prompt recall must go off. How depends on that system: a setting, an environment variable read by its hook or by its server process, or removing its hook entry. Verify on the first flip:
turn it off, send a prompt, and check the transcript to confirm only one injection arrives. Reverse by restoring the setting.

## Still missing after the swap (known, not blocking)

Session-start core and working-note parity (`session.py` builds a block that is logged in shadow but not injected); watcher parity; compaction survival read from the store; a way to inject a state by id; the learning loop acting on outcomes (parameters are suggested, never applied, until outcomes accrue); a second machine.
