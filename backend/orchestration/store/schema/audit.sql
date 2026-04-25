-- audit.db — Agent observability: decisions, costs, employees.
-- Source: RealMetaPRD §7.5 (lines 1114–1171). Append-only; the EU AI Act story.

CREATE TABLE employees (
    id                  INTEGER PRIMARY KEY,
    email               TEXT NOT NULL UNIQUE,
    full_name           TEXT,
    swan_iban           TEXT UNIQUE,
    swan_account_id     TEXT UNIQUE,
    manager_employee_id INTEGER REFERENCES employees(id),
    department          TEXT,             -- free-form for MVP
    active              INTEGER NOT NULL DEFAULT 1,
    created_at          TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
) STRICT;

CREATE TABLE agent_decisions (
    id                  INTEGER PRIMARY KEY,
    run_id_logical      INTEGER NOT NULL,        -- logical FK → orchestration.pipeline_runs.id
    node_id             TEXT NOT NULL,
    source              TEXT NOT NULL,           -- 'agent' | 'rule' | 'cache' | 'human'
    runner              TEXT NOT NULL,           -- 'anthropic' | 'adk' | 'pydantic_ai'
    model               TEXT,
    response_id         TEXT,
    prompt_hash         TEXT,
    alternatives_json   TEXT,
    confidence          REAL,
    line_id_logical     TEXT,                    -- logical FK → accounting.journal_lines.id
    -- LLM-call observability (ANTHROPIC_SDK_STACK_REFERENCE:1087-1107)
    latency_ms          INTEGER,
    finish_reason       TEXT,                    -- 'end_turn' | 'tool_use' | 'max_tokens' | …
    temperature         REAL,
    seed                INTEGER,
    started_at          TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    completed_at        TEXT,
    CHECK (alternatives_json IS NULL OR json_valid(alternatives_json)),
    CHECK (confidence IS NULL OR (confidence >= 0 AND confidence <= 1))
) STRICT;
CREATE INDEX idx_decisions_run  ON agent_decisions(run_id_logical);
CREATE INDEX idx_decisions_line ON agent_decisions(line_id_logical);

CREATE TABLE agent_costs (
    decision_id        INTEGER PRIMARY KEY REFERENCES agent_decisions(id),
    employee_id        INTEGER REFERENCES employees(id),
    provider           TEXT NOT NULL,
    model              TEXT NOT NULL,
    input_tokens       INTEGER NOT NULL DEFAULT 0,
    output_tokens      INTEGER NOT NULL DEFAULT 0,
    cache_read_tokens  INTEGER NOT NULL DEFAULT 0,
    cache_write_tokens INTEGER NOT NULL DEFAULT 0,
    reasoning_tokens   INTEGER NOT NULL DEFAULT 0,
    cost_micro_usd     INTEGER NOT NULL,
    created_at         TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
) STRICT;
CREATE INDEX idx_costs_employee_month ON agent_costs(employee_id, created_at);
CREATE INDEX idx_costs_provider_month ON agent_costs(provider, created_at);

CREATE TABLE _migrations (
    id INTEGER PRIMARY KEY, name TEXT UNIQUE NOT NULL,
    applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
) STRICT;
