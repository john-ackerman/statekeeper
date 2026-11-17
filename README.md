# statekeeper

**Status: experimental.** A shared, current-state layer ("state memory") for coding agents: what they know, how work stands, what they have committed to, and who they deal with, kept current and checkable. It is a research prototype, not a stable library; interfaces will change.

## The idea

1. When an agent is shown an input, it also sees the current, reliable picture of what it is aware of that the input bears on.
2. That picture stays right as things change, shows when and why, and says how much to trust it right now.
3. It is built without the agent curating it: every input updates it; agents write what they learn as they learn it.
4. What the agent says can be checked against it (an open commitment is not silently dropped).
5. Raw history is evidence, not recall: stored and searchable on demand, never injected because it merely sounds similar.
6. One shared picture across sessions and agents, with provenance, so an agent's claim is never mistaken for a verified fact.
7. The agent itself and other people are ordinary state, not special machinery.

Problem statement: `docs/A-problem-definition.md`. Design notes: `docs/B-design.md`. Adopting it next to an existing recall system: `docs/C-incremental-adoption.md`, `docs/E-adoption-plan.md`, `docs/F-adoption-bar.md`.

## Layout

```
src/statekeeper/   store (SQLite), schema, claims, retrieval (names + meaning), embeddings client,
                   extractor, commitments, consolidation/dedupe, miss detection, outcome report, writers/
adapters/claude_code/  thin Claude Code hook adapters (they call only the Store interface; nothing installs them by itself)
tools/             command-line helpers (smoke test, reports, replay and miss/extraction sampling, legacy importer, optional hook installer, observers)
tests/             unittest suite, standard library only
docs/              A problem definition, B design notes, C/E/F adopting statekeeper alongside an existing recall system
local/             git-ignored: your own adapters and notes for your install (never committed)
```

## Running it

Python 3.11+, standard library only. An embedding server is optional: any OpenAI-compatible `/v1/embeddings` endpoint (for example llama.cpp `llama-server --embedding` with a small model such as bge-small). Without one, retrieval uses names only.

```
python -m unittest discover -s tests     # unit tests, no network needed
python tools/smoke.py                    # end-to-end check on a temporary database
```

Environment: `STATEKEEPER_DB` (default `out/statekeeper.db`), `STATEKEEPER_EMBED_URL` (default `http://127.0.0.1:8081/v1`), `STATEKEEPER_EMBED_MODEL` (default `bge-small`). On Windows set `PYTHONUTF8=1`.

Some live writers (`src/statekeeper/writers/`) read GitHub through `gh` and Azure DevOps through `az` (read only). They are configured with an optional JSON file (repositories, ADO organization and project, an export command for an earlier fact store); see `src/statekeeper/config.py` for the search order and keys. `STATEKEEPER_ADO_ORG` / `STATEKEEPER_ADO_PROJECT` override it. The free-text extractor calls `claude -p` headlessly. Neither is needed for the tests.

Nothing in this repository edits Claude Code settings unless you run `tools/install_shadow_hooks.py` yourself (it merges, supports `--dry-run` and `--remove`).

## Notes

- Databases and `out/` hold message text and are git-ignored; do not commit or share them.
- Numbers quoted in the docs come from small samples judged by one person; treat them as indicative.

## License

MIT, see `LICENSE`.
