-- statekeeper schema, migration 001.
--
-- Vocabulary: a THING is anything whose state is tracked (a ticket, a repo, a person, the agent itself); a STATE is the current value of
-- something about a thing, with when it changed, when it was last verified, who wrote it, how it was learned and how far to trust it;
-- EVIDENCE is what a state cites. There is no separate machinery for the agent itself or for people: they are things.
--
-- Rules the database enforces, so no writer has to be trusted with them:
--   * a state cannot leave 'pending' without at least one piece of evidence, and its last evidence cannot be removed while it stands;
--   * states are inserted pending and stand only once their evidence is written;
--   * every change is also one row in the append-only event log, written by triggers in the same transaction.

CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER PRIMARY KEY,
    applied_utc TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS things (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    kind TEXT NOT NULL DEFAULT 'thing',          -- thing | person | agent | value
    kind_confirmed INTEGER NOT NULL DEFAULT 0,   -- 1 owner/agent confirmed, 0 said when made, -1 nobody said, -2 retired (merged away)
    parent_id INTEGER NULL REFERENCES things(id),
    summary TEXT NULL,
    created_utc TEXT NOT NULL,
    CHECK (kind IN ('thing', 'person', 'agent', 'value'))
);
CREATE UNIQUE INDEX IF NOT EXISTS uidx_things_name ON things(name COLLATE NOCASE);
CREATE INDEX IF NOT EXISTS idx_things_parent ON things(parent_id) WHERE parent_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS aliases (
    thing_id INTEGER NOT NULL REFERENCES things(id),
    alias TEXT NOT NULL,
    note TEXT NULL,
    PRIMARY KEY (thing_id, alias)
);
CREATE UNIQUE INDEX IF NOT EXISTS uidx_aliases_alias ON aliases(alias COLLATE NOCASE);

CREATE TABLE IF NOT EXISTS states (
    id INTEGER PRIMARY KEY,
    thing_id INTEGER NOT NULL REFERENCES things(id),
    object_id INTEGER NULL REFERENCES things(id),
    text TEXT NOT NULL,
    kind TEXT NOT NULL DEFAULT 'belief',          -- belief | rule | commitment
    state_key TEXT NULL,                          -- the relation this is a value of ("ci", "owner"); at most one current per (thing, key)
    is_state INTEGER NOT NULL DEFAULT 0,          -- 1 a value true until replaced, 0 an event that happened
    as_of_utc TEXT NULL,                          -- when it happened or became true, if the input says
    recorded_utc TEXT NOT NULL,                   -- when the layer learned it
    confirmed_utc TEXT NULL,                      -- last time it was verified still to hold (not the same as changed)
    status TEXT NOT NULL DEFAULT 'pending',       -- pending | active | proposed | rejected
    trust TEXT NOT NULL,                          -- owner | verified | derived | claimed | extracted
    learned_how TEXT NOT NULL,                    -- read | inferred | told | claimed
    writer TEXT NOT NULL,                         -- a rule name, an agent, the owner
    session_id TEXT NULL,                         -- the harness's session id, never the agent's own claim
    volatility TEXT NOT NULL DEFAULT 'work',      -- live | work | stable | rule
    verify_handle TEXT NULL,                      -- where to re-read it: a command, URL or query
    superseded_by INTEGER NULL REFERENCES states(id),
    supersedes INTEGER NULL REFERENCES states(id),
    resolution TEXT NULL,
    CHECK (status IN ('pending', 'active', 'proposed', 'rejected')),
    CHECK (kind IN ('belief', 'rule', 'commitment')),
    CHECK (trust IN ('owner', 'verified', 'derived', 'claimed', 'extracted')),
    CHECK (learned_how IN ('read', 'inferred', 'told', 'claimed')),
    CHECK (volatility IN ('live', 'work', 'stable', 'rule'))
);
CREATE INDEX IF NOT EXISTS idx_states_thing ON states(thing_id);
CREATE INDEX IF NOT EXISTS idx_states_object ON states(object_id) WHERE object_id IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS uidx_states_current_key ON states(thing_id, state_key)
    WHERE state_key IS NOT NULL AND superseded_by IS NULL AND status = 'active';

CREATE TABLE IF NOT EXISTS evidence (
    state_id INTEGER NOT NULL REFERENCES states(id),
    ref_type TEXT NOT NULL,                       -- message | file | url | command | legacy-source | ...
    ref TEXT NOT NULL,
    excerpt TEXT NULL,
    sha256 TEXT NULL,
    recorded_utc TEXT NOT NULL,
    PRIMARY KEY (state_id, ref_type, ref)
);
CREATE INDEX IF NOT EXISTS idx_evidence_ref ON evidence(ref_type, ref);

-- Negative links: a person saying "these may look related but they are not". Selection honours the active ones; nothing is deleted (a relink
-- sets removed_utc), and every change is an event. Shapes: (state, thing) a state never connects through that thing; (state, concept) a state
-- never surfaces for inputs about that concept; (state, match='meaning') a state surfaces only through its own name, never by meaning or words;
-- (thing, other_thing) two things never connect each other's states.
CREATE TABLE IF NOT EXISTS unlinks (
    id INTEGER PRIMARY KEY,
    state_id INTEGER NULL REFERENCES states(id),
    thing_id INTEGER NULL REFERENCES things(id),
    other_thing_id INTEGER NULL REFERENCES things(id),
    concept TEXT NULL,
    match TEXT NOT NULL DEFAULT 'any',            -- any | name | meaning
    actor TEXT NOT NULL,
    reason TEXT NOT NULL,
    created_utc TEXT NOT NULL,
    removed_utc TEXT NULL,
    removed_reason TEXT NULL,
    CHECK (match IN ('any', 'name', 'meaning'))
);
CREATE INDEX IF NOT EXISTS idx_unlinks_state ON unlinks(state_id) WHERE state_id IS NOT NULL;

CREATE TRIGGER IF NOT EXISTS unlinks_event_added AFTER INSERT ON unlinks
BEGIN
    INSERT INTO events (event_type, thing_id, state_id, actor, payload, recorded_utc)
    VALUES ('unlinked', NEW.thing_id, NEW.state_id, NEW.actor,
            json_object('unlink', NEW.id, 'other_thing', NEW.other_thing_id, 'concept', NEW.concept, 'match', NEW.match, 'reason', NEW.reason),
            strftime('%Y-%m-%d %H:%M:%f+00:00', 'now'));
END;

CREATE TRIGGER IF NOT EXISTS unlinks_event_removed AFTER UPDATE OF removed_utc ON unlinks
WHEN OLD.removed_utc IS NULL AND NEW.removed_utc IS NOT NULL
BEGIN
    INSERT INTO events (event_type, thing_id, state_id, actor, payload, recorded_utc)
    VALUES ('relinked', NEW.thing_id, NEW.state_id, COALESCE((SELECT actor FROM event_context WHERE id = 1), 'store'),
            json_object('unlink', NEW.id, 'reason', NEW.removed_reason), strftime('%Y-%m-%d %H:%M:%f+00:00', 'now'));
END;

CREATE TABLE IF NOT EXISTS flags (
    id INTEGER PRIMARY KEY,
    state_id INTEGER NOT NULL REFERENCES states(id),
    flagged_by TEXT NOT NULL,
    reason TEXT NOT NULL,
    created_utc TEXT NOT NULL,
    resolved_utc TEXT NULL
);

-- Vectors for meaning search. The unit that matters is the state; a thing's own description may be embedded too ('thing').
CREATE TABLE IF NOT EXISTS embeddings (
    owner_type TEXT NOT NULL,                     -- state | thing
    owner_id INTEGER NOT NULL,
    model TEXT NOT NULL,
    dims INTEGER NOT NULL,
    vector BLOB NOT NULL,
    content_hash TEXT NOT NULL,
    updated_utc TEXT NOT NULL,
    PRIMARY KEY (owner_type, owner_id, model)
);

-- ---- the append-only event log ----
CREATE TABLE IF NOT EXISTS events (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    event_type TEXT NOT NULL,
    thing_id INTEGER NULL,
    state_id INTEGER NULL,
    actor TEXT NOT NULL,
    payload TEXT NOT NULL,                        -- JSON: ids and values, never rendered prose
    recorded_utc TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_events_thing ON events(thing_id) WHERE thing_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_events_state ON events(state_id) WHERE state_id IS NOT NULL;

-- Who is acting, set by the store before each write so a trigger can record it.
CREATE TABLE IF NOT EXISTS event_context (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    actor TEXT NOT NULL
);

CREATE TRIGGER IF NOT EXISTS events_no_update BEFORE UPDATE ON events
BEGIN SELECT RAISE(ABORT, 'the event log is append-only'); END;
CREATE TRIGGER IF NOT EXISTS events_no_delete BEFORE DELETE ON events
BEGIN SELECT RAISE(ABORT, 'the event log is append-only'); END;

-- ---- rules ----
CREATE TRIGGER IF NOT EXISTS states_cite_before_standing
BEFORE UPDATE OF status ON states
WHEN NEW.status <> 'pending' AND NOT EXISTS (SELECT 1 FROM evidence WHERE state_id = NEW.id)
BEGIN SELECT RAISE(ABORT, 'a state must cite at least one piece of evidence'); END;

CREATE TRIGGER IF NOT EXISTS states_born_pending
BEFORE INSERT ON states
WHEN NEW.status <> 'pending'
BEGIN SELECT RAISE(ABORT, 'a state is inserted pending and stands only once its evidence is written'); END;

CREATE TRIGGER IF NOT EXISTS evidence_keep_the_last
BEFORE DELETE ON evidence
WHEN (SELECT status FROM states WHERE id = OLD.state_id) <> 'pending'
 AND (SELECT COUNT(*) FROM evidence WHERE state_id = OLD.state_id) = 1
BEGIN SELECT RAISE(ABORT, 'a standing state cannot lose its last evidence'); END;

-- ---- one event per change, in the transaction that makes it ----
CREATE TRIGGER IF NOT EXISTS things_event_added AFTER INSERT ON things
BEGIN
    INSERT INTO events (event_type, thing_id, actor, payload, recorded_utc)
    VALUES ('thing-added', NEW.id, COALESCE((SELECT actor FROM event_context WHERE id = 1), 'store'),
            json_object('name', NEW.name, 'kind', NEW.kind, 'kind_confirmed', NEW.kind_confirmed, 'parent', NEW.parent_id),
            strftime('%Y-%m-%d %H:%M:%f+00:00', 'now'));
END;

CREATE TRIGGER IF NOT EXISTS things_event_kind AFTER UPDATE OF kind, kind_confirmed ON things
WHEN OLD.kind IS NOT NEW.kind OR OLD.kind_confirmed IS NOT NEW.kind_confirmed
BEGIN
    INSERT INTO events (event_type, thing_id, actor, payload, recorded_utc)
    VALUES ('thing-kind-set', NEW.id, COALESCE((SELECT actor FROM event_context WHERE id = 1), 'store'),
            json_object('kind', NEW.kind, 'kind_confirmed', NEW.kind_confirmed, 'was', OLD.kind),
            strftime('%Y-%m-%d %H:%M:%f+00:00', 'now'));
END;

CREATE TRIGGER IF NOT EXISTS aliases_event_added AFTER INSERT ON aliases
BEGIN
    INSERT INTO events (event_type, thing_id, actor, payload, recorded_utc)
    VALUES ('alias-added', NEW.thing_id, COALESCE((SELECT actor FROM event_context WHERE id = 1), 'store'),
            json_object('alias', NEW.alias, 'note', NEW.note), strftime('%Y-%m-%d %H:%M:%f+00:00', 'now'));
END;

CREATE TRIGGER IF NOT EXISTS aliases_event_moved AFTER UPDATE ON aliases
WHEN OLD.thing_id IS NOT NEW.thing_id
BEGIN
    INSERT INTO events (event_type, thing_id, actor, payload, recorded_utc)
    VALUES ('alias-moved', NEW.thing_id, COALESCE((SELECT actor FROM event_context WHERE id = 1), 'store'),
            json_object('alias', NEW.alias, 'note', NEW.note, 'from', OLD.thing_id), strftime('%Y-%m-%d %H:%M:%f+00:00', 'now'));
END;

CREATE TRIGGER IF NOT EXISTS states_event_added AFTER INSERT ON states
BEGIN
    INSERT INTO events (event_type, thing_id, state_id, actor, payload, recorded_utc)
    VALUES ('state-added', NEW.thing_id, NEW.id, COALESCE((SELECT actor FROM event_context WHERE id = 1), NEW.writer),
            json_object('object', NEW.object_id, 'text', NEW.text, 'kind', NEW.kind, 'key', NEW.state_key, 'is_state', NEW.is_state,
                        'as_of', NEW.as_of_utc, 'trust', NEW.trust, 'learned_how', NEW.learned_how, 'writer', NEW.writer,
                        'session', NEW.session_id, 'supersedes', NEW.supersedes),
            strftime('%Y-%m-%d %H:%M:%f+00:00', 'now'));
END;

CREATE TRIGGER IF NOT EXISTS evidence_event_added AFTER INSERT ON evidence
BEGIN
    INSERT INTO events (event_type, thing_id, state_id, actor, payload, recorded_utc)
    VALUES ('state-evidenced', (SELECT thing_id FROM states WHERE id = NEW.state_id), NEW.state_id,
            COALESCE((SELECT actor FROM event_context WHERE id = 1), 'store'),
            json_object('ref_type', NEW.ref_type, 'ref', NEW.ref), strftime('%Y-%m-%d %H:%M:%f+00:00', 'now'));
END;

CREATE TRIGGER IF NOT EXISTS states_event_status AFTER UPDATE OF status, resolution ON states
WHEN OLD.status IS NOT NEW.status OR OLD.resolution IS NOT NEW.resolution
BEGIN
    INSERT INTO events (event_type, thing_id, state_id, actor, payload, recorded_utc)
    VALUES ('state-status', NEW.thing_id, NEW.id, COALESCE((SELECT actor FROM event_context WHERE id = 1), 'store'),
            json_object('from', OLD.status, 'to', NEW.status, 'resolution', NEW.resolution), strftime('%Y-%m-%d %H:%M:%f+00:00', 'now'));
END;

CREATE TRIGGER IF NOT EXISTS states_event_superseded AFTER UPDATE OF superseded_by ON states
WHEN OLD.superseded_by IS NOT NEW.superseded_by
BEGIN
    INSERT INTO events (event_type, thing_id, state_id, actor, payload, recorded_utc)
    VALUES ('state-superseded', NEW.thing_id, NEW.id, COALESCE((SELECT actor FROM event_context WHERE id = 1), 'store'),
            json_object('by', NEW.superseded_by, 'resolution', NEW.resolution), strftime('%Y-%m-%d %H:%M:%f+00:00', 'now'));
END;

CREATE TRIGGER IF NOT EXISTS states_event_confirmed AFTER UPDATE OF confirmed_utc ON states
WHEN OLD.confirmed_utc IS NOT NEW.confirmed_utc
BEGIN
    INSERT INTO events (event_type, thing_id, state_id, actor, payload, recorded_utc)
    VALUES ('state-confirmed', NEW.thing_id, NEW.id, COALESCE((SELECT actor FROM event_context WHERE id = 1), 'store'),
            json_object('confirmed', NEW.confirmed_utc), strftime('%Y-%m-%d %H:%M:%f+00:00', 'now'));
END;

CREATE TRIGGER IF NOT EXISTS flags_event_added AFTER INSERT ON flags
BEGIN
    INSERT INTO events (event_type, thing_id, state_id, actor, payload, recorded_utc)
    VALUES ('state-flagged', (SELECT thing_id FROM states WHERE id = NEW.state_id), NEW.state_id, NEW.flagged_by,
            json_object('flag', NEW.id, 'reason', NEW.reason), strftime('%Y-%m-%d %H:%M:%f+00:00', 'now'));
END;

-- Outcomes: what happened to a state after it was (or was not) surfaced. Append-only. Ground truth comes from outside the store (a deterministic
-- writer's re-read, a verify handle, the agent's later output); the store never grades itself. `params` records the parameters in force.
CREATE TABLE IF NOT EXISTS outcomes (
    id INTEGER PRIMARY KEY,
    state_id INTEGER NULL REFERENCES states(id),
    thing_id INTEGER NULL REFERENCES things(id),
    kind TEXT NOT NULL,
    source TEXT NOT NULL,
    turn_ref TEXT NULL,
    detail TEXT NULL,
    params TEXT NULL,
    recorded_utc TEXT NOT NULL,
    CHECK (kind IN ('surfaced', 'omitted', 'used', 'unhelpful', 'contradicted', 'verified_right', 'verified_wrong', 'missed'))
);
CREATE INDEX IF NOT EXISTS idx_outcomes_state ON outcomes(state_id);
CREATE INDEX IF NOT EXISTS idx_outcomes_turn ON outcomes(turn_ref) WHERE turn_ref IS NOT NULL;

CREATE TRIGGER IF NOT EXISTS outcomes_no_update BEFORE UPDATE ON outcomes
BEGIN SELECT RAISE(ABORT, 'outcomes are append-only'); END;
CREATE TRIGGER IF NOT EXISTS outcomes_no_delete BEFORE DELETE ON outcomes
BEGIN SELECT RAISE(ABORT, 'outcomes are append-only'); END;

-- Parameters the system may tune (thresholds, floors, per-writer trust, per-kind half-life). Every change is a row: who, why, what it replaced.
-- The current value is the latest row for the name; reverting appends a row with the previous value.
CREATE TABLE IF NOT EXISTS param_history (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    value REAL NOT NULL,
    reason TEXT NOT NULL,
    actor TEXT NOT NULL,
    previous REAL NULL,
    recorded_utc TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_param_name ON param_history(name, id);
CREATE TRIGGER IF NOT EXISTS param_no_update BEFORE UPDATE ON param_history
BEGIN SELECT RAISE(ABORT, 'parameter history is append-only'); END;
CREATE TRIGGER IF NOT EXISTS param_no_delete BEFORE DELETE ON param_history
BEGIN SELECT RAISE(ABORT, 'parameter history is append-only'); END;

-- The text of live turns seen by the shadow adapters: what the user typed and what the agent answered, keyed by the turn the outcomes refer to.
-- Local to the machine; used for offline miss detection (restatements, unstored facts). Never injected anywhere.
CREATE TABLE IF NOT EXISTS turn_texts (
    turn_ref TEXT NOT NULL,
    kind TEXT NOT NULL,
    session_id TEXT NULL,
    created_utc TEXT NOT NULL,
    text TEXT NOT NULL,
    PRIMARY KEY (turn_ref, kind),
    CHECK (kind IN ('prompt', 'output'))
);
CREATE INDEX IF NOT EXISTS idx_turn_texts_created ON turn_texts(created_utc);

-- Links between states that are about the same or related content (consolidation). `same` = judged a duplicate (the newer was merged into the older),
-- `related` = close in meaning but distinct. Append-only; a link is evidence for retrieval ("see also") and for validating consolidation from outcomes.
CREATE TABLE IF NOT EXISTS state_links (
    id INTEGER PRIMARY KEY,
    a_state INTEGER NOT NULL REFERENCES states(id),
    b_state INTEGER NOT NULL REFERENCES states(id),
    relation TEXT NOT NULL,
    score REAL NOT NULL,
    actor TEXT NOT NULL,
    created_utc TEXT NOT NULL,
    CHECK (relation IN ('same', 'related')),
    CHECK (a_state <> b_state),
    UNIQUE (a_state, b_state, relation)
);
CREATE TRIGGER IF NOT EXISTS links_no_update BEFORE UPDATE ON state_links
BEGIN SELECT RAISE(ABORT, 'links are append-only'); END;
CREATE TRIGGER IF NOT EXISTS links_no_delete BEFORE DELETE ON state_links
BEGIN SELECT RAISE(ABORT, 'links are append-only'); END;

-- Which source rows an import already brought in, so a re-import is incremental and never duplicates.
CREATE TABLE IF NOT EXISTS import_map (
    source TEXT NOT NULL,
    source_id INTEGER NOT NULL,
    state_id INTEGER NOT NULL REFERENCES states(id),
    PRIMARY KEY (source, source_id)
);

-- Shadow injections: what was (Legacy) or WOULD have been (statekeeper) put in front of the model at a hook moment, per turn, so the two can be compared.
-- The statekeeper rows are never emitted anywhere. `kind` is a coarse category (concept_map, tracked, session_core, other).
CREATE TABLE IF NOT EXISTS shadow_injections (
    id INTEGER PRIMARY KEY,
    turn_ref TEXT NOT NULL,
    source TEXT NOT NULL,
    hook_event TEXT NOT NULL,
    kind TEXT NOT NULL,
    text TEXT NOT NULL,
    created_utc TEXT NOT NULL,
    CHECK (source IN ('legacy', 'statekeeper'))
);
CREATE INDEX IF NOT EXISTS idx_shadow_inj_turn ON shadow_injections(turn_ref);

-- Requests a state answers: short things a person says when they need it ("is there a script to sign the agent into QA?"), written once per durable
-- or how-to state by a model, embedded as owner_type 'question'. A prompt is matched against these as well as against the state's own text: a weak
-- embedding model matches a request to a request far better than a request to a statement of fact. Never shown; evidence for retrieval only.
CREATE TABLE IF NOT EXISTS state_questions (
    id INTEGER PRIMARY KEY,
    state_id INTEGER NOT NULL REFERENCES states(id),
    text TEXT NOT NULL,
    writer TEXT NOT NULL,
    created_utc TEXT NOT NULL,
    UNIQUE (state_id, text)
);
CREATE INDEX IF NOT EXISTS idx_state_questions_state ON state_questions(state_id);
