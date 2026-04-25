-- accounting.db — Domain truth: GL, entities, documents, budgets.
-- Source: RealMetaPRD §7.5 (lines 849–1044).
-- All tables STRICT; integer cents (no floats); JSON columns guarded with
-- json_valid CHECK.

-- Bank mirror (Domain A): faithful Swan reflection
CREATE TABLE swan_transactions (
    id                   TEXT PRIMARY KEY,         -- Swan's transaction id
    swan_event_id        TEXT NOT NULL,
    side                 TEXT NOT NULL,            -- 'Debit' | 'Credit'
    type                 TEXT NOT NULL,            -- subtype string from Swan
    status               TEXT NOT NULL,            -- 'Booked' | 'Pending' | …
    amount_cents         INTEGER NOT NULL,
    currency             TEXT NOT NULL,            -- 'EUR' enforced
    counterparty_label   TEXT,
    payment_reference    TEXT,
    external_reference   TEXT,
    execution_date       TEXT NOT NULL,
    booked_balance_after INTEGER,
    raw                  TEXT NOT NULL,            -- normalized JSON
    created_at           TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CHECK (currency = 'EUR'),
    CHECK (json_valid(raw))
) STRICT;
CREATE INDEX idx_swan_tx_status ON swan_transactions(status);
CREATE INDEX idx_swan_tx_date   ON swan_transactions(execution_date);

-- Entity layer (Domain B)
CREATE TABLE counterparties (
    id           INTEGER PRIMARY KEY,
    legal_name   TEXT NOT NULL,
    kind         TEXT NOT NULL,        -- 'customer' | 'supplier' | 'employee' |
                                       --   'tax_authority' | 'bank' | 'internal'
    primary_iban TEXT,
    vat_number   TEXT,
    confidence   REAL,
    sources      TEXT,                 -- JSON array
    created_at   TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
) STRICT;

CREATE TABLE counterparty_identifiers (
    id              INTEGER PRIMARY KEY,
    counterparty_id INTEGER NOT NULL REFERENCES counterparties(id),
    identifier_type TEXT NOT NULL,     -- 'iban' | 'vat' | 'mcc' | 'merchant_id' |
                                       --   'email_domain' | 'name_alias'
    identifier      TEXT NOT NULL,
    source          TEXT NOT NULL,     -- 'rule' | 'config' | 'ai' | 'user'
    confidence      REAL,
    created_at      TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (identifier_type, identifier)
) STRICT;

-- Documents (Domain C)
CREATE TABLE documents (
    id              INTEGER PRIMARY KEY,
    sha256          TEXT NOT NULL UNIQUE,
    kind            TEXT NOT NULL,        -- 'invoice_in' | 'invoice_out' |
                                          --   'receipt' | 'contract'
    direction       TEXT NOT NULL,        -- 'inbound' | 'outbound'
    counterparty_id INTEGER REFERENCES counterparties(id),
    amount_cents    INTEGER,
    vat_cents       INTEGER,
    issue_date      TEXT,
    due_date        TEXT,
    employee_id     INTEGER,              -- logical FK → audit.employees.id
    extraction      TEXT,                 -- JSON of full extraction
    blob_path       TEXT NOT NULL,        -- data/blobs/<sha256>
    created_at      TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CHECK (extraction IS NULL OR json_valid(extraction))
) STRICT;

CREATE TABLE document_line_items (
    id          INTEGER PRIMARY KEY,
    document_id INTEGER NOT NULL REFERENCES documents(id),
    description TEXT,
    amount_cents INTEGER NOT NULL,
    vat_rate_bp INTEGER,                  -- VAT rate in basis points (2000 = 20%)
    gl_hint     TEXT
) STRICT;

CREATE TABLE expected_payments (
    id              INTEGER PRIMARY KEY,
    direction       TEXT NOT NULL,        -- 'inbound' | 'outbound'
    counterparty_id INTEGER NOT NULL REFERENCES counterparties(id),
    document_id     INTEGER REFERENCES documents(id),
    amount_cents    INTEGER NOT NULL,
    due_date        TEXT,
    status          TEXT NOT NULL,        -- 'open' | 'partial' | 'paid' |
                                          --   'overdue' | 'written_off'
    created_at      TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
) STRICT;

-- GL (Domain D)
CREATE TABLE chart_of_accounts (
    code      TEXT PRIMARY KEY,           -- e.g. '512', '401', '626100'
    name      TEXT NOT NULL,
    type      TEXT NOT NULL,              -- 'asset' | 'liability' | 'equity' |
                                          --   'revenue' | 'expense' | 'contra'
    parent    TEXT REFERENCES chart_of_accounts(code)
) STRICT;

CREATE TABLE journal_entries (
    id                INTEGER PRIMARY KEY,
    basis             TEXT NOT NULL,      -- 'cash' | 'accrual'
    entry_date        TEXT NOT NULL,
    description       TEXT,
    source_pipeline   TEXT NOT NULL,
    source_run_id     INTEGER NOT NULL,   -- logical FK → orchestration.pipeline_runs.id
    status            TEXT NOT NULL,      -- 'draft' | 'posted' | 'reversed'
    accrual_link_id   INTEGER REFERENCES journal_entries(id),
                                          -- pairs cash and accrual entries
    reversal_of_id    INTEGER REFERENCES journal_entries(id),
                                          -- explicit reversal pointer
    created_at        TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CHECK (basis IN ('cash','accrual'))
) STRICT;

CREATE TABLE journal_lines (
    id                  INTEGER PRIMARY KEY,
    entry_id            INTEGER NOT NULL REFERENCES journal_entries(id),
    account_code        TEXT NOT NULL REFERENCES chart_of_accounts(code),
    debit_cents         INTEGER NOT NULL DEFAULT 0,
    credit_cents        INTEGER NOT NULL DEFAULT 0,
    counterparty_id     INTEGER REFERENCES counterparties(id),
    swan_transaction_id TEXT REFERENCES swan_transactions(id),
    document_id         INTEGER REFERENCES documents(id),
    description         TEXT,
    CHECK (debit_cents >= 0 AND credit_cents >= 0),
    CHECK (NOT (debit_cents > 0 AND credit_cents > 0))
) STRICT;
CREATE INDEX idx_lines_entry  ON journal_lines(entry_id);
CREATE INDEX idx_lines_account ON journal_lines(account_code);

CREATE TABLE decision_traces (
    id              INTEGER PRIMARY KEY,
    line_id         INTEGER NOT NULL REFERENCES journal_lines(id),
    source          TEXT NOT NULL,        -- 'webhook' | 'agent' | 'rule' | 'human'
    rule_id         TEXT,
    confidence      REAL,
    -- cross-DB seam to audit.agent_decisions:
    agent_decision_id_logical TEXT,
    parent_event_id TEXT,                 -- swan_event_id or document.sha256
    approver_id     INTEGER,              -- logical FK → audit.employees.id
    approved_at     TEXT,
    created_at      TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
) STRICT;
CREATE INDEX idx_traces_line ON decision_traces(line_id);

-- Configuration / policy (Domain E)
CREATE TABLE account_rules (
    id              INTEGER PRIMARY KEY,
    pattern_kind    TEXT NOT NULL,        -- 'mcc' | 'counterparty' | 'iban' |
                                          --   'merchant_name'
    pattern_value   TEXT NOT NULL,
    gl_account      TEXT NOT NULL REFERENCES chart_of_accounts(code),
    precedence      INTEGER NOT NULL DEFAULT 100,
    source          TEXT NOT NULL,        -- 'config' | 'ai' | 'user'
    created_at      TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
) STRICT;

CREATE TABLE vat_rates (
    id           INTEGER PRIMARY KEY,
    gl_account   TEXT REFERENCES chart_of_accounts(code),
    rate_bp      INTEGER NOT NULL,        -- basis points; 2000 = 20%
    valid_from   TEXT NOT NULL,
    valid_to     TEXT
) STRICT;

CREATE TABLE confidence_thresholds (
    id           INTEGER PRIMARY KEY,
    scope        TEXT NOT NULL,           -- 'global' | 'pipeline:<name>'
    floor        REAL NOT NULL DEFAULT 0.50,
    created_at   TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
) STRICT;

-- Budgets (Domain F — minimum-viable in MVP)
CREATE TABLE budget_envelopes (
    id              INTEGER PRIMARY KEY,
    scope_kind      TEXT NOT NULL,        -- 'employee' | 'team' | 'company'
    scope_id        INTEGER,              -- employee_id or team_id (NULL for company)
    category        TEXT NOT NULL,        -- 'food' | 'travel' | 'saas' | 'ai_tokens' | …
    period          TEXT NOT NULL,        -- 'YYYY-MM'
    cap_cents       INTEGER NOT NULL,
    soft_threshold_pct INTEGER DEFAULT 80,
    created_at      TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
) STRICT;

CREATE TABLE budget_allocations (
    id           INTEGER PRIMARY KEY,
    envelope_id  INTEGER NOT NULL REFERENCES budget_envelopes(id),
    line_id      INTEGER NOT NULL REFERENCES journal_lines(id),
    amount_cents INTEGER NOT NULL,
    created_at   TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
) STRICT;

CREATE TABLE _migrations (
    id INTEGER PRIMARY KEY, name TEXT UNIQUE NOT NULL,
    applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
) STRICT;
