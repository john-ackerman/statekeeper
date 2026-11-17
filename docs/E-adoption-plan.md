# E. Adoption plan: running statekeeper alongside an existing recall system, then replacing its per-prompt injection

This is a plan with a log-only first stage already built; the legacy system is not modified, disabled or deleted by any step below until the owner says go for that step.
"Legacy" means whatever recall system the agent has today.

## 0. Principles

- The legacy system keeps running exactly as installed until parity is shown and the owner approves.
- Every step is reversible; the rollback is a documented command set, tested before it is needed.
- Nothing edits agent settings or installs a hook except tools the owner runs on purpose (`tools/install_shadow_hooks.py`, which merges, backs up and supports `--dry-run` and `--remove`).

## 1. Claude Code adapters (thin; the store knows nothing about Claude Code)

Small scripts under `adapters/claude_code/` that call the Store interface only.

| Event | Legacy typically does | statekeeper adapter |
|---|---|---|
| SessionStart | loads standing core and a working note | the session's standing state: owner-confirmed core states about the agent and people (kind rule/belief), plus the working note as a state, read from the store |
| SessionStart:compact | re-injects core, note and the conversation tail | the same block plus open commitments and the newest state of the tasks in scope |
| UserPromptSubmit | recall of related fragments and concepts | `shadow_hook.py` (log only) or `inject_hook.py` (live, off by default): `surface()` for the prompt, with a turn reference so outcomes accrue |
| Stop | extraction and observation | writes the turn's claims through `submit_claim`; labels used/missed on the output; offline commitment detection (record only) |
| watchers | deterministic observations | the deterministic writers (PR, CI, agent status) |

## 2. Shadow mode (before any cut-over)

1. Run the shadow adapter: it receives the same prompts and outputs, logs what statekeeper would surface, and injects nothing. It reads what the legacy system actually injected back from the transcript, after the fact.
2. Imports from the legacy store run one way into statekeeper (`tools/import_legacy.py`, or the nightly job when `legacy_export_command` is configured), so the two stay comparable. Nothing is written back.
3. The outcome log (A section 15) scores both: for the same turns, which surfaced state was used, which was missed, which was verified wrong.
4. Shadow runs until the parity checks hold over at least two weeks of real use.

## 3. Parity and acceptance checks (all measured)

- Held-out turns: owner-judged precision and needed-state hit rate at least equal to the legacy system's on the same turns (`tools/parity_sheet.py`, `tools/parity_report.py`).
- Session start: every standing core item has a state, checked by listing, within the same size budget.
- Compaction: after a forced compact in a test session the agent can state its working note and open commitments.
- Latency: surface p95 well under the hook budget.
- Failure modes: embedder down degrades to names only (tested); a locked or absent store never fails a turn (tested).
- The outcome log shows no rise in verified_wrong or missed relative to the legacy system over the shadow period.

## 4. Verified backup and rollback

- Before any cut-over step: back up the statekeeper database with `tools/backup_db.py` (SQLite online backup, then row counts per table and `integrity_check` compared against the source) and keep a copy of the legacy store.
- Rollback restores the original settings file (the installer makes a timestamped backup) and leaves the legacy store untouched (statekeeper never writes to it). The cut-over only changes which hooks run, so rollback does not depend on restoring data.
- A rollback drill (apply, roll back, run the legacy system's own smoke test) is performed once on a scratch profile before the real cut-over.

## 5. Cut-over sequence (each step needs the owner's go)

1. Shadow mode on. 2. Parity report and review of the held-out judgments (F is the bar). 3. Switch UserPromptSubmit only (statekeeper injects, legacy per-prompt recall off) for a week, everything else unchanged. 4. Switch SessionStart and compaction. 5. Switch Stop. 6. Leave the legacy system installed but idle for a further period; removal is a separate decision.

## 6. Not decided

- Where the single shared store lives when more than one machine is involved (today: one machine, one WAL file).

## 7. Shadow mode: what is installed, how to turn it off, how to roll back

**What it is.** `adapters/claude_code/shadow_hook.py` is a single log-only hook for UserPromptSubmit, Stop and SessionStart. It never prints to stdout (so it never injects), always exits 0, is registered `async`,
bounds its embedding call to one second, and records into a store of its own (`~/.claude/statekeeper/shadow.db`, log `shadow.log.jsonl`, both outside the repository). It records surfaced and omitted outcomes per prompt,
labels used and missed from the agent's answer at Stop, counts commitments, and keeps turn text locally for the offline miss detectors.

**Install (merge, never replace).** `python tools/install_shadow_hooks.py` backs up `~/.claude/settings.json` to a timestamped file, appends one group per event, refuses to write unless every pre-existing hook is unchanged and in order and the JSON re-parses, and prints the backup path.

**Turn off.** `STATEKEEPER_SHADOW=0` (shell or settings `env`) makes every adapter call a no-op. **Remove:** `python tools/install_shadow_hooks.py --remove` deletes only groups whose command mentions `shadow_hook.py`. **Roll back:** copy the timestamped backup over `settings.json`.

**Nightly.** `tools/nightly.py` runs a verified backup (newest 14 kept), the live writers, an optional incremental re-import from the legacy store (`legacy_export_command` in the configuration, `src/statekeeper/config.py`),
a consolidation pass, miss detection and an outcome report under `~/.claude/statekeeper/reports/`. Schedule it with whatever scheduler you use; to undo a night, copy the newest backup over `shadow.db`.
