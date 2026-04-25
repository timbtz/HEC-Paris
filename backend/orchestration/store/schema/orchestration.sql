-- orchestration.db — Runtime journal: pipeline runs, events, ingress, cache.
-- Source: RealMetaPRD §7.5 (lines 1048–1109). Append-only by design.

CREATE TABLE pipeline_runs (
    id                INTEGER PRIMARY KEY,
    pipeline_name     TEXT NOT NULL,
    pipeline_version  INTEGER NOT NULL,
    trigger_source    TEXT NOT NULL,
    trigger_payload   TEXT NOT NULL,
    employee_id_logical TEXT,             -- logical FK → audit.employees.id
    status            TEXT NOT NULL,
    error             TEXT,
    started_at        TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    completed_at      TEXT,
    metadata          TEXT,
    CHECK (json_valid(trigger_payload)),
    CHECK (metadata IS NULL OR json_valid(metadata))
) STRICT;

CREATE TABLE pipeline_events (
    id              INTEGER PRIMARY KEY,
    run_id          INTEGER NOT NULL REFERENCES pipeline_runs(id),
    event_type      TEXT NOT NULL,
    node_id         TEXT,
    data            TEXT NOT NULL,
    payload_version INTEGER NOT NULL DEFAULT 1,    -- REF-SQLITE-BACKBONE:578
    elapsed_ms      INTEGER,
    created_at      TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CHECK (json_valid(data))
) STRICT;
CREATE INDEX idx_events_run ON pipeline_events(run_id, created_at);

CREATE TABLE external_events (
    id              INTEGER PRIMARY KEY,
    provider        TEXT NOT NULL,        -- 'swan' | 'shopify' | 'document' | …
    event_id        TEXT NOT NULL,
    event_type      TEXT NOT NULL,
    resource_id     TEXT,
    payload         TEXT NOT NULL,
    payload_version INTEGER NOT NULL DEFAULT 1,    -- REF-SQLITE-BACKBONE:578
    processed       INTEGER NOT NULL DEFAULT 0,
    created_at      TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (provider, event_id),
    CHECK (json_valid(payload))
) STRICT;

CREATE TABLE node_cache (
    cache_key       TEXT PRIMARY KEY,
    node_id         TEXT NOT NULL,
    pipeline_name   TEXT NOT NULL,
    code_version    TEXT NOT NULL,
    input_json      TEXT NOT NULL,
    output_json     TEXT NOT NULL,
    payload_version INTEGER NOT NULL DEFAULT 1,    -- REF-SQLITE-BACKBONE:578
    created_at      TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_hit_at     TEXT,
    hit_count       INTEGER NOT NULL DEFAULT 0,
    CHECK (json_valid(input_json) AND json_valid(output_json))
) STRICT;

CREATE TABLE _migrations (
    id INTEGER PRIMARY KEY, name TEXT UNIQUE NOT NULL,
    applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
) STRICT;
