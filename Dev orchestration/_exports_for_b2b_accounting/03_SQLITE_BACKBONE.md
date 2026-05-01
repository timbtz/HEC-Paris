# 03 · SQLite Backbone — Data Layer Patterns Worth Lifting

This document catalogs the SQLite design that the new project (B2B accounting on Swan rails) should mirror. The patterns transfer directly; only the column names change. The text annotates "translates to accounting as ..." for each pattern.

---

## 1. The two-database split

Fingent runs against **two separate SQLite files**, joined only at runtime through `FingentContext`:

| File | Purpose | Lifecycle | Schema |
|------|---------|-----------|--------|
| `db_enriched.sqlite` | **Domain data** — canonical entities, identifiers, scored facts, decision tables, append-only event log | Long-lived; bootstrapped from `db.sqlite` (raw seed) and migrated forward | `schema/enriched_schema.sql` + a stack of `enrichment/db_migrate_*.py` scripts |
| `orchestration.db` | **Execution log** — pipeline runs, per-node events, durable audit trail of every workflow execution | Auto-created on FastAPI startup; small, indexed, append-mostly | `orchestration/schema/pipeline_schema.sql`, applied by `orchestration/api/db.py:init_db()` |

**Why the split:**

- The domain DB is the *system of record*. It is queried by user-facing read paths and updated by tools that are explicitly granted writeback permission. Schema changes go through a migration.
- The orchestration DB is the *journal of what happened*. It is append-only at the row level (no `UPDATE pipeline_events`), and it can be wiped without losing domain truth. It exists so any pipeline run can be reconstructed end-to-end without leaning on log files.
- Separating them lets you `WAL` both, attach them in the same connection if you ever need a join, and back them up on different cadences. The new project should keep this split: `accounting.db` (canonical entities, journal entries, balances) and `orchestration.db` (workflow trace).

Both files use `PRAGMA journal_mode=WAL` (`orchestration/api/db.py:16`, `enrichment/db_bootstrap.py:190`). The new project should do the same. Foreign keys are enabled in the bootstrap (`PRAGMA foreign_keys=ON` at line 191).

---

## 2. Patterns worth lifting

### 2a. Confidence-scored identity tables

The cleanest example is `Ingredient_Canonical` (`schema/enriched_schema.sql:9-25`):

```sql
CREATE TABLE IF NOT EXISTS Ingredient_Canonical (
    Id              INTEGER PRIMARY KEY AUTOINCREMENT,
    Name            TEXT NOT NULL,
    CAS_Number      TEXT,
    PubChem_CID     INTEGER,
    UNII_Code       TEXT,                 -- FDA UNII identifier
    Molport_Id      TEXT,
    FDC_Id          INTEGER,
    RxCUI           TEXT,
    SMILES          TEXT,
    IUPAC_Name      TEXT,
    Function        TEXT,                 -- "excipient:lubricant" etc.
    Confidence      REAL NOT NULL DEFAULT 0.0,    -- 0.0–1.0
    Sources         TEXT NOT NULL DEFAULT '[]',   -- JSON array of source strings
    Grade_Flag      TEXT DEFAULT 'unknown'
);
```

The shape: **one row per canonical entity, with N alternative external identifiers as columns**, plus a `Confidence` and a JSON `Sources` array recording where the row came from. The same compound is reachable by CAS, UNII, PubChem CID, or fuzzy name; resolution code tries each in turn.

> **Translates to accounting as:** `counterparties` table — one row per resolved counterparty, with alternative identifiers as columns: `iban`, `vat_number`, `mcc`, `merchant_id`, `email_domain`, `legal_name`. Plus `confidence`, `sources` (JSON array), `country`, `gleif_lei`. A novel `iban` arriving on a Swan webhook gets resolved through identifiers in priority order; on miss it falls through to a Claude agent that returns a candidate counterparty + confidence; that becomes a new row, and every subsequent occurrence is deterministic.

The mapping table — incoming SKUs → canonical — is a separate table (`SKU_To_Canonical`, `enriched_schema.sql:28-39`):

```sql
CREATE TABLE IF NOT EXISTS SKU_To_Canonical (
    ProductId       INTEGER NOT NULL,
    CanonicalId     INTEGER NOT NULL,
    ExtractedName   TEXT,                       -- raw name parsed from slug
    MatchMethod     TEXT,                       -- pubchem|dsld|rxnorm|fuzzy|manual
    Confidence      REAL NOT NULL DEFAULT 0.0,
    MatchScore      REAL,                       -- raw rapidfuzz score (0–100)
    CreatedAt       TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (ProductId),
    FOREIGN KEY (ProductId)   REFERENCES Product(Id),
    FOREIGN KEY (CanonicalId) REFERENCES Ingredient_Canonical(Id)
);
```

> **Translates to accounting as:** `swan_transaction_to_counterparty` — `(swan_transaction_id, counterparty_id, match_method, confidence, match_score)`. Every Swan webhook transaction gets resolved against `counterparties`; the match method is one of `iban_exact`, `vat_exact`, `mcc_heuristic`, `fuzzy_name`, `claude_agent`, `manual`. This table is read by every downstream piece of logic that needs "who is this transaction with".

### 2b. Append-only event tables

The orchestration DB has the canonical example (`orchestration/schema/pipeline_schema.sql:16-24`):

```sql
CREATE TABLE IF NOT EXISTS pipeline_events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id      TEXT NOT NULL,
    event_type  TEXT NOT NULL,   -- pipeline_started|node_started|node_completed|...
    node_id     TEXT,
    data        TEXT NOT NULL DEFAULT '{}',     -- JSON
    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (run_id) REFERENCES pipeline_runs(id)
);
CREATE INDEX IF NOT EXISTS idx_pipeline_events_run_id ON pipeline_events(run_id);
```

There is no `update_event()` and no `delete_event()` in `orchestration/api/db.py`. The only write path is `write_event()` (`db.py:79-94`). Indexed by run_id; events for a single run are pulled in order with `WHERE run_id=? ORDER BY id`.

The domain side has `Enrichment_Run_Log` (`enriched_schema.sql:157-167`) for tracking external-API attempts, and `Agent_Log` (`enriched_schema.sql:185-200`) for agent step trace. Both are append-only. Note `Agent_Log.Run_Id` is the same UUID as `pipeline_runs.id` — the two databases are stitched by a shared identifier, not a foreign key.

> **Translates to accounting as:** `swan_webhook_events` — every raw Swan payload, keyed for idempotency by `(provider, swan_event_id)` UNIQUE. Never updated. The journal entry generator reads from this table and writes to `journal_entries`; if it fails, the event is still safe and the run can be retried.

### 2c. Audit / decision-trace tables

Three separate tables capture different decision traces; the new project needs all three shapes:

**Pipeline-level trace** (`pipeline_runs` + `pipeline_events`) is described above. Every workflow run, every node start/complete/fail/skip, with full input/output JSON. Reconstructable: see `db.get_run_with_events()` (`api/db.py:106-112`).

**Refusal log** (`enrichment/db_migrate_citation_refusal.py:25-36`):

```sql
CREATE TABLE IF NOT EXISTS Refusal_Log (
    Id              INTEGER PRIMARY KEY AUTOINCREMENT,
    CanonicalId     INTEGER NOT NULL,
    IngredientName  TEXT NOT NULL,
    Decision        TEXT NOT NULL,        -- refuse|defer_human_review|...
    Justification   TEXT,
    Confidence      REAL,
    BlockingFactors TEXT,                 -- JSON array
    UnblockHint     TEXT,                 -- what would clear the refusal
    RunId           TEXT,                 -- back-reference to pipeline_runs.id
    CreatedAt       TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_refusal_canonical ON Refusal_Log(CanonicalId);
```

The crucial fields are **`BlockingFactors`** (machine-readable JSON), **`UnblockHint`** (human-readable next step), and **`RunId`** linking back to the pipeline event log. This is the row the UI shows when it says "Fingent refused to recommend X — here's why and here's what would change the answer."

**Claim citations** (`db_migrate_citation_refusal.py:12-23`):

```sql
CREATE TABLE IF NOT EXISTS Claim_Citation (
    Id              INTEGER PRIMARY KEY AUTOINCREMENT,
    OpportunityId   INTEGER NOT NULL,
    ClaimText       TEXT NOT NULL,
    SourceType      TEXT NOT NULL,        -- pubchem|dsld|gleif|web|manual
    SourceId        TEXT, SourceUrl TEXT,
    SourceSnippet   TEXT,
    Confidence      REAL,
    CreatedAt       TEXT DEFAULT (datetime('now'))
);
```

Every assertion that flows into a generated proposal carries one or more citations pointing at the table row that justified it. This is the data layer behind "every claim is sourced".

> **Translates to accounting as:** for every journal entry the AI generates, write a `journal_entry_decision_trace` row with `entry_id`, `decision_type` (rule|cached|claude), `confidence`, `inputs_json`, `rule_fired_or_prompt`, `alternatives_considered_json`, plus a `citations` table linking each line item to its source webhook event or invoice. This is what makes the system audit-ready by construction.

### 2d. SKU-to-canonical resolution

The pattern is: **one foreign key per "incoming row" in a domain table** pointing to the canonical entity it resolves to, with the match method recorded inline. See `SKU_To_Canonical` above.

> **Translates to accounting as:** `swan_transactions.counterparty_id` (FK to `counterparties.id`), plus the `match_method` and `confidence` columns colocated. Reverse lookups (all transactions for a counterparty) just `WHERE counterparty_id=?`.

---

## 3. Bootstrap & migration

`enrichment/db_bootstrap.py` is the master bootstrap. It does five things in order (`db_bootstrap.py:176-208`):

1. **Clone** `db.sqlite` (raw seed) → `db_enriched.sqlite`. With `--force`, deletes the existing enriched DB.
2. **Apply schema** — `conn.executescript(SCHEMA_FILE.read_text())` runs the full `schema/enriched_schema.sql` (which uses `CREATE TABLE IF NOT EXISTS` everywhere — idempotent).
3. **Run incremental migrations** — `from enrichment.db_migrate_v11 import migrate_v11; migrate_v11(conn)`. Each migration is a Python module with its own `migrate(conn)` function and is idempotent against the schema state it expects.
4. **Seed reference data** — `_seed_substitution_rules()` populates curated rules (skipped if already present); `_seed_confidence_matrix()` writes a JSON config blob into `API_Response_Cache` keyed by `('config', 'confidence_matrix')`.
5. **Validate** — `_validate()` confirms the required table set is present (`db_bootstrap.py:236-253`) and prints a summary.

The migration files (`enrichment/db_migrate_*.py`) are flat Python with one `migrate(conn)` function each. They use `CREATE TABLE IF NOT EXISTS`, `ALTER TABLE` guarded by introspection of `PRAGMA table_info`, and `INSERT OR REPLACE`. None of them rely on a versioning table — idempotency is achieved by structural checks. **Honest gap:** there is no `applied_migrations` table, so re-running the migrations is safe but you can't easily tell which ones have run. The new project should add a tiny `_migrations` table (id, name, applied_at) and skip already-applied ones — same shape as Alembic but five lines.

> **Translates to accounting as:** `accounting/db_bootstrap.py` clones `accounting_seed.sqlite` → `accounting.db`, applies `accounting/schema/schema.sql`, runs migrations from `accounting/migrations/`, seeds the chart of accounts and the European tax-code reference data. Add `_migrations` tracking from day one.

---

## 4. Indexes, triggers, and write-time invariants

### Indexes that matter

```sql
CREATE INDEX IF NOT EXISTS idx_pipeline_events_run_id ON pipeline_events(run_id);
CREATE INDEX IF NOT EXISTS idx_pipeline_runs_status   ON pipeline_runs(status);
CREATE INDEX IF NOT EXISTS idx_fda_iid_unii           ON FDA_Inactive_Ingredient(UNII);
CREATE INDEX IF NOT EXISTS idx_fda_iid_canonical      ON FDA_Inactive_Ingredient(CanonicalIngredientId);
CREATE INDEX IF NOT EXISTS idx_iid_cl_canonical       ON FDA_IID_Change_Log(CanonicalIngredientId);
CREATE INDEX IF NOT EXISTS idx_iid_cl_status          ON FDA_IID_Change_Log(Status);
CREATE INDEX IF NOT EXISTS idx_price_alert_canonical  ON Price_Change_Alert(CanonicalIngredientId);
CREATE INDEX IF NOT EXISTS idx_price_alert_dismissed  ON Price_Change_Alert(Dismissed, Detected_At);
```

The pattern: **index on the FK to the canonical entity**, plus **a status flag composite where the UI filters** (e.g. `(Dismissed, Detected_At)` for the alerts list). The new project should index `(counterparty_id)` on `swan_transactions`, `(status)` on `journal_entries`, and `(reviewed, created_at)` on whatever queue the human uses.

### CHECK constraints

```sql
Status TEXT NOT NULL CHECK(Status IN ('C','D','R'))   -- FDA_IID_Change_Log
UNIQUE (Source, Cache_Key)                            -- API_Response_Cache
UNIQUE(ChangeId, SnapshotDate, Route, DosageForm)     -- FDA_IID_Change_Log
```

CHECK constraints enforce enum validity; UNIQUE composites enforce row-level idempotency on append. The new project's most important invariant is **balanced double-entry**: `SUM(debits) = SUM(credits)` per journal entry.

**Honest gap:** Fingent does **not** use SQL triggers anywhere. The `SUM(debits) = SUM(credits)` invariant is something the new project will need to add itself. Two options:

1. **Trigger-based** — `CREATE TRIGGER` on `INSERT/UPDATE` of `journal_entry_lines` that re-aggregates and raises `RAISE(ABORT, ...)` if unbalanced. Atomic but harder to debug.
2. **Application-level** — wrap journal-entry creation in a single transaction in Python that inserts the header + all lines + a balance assertion (`SELECT SUM(amount_cents) FROM journal_entry_lines WHERE entry_id=?` checked before commit). Easier to test, easier to surface the violation as a structured error to the caller.

For a hackathon, the application-level option is faster to land and easier to explain. Add the trigger later as defense in depth.

There is one place Fingent does a write-time invariant via Python: the `RefusalEngine` short-circuits when `compound_confidence < CONFIDENCE_FLOOR (0.50)` and refuses to write a recommendation (`reasoning/refusal_engine.py:23, 57-78`). The pattern — invariant enforced at the gate, not at the row — applies cleanly: a journal entry that fails the balance check is *refused*, not corrupted into the table.

---

## 5. What to drop

The new project does **not** need:

- `Ingredient_Substitution_Rule` (curated substitution edges) — domain-specific.
- `BOM_Component_Quantity` — domain-specific.
- `Certification_Registry`, `FDA_Inactive_Ingredient`, `FDA_IID_Change_Log` — domain-specific reference data tables. The *shape* (raw scraped JSON in `Raw_Data` plus typed columns for what you index on) is reusable for European VAT and SEPA registries.
- `API_Response_Cache` (`enriched_schema.sql:146-154`) is worth keeping. It is a generic `(source, cache_key) → response` cache with a TTL column, indexed for upserts. Useful for caching GLEIF / VIES / Swan API responses without writing a new module per source.

```sql
CREATE TABLE IF NOT EXISTS API_Response_Cache (
    Id          INTEGER PRIMARY KEY AUTOINCREMENT,
    Source      TEXT NOT NULL,
    Cache_Key   TEXT NOT NULL,
    Response    TEXT,                       -- raw JSON; NULL = confirmed no-match
    Fetched_At  TEXT NOT NULL DEFAULT (datetime('now')),
    TTL_Days    INTEGER NOT NULL DEFAULT 30,
    UNIQUE (Source, Cache_Key)
);
```

The `NULL = confirmed no-match` convention is clever: a cache miss vs. "we asked and there is no match" are different semantics, and storing `NULL` instead of skipping the row preserves that distinction.

---

## 6. Summary — minimal table set for the new project

Domain DB (`accounting.db`):

- `counterparties` (canonical, confidence-scored, multi-identifier) — mirror `Ingredient_Canonical`
- `counterparty_identifiers` (alt rows pointing at one entity, if you want one-row-per-identifier instead of columns) — optional, more flexible
- `swan_webhook_events` (append-only raw payloads, idempotent on `(provider, event_id)`)
- `swan_transactions` (typed view of webhooks, with `counterparty_id` FK + match metadata)
- `journal_entries` + `journal_entry_lines` (header + lines, balance enforced application-side)
- `journal_entry_decision_trace` (per-entry: rule fired, confidence, alternatives JSON)
- `claim_citations` (links decisions to source events/invoices)
- `refusal_log` (when the AI refuses to post: justification + unblock_hint + run_id back-reference)
- `api_response_cache` (generic, lift verbatim)
- `_migrations` (id, name, applied_at — the one new thing)

Orchestration DB (`orchestration.db`): lift `pipeline_runs` + `pipeline_events` verbatim. Two tables, three indexes, done.
