---
title: PRD1 — Orchestration Backbone (Metalayer)
status: draft
parent: Orchestration/PRDs/PRD.md
phase_of_parent: 1
scope: orchestration metalayer only — no domain pipelines, no frontend, no concrete tools/agents
last_updated: 2026-04-25
---

# PRD1 — Orchestration Backbone (Metalayer)

> Carve-out from `Orchestration/PRDs/PRD.md` Phase 1. Where the master PRD bundles
> the metalayer with concrete Swan tools and a `transaction_booked.yaml` pipeline,
> PRD1 ships **only the metalayer**: pipeline DSL, DAG executor, registries,
> SQLite-backed run store, decision/audit recording, cross-run node cache, and
> per-employee AI-credit cost ledger.

---

## 0. Source-Document Map (read these, do not paste them into context)

The next Claude implementing PRD1 should open these on demand, not all at once:

| Topic | File | Anchor |
|---|---|---|
| Master PRD, Phase 1 scope verbatim | `Orchestration/PRDs/PRD.md` | `:661-683` |
| Master PRD, orchestration.db schema | `Orchestration/PRDs/PRD.md` | `:260-299` |
| Master PRD, accounting.db schema (the seam) | `Orchestration/PRDs/PRD.md` | `:301-369` |
| Master PRD, hard invariants | `Orchestration/PRDs/PRD.md` | `:372-376` |
| SQLite backbone — schema details, WAL, migrations | `Dev orchestration/tech framework/REF-SQLITE-BACKBONE.md` | `:207-291`, `:347-360`, `:525-546`, `:600-688`, `:713-736` |
| Google ADK — runner / event stream / per-node pattern | `Dev orchestration/tech framework/REF-GOOGLE-ADK.md` | `:33-51`, `:184-249`, `:259-275`, `:301-327` |
| Anthropic SDK — token accounting + decision_traces shape | `Orchestration/research/ANTHROPIC_SDK_STACK_REFERENCE.md` | `:432-476`, `:551-565`, `:1087-1107` |
| Cerebras — orchestration decision matrix (ADK vs Pydantic AI vs raw) | `Orchestration/research/CEREBRAS_STACK_REFERENCE.md` | `:357-368`, `:826-840` |
| Example downstream pipeline shape (B2B accounting) | `Dev orchestration/_exports_for_b2b_accounting/01_ORCHESTRATION_REFERENCE.md` | whole |
| Example downstream pipeline shape (Swan banking) | `Dev orchestration/swan/SWAN_API_REFERENCE.md` | whole |

---

## 1. Executive Summary

The product needs a **runtime-agnostic agent orchestration metalayer** that turns
agent pipelines into **data, not code**. New event types, new policies, and new
report templates ship as YAML + a registered Python tool — never a redeploy
(`PRD.md:14`).

PRD1 is the **scaffolding only**. It defines:

1. A **YAML pipeline DSL** with named-condition guards.
2. A **DAG executor** that parses YAML into a topologically-layered DAG and runs
   each layer concurrently with `asyncio.gather`.
3. A **tool registry**, **agent-runtime registry**, and **named-condition
   registry** — all populated by `module.path:symbol` strings, no decorators
   that scan the filesystem at import time.
4. A **SQLite orchestration store** (`orchestration.db`) capturing every run,
   every node start/skip/complete/fail, every external event, and every
   model decision in append-only form.
5. A **cross-run node cache** so deterministic lookups (e.g., IBAN→counterparty)
   are remembered between pipeline runs.
6. A **per-employee AI-credit ledger** linking each LLM call's `cost_usd` to an
   `employee_id`, queryable as plain SQL ("how much did we pay Anthropic this
   month, broken down by employee?").

It does **not** ship Swan integration, GL booking, the matching pipeline, the
frontend, replay, or human-in-the-loop gates. Those layer on top.

**MVP goal:** the next engineer can drop a new YAML file in `pipelines/`,
register one Python tool, and watch the executor pick it up — with every
decision durably recorded and every repeat lookup served from cache.

---

## 2. Mission

Make the agent layer **inspectable, replayable-by-design, and cheap by default**:
every decision is a queryable row, every deterministic computation is cached,
every LLM call carries a price tag tied to an employee.

**Core principles** (all carry forward from `PRD.md:14-32`):

1. **Pipelines are data.** Adding `card.created` is "write
   `card_lifecycle.yaml`, register one tool, edit one line in `routing.yaml`,
   zero LOC changes to ingress" (`PRD.md:683`).
2. **Append-only, idempotent.** Every external event lands in an immutable table
   keyed on the provider's event ID (`PRD.md:31`).
3. **Deterministic before AI.** Rules, exact matches, and cache hits before the
   LLM. AI never does arithmetic, never bypasses the confidence gate
   (`PRD.md:28`).
4. **Decision trace is non-negotiable.** Every meaningful node output carries a
   structured trace row showing source, model, prompt hash, alternatives, and
   confidence (`PRD.md:29`).
5. **No floats.** Money is integer cents; cost is integer micro-USD
   (see §6 schema).

---

## 3. Target Users

PRD1's **users are other Claude/Codex instances and one human implementor**.
The frontend user (the auditor reviewing decisions) is downstream — PRD1 must
produce data that a frontend *can* render, but ships no UI.

| Persona | Need PRD1 must satisfy |
|---|---|
| Backend engineer adding a new pipeline | Drop a YAML, register a tool, no executor edits |
| Auditor (downstream, frontend) | Every node output, every model call, every tool input/output is queryable |
| Finance/ops (downstream) | `SELECT employee_id, SUM(cost_micro_usd) FROM agent_costs WHERE month=...` works on day one |
| Future Claude implementing Phase 2 (Swan, GL, frontend) | Stable schema, stable Python interfaces, no need to refactor the executor |

---

## 4. MVP Scope

### ✅ In Scope

**Core metalayer**
- ✅ YAML pipeline DSL: nodes, depends_on, when (named condition), tool/agent ref
- ✅ DAG parser using Kahn's algorithm; reject cycles at parse time (`PRD.md:169`)
- ✅ DAG executor: per-layer `asyncio.gather`, fail-fast within a run
- ✅ `AgnesContext` dataclass: run_id, pipeline_name, db handles, employee_id, node_outputs, trigger_payload
- ✅ Tool registry (`_TOOL_REGISTRY: dict[str, str]`, lazy import — see `PRD.md:170`)
- ✅ Agent-runtime registry (pluggable — see Q3 decision §7)
- ✅ Named-condition registry (`when:` references a Python `def cond(ctx) -> bool`, never an expression string — `PRD.md:173`)

**Persistence**
- ✅ `orchestration.db` with WAL, `foreign_keys=ON`, `busy_timeout=5000`,
      `BEGIN IMMEDIATE` for writes (see REF-SQLITE-BACKBONE.md:207-291)
- ✅ Tables: `pipeline_runs`, `pipeline_events`, `external_events`,
      `node_cache`, `agent_decisions`, `agent_costs`, `employees`,
      `_migrations` (full DDL in §6)
- ✅ Migrations runner with `_migrations` table from day one
      (REF-SQLITE-BACKBONE.md:600-655)
- ✅ Per-DB `asyncio.Lock` for single-writer discipline
      (REF-SQLITE-BACKBONE.md:273-291, "Shape A")

**Cross-run node cache**
- ✅ Content-addressed key: `sha256(node_id + code_version + canonical(input))`
- ✅ Cache hit short-circuits node execution; cache hit is itself recorded
      in `pipeline_events` (so audit trail is unbroken)
- ✅ Tools mark themselves `cacheable: bool` at registration time;
      LLM agents are **not** cached cross-run by default
- ✅ Cache table has `created_at`, `last_hit_at`, `hit_count`; no eviction
      in PRD1 (defer to PRD2)

**Audit / decision recording**
- ✅ Every model call appends a row to `agent_decisions` with
      `(run_id, node_id, source, model, prompt_hash, alternatives_json,
        confidence, response_id, started_at, completed_at)`
      (shape from ANTHROPIC_SDK_STACK_REFERENCE.md:1087-1107)
- ✅ Every tool call appends a `node_started` and `node_completed`
      event to `pipeline_events` with input + output JSON
- ✅ All rows are **append-only**; no UPDATE except setting
      `pipeline_runs.status` and `completed_at`

**Cost / employee ledger**
- ✅ `employees` table: `id, email, swan_iban, swan_account_id,
      manager_employee_id, created_at` — minimal columns enabling
      "match employee → Swan IBAN" downstream
- ✅ `agent_costs` table: per-decision row with
      `(decision_id, employee_id, provider, model, input_tokens,
        output_tokens, cache_read_tokens, cache_write_tokens,
        cost_micro_usd, created_at)`
- ✅ A monthly rollup query is documented (no view shipped yet)

**Accounting-DB seam (thin only)**
- ✅ `accounting.db` opened with same WAL + FK pragmas
- ✅ Empty `_migrations` + a placeholder `decision_traces` table that
      mirrors the master PRD's schema (`PRD.md:301-369`) so PRD2 can fill it
- ✅ `agent_decisions.line_id_logical TEXT` is the cross-DB seam
      (logical FK, not enforced — see §6 note)

### ❌ Out of Scope

**Domain & integrations**
- ❌ Swan webhooks, OAuth client, GraphQL queries (`PRD.md` §7-8 — Phase 2)
- ❌ Counterparty resolver, journal-entry builder, balance invariant tools
      (named in `PRD.md:678` but Swan-specific)
- ❌ Any concrete pipeline YAML beyond a `noop_demo.yaml` test fixture
- ❌ Anthropic SDK calls hardcoded into the executor — runtime is pluggable

**Runtime features**
- ❌ Pipeline replay (`replay_pipeline(run_id)`) — REF-SQLITE-BACKBONE:720-726
      shows it's cheap once schema is right; defer to PRD2
- ❌ Human-in-the-loop confidence gate / `decision_pending` polling
      (ANTHROPIC_SDK_STACK_REFERENCE.md:789-858) — PRD3+
- ❌ Cache eviction policies (TTL, LRU)
- ❌ Postgres / multi-tenant hardening (`PRD.md:96`)
- ❌ FTS5 over audit events (defer per REF-SQLITE-BACKBONE.md:594)

**Operability**
- ❌ Frontend / dashboard / SSE stream
- ❌ Dead-letter UI, retry dashboard
- ❌ Budget envelopes / pre-execution budget enforcement
      (PRD1 only **records** cost; enforcement = Phase 2)
- ❌ LangGraph integration (`PRD.md:96`)

---

## 5. User Stories

> The "user" of PRD1 is mostly an engineer or another Claude. Stories reflect that.

1. **As a backend engineer**, I want to add a new pipeline by writing one YAML
   file and registering one Python tool in `tools/__init__.py`, so that I never
   touch the executor or webhook ingress.
   - *Example:* `pipelines/echo.yaml` with one node `echo` referencing
     `tools.echo:run`. Drop the file, restart, hit a generic trigger endpoint
     (out of scope for PRD1, but the executor is callable from a `pytest`
     test or a REPL).

2. **As an auditor**, I want every model decision to land in `agent_decisions`
   with the prompt hash, alternatives, confidence, and `cost_micro_usd`, so that
   I can later answer "why did the agent choose X for run 4271?" with a single
   `SELECT`.
   - *Example:* `SELECT alternatives_json, confidence FROM agent_decisions
     WHERE run_id = 4271 AND node_id = 'classify_invoice';`

3. **As finance**, I want every model call linked to the employee whose action
   triggered it, so that "Anthropic billed us $3,712 in March" can be split
   per-employee in SQL.
   - *Example:* `SELECT employee_id, SUM(cost_micro_usd)/1e6 AS usd
     FROM agent_costs WHERE strftime('%Y-%m', created_at) = '2026-03'
     GROUP BY employee_id ORDER BY usd DESC;`

4. **As a future Claude implementing Swan**, I want a stable
   `pipeline_runs.id → agent_decisions → line_id_logical` chain, so that when
   I add `journal_lines` in `accounting.db` I can join the two DBs without
   re-shaping anything.

5. **As a backend engineer**, I want a deterministic node (e.g., "look up VAT
   number → counterparty") to execute exactly once per unique input, even
   across thousands of replays of similar webhooks, so that I'm not paying
   for repeated work.
   - *Example:* `node_cache` keyed on
     `sha256("resolve_iban_to_supplier" + "v1" + canonical(input))`.

6. **As a backend engineer**, I want pipeline branching to use named
   conditions (Python functions), not stringly-typed expressions, so that
   conditions are unit-testable and grep-able (`PRD.md:173`).

7. **As an operator**, I want every external event to land in
   `external_events` keyed on the provider's event ID before any pipeline
   runs, so that duplicates are no-ops and out-of-order delivery is harmless
   (`PRD.md:31-32`).

8. **As an engineer adding a new agent runtime** (e.g., wanting Pydantic AI
   instead of raw Anthropic SDK for a single node), I want to register a
   second `AgentRunner` implementation without touching the executor, so that
   runtime choice is per-node (see §7 Q3 decision).

---

## 6. Core Architecture & Data Model

### 6.1 Directory layout

```
orchestration/
  __init__.py
  context.py              # AgnesContext dataclass
  dag.py                  # Kahn parser; topo layers; cycle detection
  executor.py             # async layer-by-layer runner
  registries.py           # _TOOL_REGISTRY, _CONDITION_REGISTRY, _RUNNER_REGISTRY
  cache.py                # cross-run node cache (read + write)
  store/
    __init__.py
    bootstrap.py          # opens both DBs with PRAGMAs
    schema/
      orchestration.sql   # canonical shape — bootstrap-only
      accounting.sql      # placeholder seam, see §6.4
    migrations/
      0001_init.py
      ...                 # one file per migration; idempotent
    writes.py             # async-locked single-writer helpers
  runners/
    base.py               # AgentRunner Protocol
    anthropic_runner.py   # default — raw AsyncAnthropic
    adk_runner.py         # optional — InMemoryRunner per call
    pydantic_ai_runner.py # optional
  cost.py                 # token → micro_usd helper, per-provider table
  pipelines/              # *.yaml, plus noop_demo.yaml fixture
  tools/                  # demo no-op tool only in PRD1
  conditions/             # demo conditions only in PRD1
  tests/
    test_dag.py
    test_executor.py
    test_cache.py
    test_audit.py
    test_cost.py
    test_employee_ledger.py
```

### 6.2 YAML pipeline DSL

```yaml
name: echo
version: 1
trigger:
  source: manual          # manual | external_event:<event_type>
nodes:
  - id: echo
    tool: tools.echo:run  # registry key resolved lazily
    cacheable: true
  - id: maybe_log
    depends_on: [echo]
    when: conditions.echo:has_payload   # named condition, not an expression
    tool: tools.log:run
    cacheable: false
```

**Validation rules** (enforced at load time):
- All `tool:` / `agent:` references must resolve in their registries.
- All `when:` references must resolve in `_CONDITION_REGISTRY`.
- `depends_on` graph must be a DAG (Kahn rejects cycles).
- Unknown keys → fail loudly. Versioning via the `version:` integer.

DSL conventions follow `Dev orchestration/_exports_for_b2b_accounting/02_YAML_WORKFLOW_DSL.md`
(open this file when implementing the parser; do not load into context until then).

### 6.3 `AgnesContext`

```python
@dataclass
class AgnesContext:
    run_id: int
    pipeline_name: str
    pipeline_version: int
    employee_id: int | None         # who triggered the run
    trigger_payload: dict
    node_outputs: dict[str, Any]    # populated as nodes complete
    orchestration_db: aiosqlite.Connection
    accounting_db: aiosqlite.Connection
    write_locks: dict[str, asyncio.Lock]  # one per DB
```

Pattern matches REF-GOOGLE-ADK.md:259-275 (`PipelineContext` propagated
through every layer, never stored in agent session state).

### 6.4 SQLite schema — `orchestration.db`

> Lifted and extended from `PRD.md:260-299`. Table names match the master PRD
> where they exist; new tables (`node_cache`, `agent_decisions`, `agent_costs`,
> `employees`, `external_events`) are net-new for PRD1.

```sql
-- Pipeline runs (matches PRD.md:263-275)
CREATE TABLE pipeline_runs (
    id                INTEGER PRIMARY KEY,
    pipeline_name     TEXT NOT NULL,
    pipeline_version  INTEGER NOT NULL,
    trigger_source    TEXT NOT NULL,           -- 'manual' | 'external_event:<type>'
    trigger_payload   TEXT NOT NULL,           -- JSON
    employee_id       INTEGER,                 -- nullable; logical FK to employees.id
    status            TEXT NOT NULL,           -- 'running' | 'completed' | 'failed'
    error             TEXT,
    started_at        TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    completed_at      TEXT,
    metadata          TEXT,                    -- JSON
    CHECK (json_valid(trigger_payload)),
    CHECK (metadata IS NULL OR json_valid(metadata))
) STRICT;
CREATE INDEX idx_runs_pipeline ON pipeline_runs(pipeline_name, started_at);
CREATE INDEX idx_runs_employee ON pipeline_runs(employee_id);

-- Append-only event log (matches PRD.md:277-289)
CREATE TABLE pipeline_events (
    id          INTEGER PRIMARY KEY,
    run_id      INTEGER NOT NULL REFERENCES pipeline_runs(id),
    event_type  TEXT NOT NULL,                 -- 'pipeline_started' | 'node_started'
                                               -- | 'node_completed' | 'node_skipped'
                                               -- | 'node_failed' | 'cache_hit'
                                               -- | 'pipeline_completed' | 'pipeline_failed'
    node_id     TEXT,
    data        TEXT NOT NULL,                 -- JSON: input, output, error
    elapsed_ms  INTEGER,
    created_at  TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CHECK (json_valid(data))
) STRICT;
CREATE INDEX idx_events_run  ON pipeline_events(run_id, created_at);
CREATE INDEX idx_events_type ON pipeline_events(event_type, created_at);
-- VIRTUAL generated column for indexed JSON drill-back (REF-SQLITE-BACKBONE.md:568-574):
ALTER TABLE pipeline_events
  ADD COLUMN node_name TEXT
  GENERATED ALWAYS AS (data ->> '$.node_name') VIRTUAL;
CREATE INDEX idx_events_node_name ON pipeline_events(node_name);

-- External events (provider-id idempotency; PRD.md:291-299, REF-SQLITE-BACKBONE.md:347-360)
CREATE TABLE external_events (
    id           INTEGER PRIMARY KEY,
    provider     TEXT NOT NULL,                -- 'swan' | 'stripe' | ...
    event_id     TEXT NOT NULL,                -- provider's event id
    event_type   TEXT NOT NULL,
    resource_id  TEXT,
    payload      TEXT NOT NULL,                -- raw envelope
    processed    INTEGER NOT NULL DEFAULT 0,
    created_at   TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (provider, event_id),
    CHECK (json_valid(payload))
) STRICT;
CREATE INDEX idx_ext_events_unprocessed ON external_events(processed) WHERE processed = 0;

-- Cross-run node cache (NEW in PRD1)
CREATE TABLE node_cache (
    cache_key      TEXT PRIMARY KEY,            -- sha256(node_id + version + canonical(input))
    node_id        TEXT NOT NULL,
    pipeline_name  TEXT NOT NULL,
    code_version   TEXT NOT NULL,               -- bumped by tool author when semantics change
    input_json     TEXT NOT NULL,               -- canonicalized
    output_json    TEXT NOT NULL,
    created_at     TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_hit_at    TEXT,
    hit_count      INTEGER NOT NULL DEFAULT 0,
    CHECK (json_valid(input_json) AND json_valid(output_json))
) STRICT;
CREATE INDEX idx_cache_node ON node_cache(node_id, code_version);

-- Per-decision audit row (NEW; merges Anthropic and Cerebras research schemas:
-- ANTHROPIC_SDK_STACK_REFERENCE.md:1087-1107, CEREBRAS_STACK_REFERENCE.md:378-405)
CREATE TABLE agent_decisions (
    id                INTEGER PRIMARY KEY,
    run_id            INTEGER NOT NULL REFERENCES pipeline_runs(id),
    node_id           TEXT NOT NULL,
    source            TEXT NOT NULL,            -- 'agent' | 'rule' | 'cache' | 'human'
    runner            TEXT NOT NULL,            -- 'anthropic' | 'adk' | 'pydantic_ai'
    model             TEXT,                     -- e.g. 'claude-sonnet-4-6'
    response_id       TEXT,                     -- provider response id (replay anchor)
    prompt_hash       TEXT,                     -- sha256(canonical(system+tools+messages))
    alternatives_json TEXT,                     -- JSON array of {label, score}
    confidence        REAL,                     -- 0..1
    line_id_logical   TEXT,                     -- cross-DB seam → accounting.journal_lines.id
    started_at        TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    completed_at      TEXT,
    CHECK (alternatives_json IS NULL OR json_valid(alternatives_json)),
    CHECK (confidence IS NULL OR (confidence >= 0 AND confidence <= 1))
) STRICT;
CREATE INDEX idx_decisions_run     ON agent_decisions(run_id);
CREATE INDEX idx_decisions_line    ON agent_decisions(line_id_logical);
CREATE INDEX idx_decisions_runner  ON agent_decisions(runner, model);

-- Per-decision cost (NEW; one-to-one with agent_decisions)
CREATE TABLE agent_costs (
    decision_id              INTEGER PRIMARY KEY REFERENCES agent_decisions(id),
    employee_id              INTEGER REFERENCES employees(id),
    provider                 TEXT NOT NULL,     -- 'anthropic' | 'cerebras' | 'openai'
    model                    TEXT NOT NULL,
    input_tokens             INTEGER NOT NULL DEFAULT 0,
    output_tokens            INTEGER NOT NULL DEFAULT 0,
    cache_read_tokens        INTEGER NOT NULL DEFAULT 0,
    cache_write_tokens       INTEGER NOT NULL DEFAULT 0,
    reasoning_tokens         INTEGER NOT NULL DEFAULT 0,  -- Cerebras GPT-OSS, etc.
    cost_micro_usd           INTEGER NOT NULL,            -- integer micro-USD; no floats
    created_at               TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
) STRICT;
CREATE INDEX idx_costs_employee_month
    ON agent_costs(employee_id, created_at);
CREATE INDEX idx_costs_provider_month
    ON agent_costs(provider, created_at);

-- Employees (NEW — minimal seam for matching to Swan IBAN)
CREATE TABLE employees (
    id                  INTEGER PRIMARY KEY,
    email               TEXT NOT NULL UNIQUE,
    full_name           TEXT,
    swan_iban           TEXT UNIQUE,            -- nullable until Swan-onboarded
    swan_account_id     TEXT UNIQUE,            -- Swan's accountId (resolved from IBAN)
    manager_employee_id INTEGER REFERENCES employees(id),
    active              INTEGER NOT NULL DEFAULT 1,
    created_at          TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
) STRICT;
CREATE INDEX idx_employees_manager ON employees(manager_employee_id);

-- Migrations bookkeeping (REF-SQLITE-BACKBONE.md:614-655)
CREATE TABLE _migrations (
    id          INTEGER PRIMARY KEY,
    name        TEXT NOT NULL UNIQUE,
    applied_at  TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
) STRICT;
```

**Cross-DB note.** `agent_decisions.line_id_logical` is a **logical** FK
(string). SQLite cannot enforce FKs across attached DBs. The frontend / Phase 2
join is `agent_decisions.line_id_logical = CAST(accounting.journal_lines.id AS TEXT)`.
Choosing TEXT here is intentional: it lets line_id_logical also reference
non-line entities (e.g., a counterparty match) for non-booking pipelines.

### 6.5 SQLite schema — `accounting.db` (thin seam)

PRD1 ships only enough of `accounting.db` for `accounting_db` handles to open
and for migrations to run cleanly:

```sql
CREATE TABLE _migrations (id INTEGER PRIMARY KEY, name TEXT UNIQUE,
                          applied_at TEXT DEFAULT CURRENT_TIMESTAMP) STRICT;

-- Placeholder; PRD2 fills the body per PRD.md:301-369.
CREATE TABLE journal_lines (
    id INTEGER PRIMARY KEY,
    placeholder TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
) STRICT;
```

Rationale: closing the seam *now* means PRD2 ships a migration that fills the
body, not a fresh DB.

### 6.6 Concurrency & write discipline

Inherited verbatim from REF-SQLITE-BACKBONE.md:207-291 ("Shape A"):

```python
async with ctx.write_locks["orchestration"]:
    await ctx.orchestration_db.execute("BEGIN IMMEDIATE")
    # ... writes ...
    await ctx.orchestration_db.commit()
```

PRAGMAs at connection open:

```sql
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;
PRAGMA busy_timeout = 5000;
PRAGMA wal_autocheckpoint = 1000;
PRAGMA journal_size_limit = 67108864;   -- 64 MB
```

---

## 7. Tools / Features (the metalayer's APIs)

### 7.1 DAG Executor

**Purpose.** Turn a parsed YAML pipeline into a sequence of asyncio "layers" and
run each layer concurrently, fail-fast.

**Operations.**
- `parse(path) -> Pipeline` — Kahn topological sort; cycle → `PipelineLoadError`.
- `execute(pipeline, trigger_payload, employee_id) -> int` — returns `run_id`.
- `_run_node(ctx, node)` — registry lookup, cache check, runner dispatch,
  event emission.

**Invariants.**
- Every node start emits a `node_started` event before tool/agent dispatch.
- Every node end emits exactly one of `node_completed`, `node_skipped`,
  `node_failed`, or `cache_hit`.
- Within a layer, nodes run via `asyncio.gather(..., return_exceptions=True)`;
  the first exception triggers `pipeline_failed` and cancels the rest of the run.
- Cache hits are recorded as `cache_hit` events with the original
  `output_json` echoed.

### 7.2 Tool Registry

```python
_TOOL_REGISTRY: dict[str, ToolSpec] = {}
# ToolSpec:  module_path: str, function_name: str, cacheable: bool,
#            code_version: str
```

Lazy-import on first call (`PRD.md:170`). Decorator-free: tools register
themselves by appending to a dict in `tools/__init__.py`.

### 7.3 Named-Condition Registry

```python
_CONDITION_REGISTRY: dict[str, Callable[[AgnesContext], bool]] = {}
```

`when:` strings in YAML resolve here. Conditions must be **pure** and
**unit-tested** (`PRD.md:173`).

### 7.4 Agent-Runtime Registry (decision on Q3)

```python
class AgentRunner(Protocol):
    name: ClassVar[str]
    async def run(
        self,
        ctx: AgnesContext,
        node_id: str,
        spec: AgentSpec,
    ) -> AgentResult: ...

_RUNNER_REGISTRY: dict[str, AgentRunner] = {}
# Three implementations ship in PRD1:
#   - "anthropic"     — raw AsyncAnthropic (default; ANTHROPIC_SDK_STACK_REFERENCE.md)
#   - "adk"           — Google ADK InMemoryRunner per call (REF-GOOGLE-ADK.md:184-249)
#   - "pydantic_ai"   — Pydantic AI agent (CEREBRAS_STACK_REFERENCE.md:410-535)
```

`AgentResult` carries `(output, model, response_id, prompt_hash,
alternatives, confidence, usage)` — enough to populate one
`agent_decisions` + `agent_costs` row pair.

YAML picks the runtime per node:

```yaml
- id: classify
  agent: agents.invoice_classifier
  runner: pydantic_ai      # default if omitted: "anthropic"
```

The executor never imports a specific SDK directly — it dispatches via
`_RUNNER_REGISTRY[spec.runner]`.

### 7.5 Cross-Run Node Cache

**Cache key.**

```python
def cache_key(node_id: str, code_version: str, input_payload: dict) -> str:
    canonical = json.dumps(input_payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(
        f"{node_id}|{code_version}|{canonical}".encode()
    ).hexdigest()
```

**Read path.** Before running a `cacheable: true` tool, the executor checks
`node_cache`. Hit → emit `cache_hit` event with `output_json` echoed,
populate `ctx.node_outputs[node_id]`, increment `hit_count`, update
`last_hit_at`, skip dispatch.

**Write path.** On node completion (non-cached, no error), write a
`node_cache` row. Single-writer lock; `INSERT OR IGNORE` on key collision
(idempotent under retry).

**What is NOT cached.** Agent runs (LLM calls) are never cached cross-run by
default — they're already cached within-call by Anthropic's 5-min ephemeral
cache (ANTHROPIC_SDK_STACK_REFERENCE.md:432-476), and cross-run replay would
be unsafe without a richer policy. Tools that wrap LLMs can opt in via
`cacheable: true` if the author understands the determinism risk.

### 7.6 Audit / Decision Recording

For every model call:

1. Compute `prompt_hash = sha256(canonical(system + tools + messages))` —
   shape per ANTHROPIC_SDK_STACK_REFERENCE.md:432-476 (so cache-hit verification
   later is a free byproduct).
2. Insert `agent_decisions` row.
3. Insert `agent_costs` row using the per-provider cost helper (§7.7).
4. Append a `node_completed` event to `pipeline_events` with
   `{decision_id, model, runner, confidence}` in `data`.

For every tool call: only step 4, with `data = {input, output}` (capped to
~64 KiB; oversize values land as `{"$truncated": true, "size_bytes": N}` —
PRD2 can move them to a blob store).

### 7.7 Cost Helper

```python
COST_TABLE_MICRO_USD: dict[tuple[str, str], dict[str, int]] = {
    # (provider, model) -> {input, output, cache_read, cache_write}
    ("anthropic", "claude-sonnet-4-6"): {"input": 3000, "output": 15000,
                                          "cache_read": 300, "cache_write": 3750},
    ("anthropic", "claude-haiku-4-5"):  {"input":  800, "output":  4000,
                                          "cache_read":  80, "cache_write":  1000},
    ("cerebras", "llama3.3-70b"):       {"input":  600, "output":   600,
                                          "cache_read": 600, "cache_write":  600},
    # ... extend per CEREBRAS_STACK_REFERENCE.md:357-368
}
# Numbers are micro-USD per million tokens (so 3000 = $3.00 / 1M tokens).

def micro_usd(usage: TokenUsage, provider: str, model: str) -> int:
    rates = COST_TABLE_MICRO_USD[(provider, model)]
    return (
        usage.input_tokens       * rates["input"]
      + usage.output_tokens      * rates["output"]
      + usage.cache_read_tokens  * rates["cache_read"]
      + usage.cache_write_tokens * rates["cache_write"]
    ) // 1_000_000
```

### 7.8 Employee Ledger

`employees` is populated out-of-band (CSV import, frontend later). PRD1
guarantees that `pipeline_runs.employee_id` and `agent_costs.employee_id` are
populated whenever the executor was invoked with a known employee — the
executor takes `employee_id` as a parameter; if `None`, both columns are
NULL but execution still proceeds.

**The wedge query** (the per-employee AI-credit demo):

```sql
SELECT
    e.email,
    e.full_name,
    COUNT(*)                          AS call_count,
    SUM(c.cost_micro_usd) / 1000000.0 AS usd_this_month
FROM agent_costs c
JOIN employees   e ON e.id = c.employee_id
WHERE strftime('%Y-%m', c.created_at) = strftime('%Y-%m', 'now')
GROUP BY e.id
ORDER BY usd_this_month DESC;
```

A second query joins to a Swan-IBAN match (provided here for documentation;
the Swan side ships in Phase 2):

```sql
-- "Match the Anthropic invoice line for $3,712.40 to per-employee buckets."
SELECT e.email, e.swan_iban, SUM(c.cost_micro_usd) / 1000000.0 AS usd
FROM agent_costs c
JOIN employees   e ON e.id = c.employee_id
WHERE c.provider = 'anthropic'
  AND strftime('%Y-%m', c.created_at) = '2026-03'
GROUP BY e.id;
```

---

## 8. Technology Stack

| Layer | Choice | Source / rationale |
|---|---|---|
| Language | Python 3.12 async | matches `PRD.md` |
| DB | SQLite (WAL) | `PRD.md:56`, REF-SQLITE-BACKBONE.md:207 |
| SQLite driver | `aiosqlite` | async parity with executor |
| Pipeline DSL | YAML + named conditions | `PRD.md:173`, B2B export DSL |
| Default agent runtime | raw `AsyncAnthropic` SDK | ANTHROPIC_SDK_STACK_REFERENCE.md |
| Optional agent runtimes | Google ADK, Pydantic AI | REF-GOOGLE-ADK.md, CEREBRAS_STACK_REFERENCE.md:826-840 |
| YAML loader | `pyyaml` (safe_load) | std |
| Hashing | `hashlib.sha256` | std |
| Tests | `pytest` + `pytest-asyncio` | std |
| Migrations | hand-rolled, `_migrations` table | REF-SQLITE-BACKBONE.md:600-655 |

**Dependencies (concrete):**

```
aiosqlite>=0.19
pyyaml>=6
anthropic>=0.40
google-adk>=0.5      # optional extra
pydantic-ai>=0.1     # optional extra
pytest>=8
pytest-asyncio>=0.23
```

ADK and Pydantic AI live behind optional extras: `pip install '.[adk]'`,
`pip install '.[pydantic-ai]'`. The executor imports them lazily so a
deployment can ship without either.

---

## 9. Security & Configuration

**In scope.**
- `.env.local` for `ANTHROPIC_API_KEY`, `CEREBRAS_API_KEY` (optional),
  `GOOGLE_API_KEY` (optional). Loader rejects on startup if a runner is
  registered without its key present.
- DBs live under a configurable `AGNES_DATA_DIR` (default `./data/`).
- `external_events.payload` may contain PII; PRD1 stores it raw — frontend
  redaction is downstream.

**Out of scope.**
- Multi-tenant isolation, row-level security, encrypted-at-rest SQLite — see
  `PRD.md:96`. Single-tenant SQLite only.
- Auth on any HTTP surface — PRD1 has no HTTP surface beyond a test fixture.

---

## 10. Public Interfaces (no HTTP API in PRD1)

PRD1 ships a **library + CLI**, not a web service. The HTTP / SSE layer is
Phase 2.

```python
# Library entry points
from orchestration.executor import execute_pipeline
from orchestration.store.bootstrap import open_dbs

async with open_dbs(data_dir="./data/") as dbs:
    run_id = await execute_pipeline(
        pipeline_name="echo",
        trigger_payload={"hello": "world"},
        employee_id=42,
        dbs=dbs,
    )
```

```bash
# CLI (thin click/argparse wrapper)
agnes run echo --payload '{"hello": "world"}' --employee 42
agnes runs list --pipeline echo --limit 20
agnes events show <run_id>
agnes cache stats
agnes costs month --month 2026-03
```

---

## 11. Success Criteria

**MVP success = all of the following pass:**

- ✅ A `noop_demo.yaml` pipeline with two nodes (one cacheable, one
  conditional) parses, executes, and produces a `pipeline_runs` row + the
  expected sequence of `pipeline_events` rows.
- ✅ Running the same pipeline twice with the same input produces a `cache_hit`
  on the cacheable node on run 2; `node_cache.hit_count = 1`.
- ✅ A node failure produces exactly one `node_failed` and one
  `pipeline_failed` event; sibling nodes in the same layer are cancelled and
  no `node_completed` events leak.
- ✅ A duplicate `external_events` insert (same `provider, event_id`) is a
  no-op; both runs (if both fire) share a single source-of-truth row.
- ✅ A test that registers all three runners (anthropic, adk, pydantic_ai)
  with stub clients and runs three single-node pipelines, asserting that
  `agent_decisions.runner` is correctly populated for each.
- ✅ Cost recording: a stubbed agent run with a known `usage` produces an
  `agent_costs` row whose `cost_micro_usd` matches `cost.micro_usd(usage)`.
- ✅ Employee linking: a run started with `employee_id=42` produces
  `pipeline_runs.employee_id = 42` and every `agent_costs` row produced by
  that run has `employee_id = 42`.
- ✅ The wedge query in §7.8 runs against the test DB and returns one row
  per known employee, sorted by USD descending.
- ✅ Adding a new YAML pipeline + registering one tool requires zero edits
  to `executor.py`, `dag.py`, `cache.py`, or any registry-internal file.
- ✅ Migrations test: boot a fresh DB from `schema/orchestration.sql`, then
  boot a second DB by replaying every migration from empty, then `diff` the
  resulting schemas — they must be identical (REF-SQLITE-BACKBONE.md:609).

**Quality indicators.**
- All tests run under 30s on a developer laptop.
- `pipeline_events` row count for a 5-node clean run is exactly 12
  (1 pipeline_started + 5×2 node_started/completed + 1 pipeline_completed).
- No `float` types anywhere except as transient computation; all stored
  monetary values are integer micro-USD.

---

## 12. Implementation Phases (within PRD1)

> No calendar — "ship it when correct" (per scoping conversation 2026-04-25).

### Phase 1.A — Schema and store
- ✅ Write `schema/orchestration.sql`, `schema/accounting.sql`
- ✅ Bootstrap opens both DBs with PRAGMAs from §6.6
- ✅ `_migrations` runner; `0001_init.py` is the seed migration
- ✅ Round-trip test: schema-from-bootstrap == schema-from-migration-replay

### Phase 1.B — DSL, parser, registries
- ✅ YAML loader with strict-key rejection
- ✅ Kahn topological sort with cycle detection
- ✅ Three registries (tools, conditions, runners) — empty fixture entries
- ✅ Unit tests for cycle detection and registry resolution failures

### Phase 1.C — Executor (no caching, no audit yet)
- ✅ `AgnesContext` dataclass
- ✅ Layer-by-layer `asyncio.gather`, fail-fast
- ✅ `pipeline_runs` + `pipeline_events` writes via single-writer locks
- ✅ Test: `noop_demo.yaml` produces the expected event sequence

### Phase 1.D — Cross-run cache
- ✅ `node_cache` table + cache_key helper
- ✅ Read-before-dispatch in executor
- ✅ Write-after-success
- ✅ `cache_hit` event emission
- ✅ Test: second run is a hit; hit_count increments

### Phase 1.E — Audit + cost + employees
- ✅ `agent_decisions` writes wired into runner dispatch
- ✅ `agent_costs` writes wired into runner dispatch
- ✅ `employees` table + minimal CRUD helpers (no UI)
- ✅ Cost helper `cost.micro_usd()` with three providers
- ✅ Wedge query test against fixture DB

### Phase 1.F — Three runners
- ✅ `AgentRunner` Protocol; default `anthropic_runner`
- ✅ Optional `adk_runner` behind extras
- ✅ Optional `pydantic_ai_runner` behind extras
- ✅ Each runner returns the same `AgentResult` shape
- ✅ Test: three single-node pipelines, one per runner, all populate
      `agent_decisions.runner` correctly

### Phase 1.G — CLI + docs
- ✅ `agnes` CLI commands from §10
- ✅ A short `README` in `orchestration/` (not a top-level repo doc)

---

## 13. Future Considerations (post-PRD1)

Open `Orchestration/PRDs/PRD.md` for the full Phase 2+ vision; the immediate
PRD1 → PRD2 handoff items:

- **Replay** — schema already supports it (`pipeline_runs` + `pipeline_events`
  + `node_cache`); ship `replay_pipeline(run_id)` from REF-SQLITE-BACKBONE.md:720-726.
- **HITL gate** — add `decision_pending` table + polling tool per
  `ANTHROPIC_SDK_STACK_REFERENCE.md:789-858`.
- **Swan webhook ingress** — `/swan/webhook` endpoint that writes to
  `external_events` and triggers pipelines via a `routing.yaml`.
- **Frontend** — read-only audit dashboard over `pipeline_events`,
  `agent_decisions`, `agent_costs`; the "false reasoning / false tool call"
  review surface the user described in scoping.
- **Budget enforcement** — pre-execution check against an
  `employee_budgets` table; runner dispatch refuses if the run would push
  this month over budget.
- **Cache eviction** — TTL or LRU policy for `node_cache`.
- **FTS5** over `pipeline_events.data` for "find every run where the agent
  considered alternative X".
- **Postgres migration** — once we outgrow SQLite's single-writer ceiling.

---

## 14. Risks & Mitigations

| Risk | Mitigation |
|---|---|
| Cross-DB FK pseudo-FK (`line_id_logical`) drifts as `journal_lines.id` evolves | Document the seam loudly in §6.4; add a CI test in PRD2 that joins the two DBs and asserts no orphaned `line_id_logical` |
| `node_cache` returns stale data when a tool's semantics change without bumping `code_version` | Make `code_version` a required field at registration time; lint test that fails if a tool changes without a version bump in the same commit |
| Three agent runtimes triple the surface area | Optional extras keep them out of the default install; `AgentResult` is the only shared type, kept tiny |
| SQLite write contention under burst load | Per-DB `asyncio.Lock` + `BEGIN IMMEDIATE` (REF-SQLITE-BACKBONE.md:273-291); load test in PRD2, not PRD1 |
| Cost-table micro-USD rates drift from provider list price | Pin the table in `cost.py` with a `# verified <date>` comment; PRD2 adds a monthly recheck task |
| `pipeline_events.data` JSON grows unbounded for chatty agents | 64 KiB cap + truncation marker (§7.6); blob spillover is a PRD2 concern |

---

## 15. Open Questions (carry to next iteration)

1. Do we want a `pipeline_dag_versions` table that snapshots the *parsed* DAG
   (nodes, edges, tool refs) at run time, so an audit can answer "what did the
   DAG actually look like for run 4271?" Currently we only record
   `pipeline_version: int` on the run. Probably yes for PRD2.

2. Should `AgentRunner` implementations be allowed to mutate
   `ctx.node_outputs`, or only return values? PRD1 says "return only" — revisit
   if a runner wants to stream partial results.

3. Is `manager_employee_id` enough, or do we need an `employee_groups` table
   for cost rollups by team? Defer until the frontend asks.

4. Per-employee budget alerts (without enforcement): do we want a passive
   "this employee is on track to spend $X this month" view in PRD1, or strictly
   PRD2? Currently scoped to PRD2.

---

## 16. Appendix — Document Index for the next Claude

When implementing PRD1, open these on demand (do NOT load all into context up
front — total volume is ~6,000 lines across them):

- **Master PRD** — `Orchestration/PRDs/PRD.md` (full spec; PRD1 is its Phase 1
  metalayer carve-out)
- **REF-SQLITE-BACKBONE** — `Dev orchestration/tech framework/REF-SQLITE-BACKBONE.md`
  (load when writing `store/`, migrations, or the cache)
- **REF-GOOGLE-ADK** — `Dev orchestration/tech framework/REF-GOOGLE-ADK.md`
  (load only when implementing `runners/adk_runner.py`)
- **ANTHROPIC_SDK_STACK_REFERENCE** —
  `Orchestration/research/ANTHROPIC_SDK_STACK_REFERENCE.md` (load when
  implementing `runners/anthropic_runner.py`, `cost.py`, prompt-hashing)
- **CEREBRAS_STACK_REFERENCE** —
  `Orchestration/research/CEREBRAS_STACK_REFERENCE.md` (load when implementing
  `runners/pydantic_ai_runner.py` or extending the cost table)
- **B2B accounting export reference** —
  `Dev orchestration/_exports_for_b2b_accounting/01_ORCHESTRATION_REFERENCE.md`,
  `02_YAML_WORKFLOW_DSL.md`, `03_SQLITE_BACKBONE.md`, `04_AGENT_PATTERNS.md`
  (load when designing the YAML DSL, named conditions, or for an example of a
  downstream consumer pipeline — but PRD1 itself does **not** ship any of these
  pipelines)
- **Swan API reference** —
  `Dev orchestration/swan/SWAN_API_REFERENCE.md` (load only when designing the
  `employees.swan_iban` / `swan_account_id` columns; **out of scope** for PRD1
  to actually call Swan)

---

*End of PRD1.*
