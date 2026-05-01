# PRD — Autonomous CFO for SMEs (Hackathon MVP, Meta-Layer Foundation)

**Status:** Draft v1 — 2026-04-25
**Owner:** HEC Paris team (Paris Fintech Hackathon 2026)
**Companion docs:** `pennylane_vs_us.md`, `projectbriefing.md`, `architecure.md`, `Dev orchestration/_exports_for_b2b_accounting/*`, `Dev orchestration/swan/SWAN_API_REFERENCE.md`
**Output target of this PRD:** the meta-layer foundation (SQLite backbone, declarative webhook ingress, agent pipeline runtime, injection pipelines) on which all later demo moments — campaigns, DD pack, budget envelopes — are built.

---

## 1. Executive Summary

**Pennylane gave the accountant a co-pilot. We're giving the SME founder an autonomous CFO.** Same Swan rails, sub-second instead of sub-day, every AI decision auditable down to the prompt.

This PRD scopes the **foundation layer** of that product: a two-database SQLite backbone, a declarative webhook ingress that handles **any** Swan event without hardcoding, a YAML-defined agent pipeline runtime with first-class decision traces, and an injection pipeline pattern that normalizes raw events into a double-entry general ledger. Every higher-order capability described in `pennylane_vs_us.md` (per-employee budget envelopes, goal-driven campaigns, agentic DD/board reports, AI-API spend allocation) compiles down to this foundation.

**MVP goal:** Within 5 seconds of a Swan `Transaction.Booked` webhook firing, the corresponding journal entry is posted to the GL with a decision trace, the affected budget envelope is decremented, and the change is pushed to the UI — driven entirely by a YAML pipeline definition with no event-type-specific Python code in the ingress path.

**Core value proposition:** A meta-layer where webhooks, agent pipelines, and ingestion paths are **data, not code**. New event types, new policies, new report templates ship as YAML + a registered tool — not as a redeploy.

---

## 2. Mission

**Mission statement.** Be the brain — not the bookkeeper — for European SMEs that don't have a CFO. Every euro that moves through a Swan account becomes a journal line, a budget movement, and a decision trace within seconds, with AI that proposes chains of actions a human approves once.

**Core principles**

1. **Deterministic by default, AI surgically.** Rules, exact matches, and cache hits before the LLM. AI never does arithmetic, never produces journal entries directly, never bypasses the confidence gate.
2. **Decision trace is non-negotiable.** Every journal line carries a structured trace row (`source`, `model`, `prompt_hash`, `alternatives`, `confidence`, `approver_id`). Built in, not bolted on.
3. **Meta-layer over hardcoding.** Webhooks, pipelines, conditions, and tools are declarative. Adding a new Swan event type or a new agent pipeline is YAML + one registry line, not a redeploy of ingress logic.
4. **Append-only, idempotent, replayable.** Every external event lands in an immutable table keyed on the provider's event ID. Pipeline runs are reconstructable from `(pipeline_runs, pipeline_events)`.
5. **Integer cents, no floats.** All monetary values stored as integer cents. VAT splits use integer arithmetic with documented rounding. Floats are an outage waiting to happen.

---

## 3. Target Users

**Primary persona — Founder-CFO of a 5–50-person SME.** Not accounting-trained. Owns the bank account, sets the budgets, signs the payments. Today juggles Swan + spreadsheet + Pennylane (or equivalent). Wants answers, not ledgers. Wants to set a goal ("save €15k for the CNC machine by Q3") and see the system reshape itself to hit it.

**Secondary persona — Part-time bookkeeper / external accountant.** Reviews flagged transactions, approves AI proposals at checkpoints, runs the close. Cares about the audit trail. Cares that AI explains itself.

**Tertiary persona — Hackathon judge / B2B fintech investor.** Will probe the agentic claims. Will ask "what does the AI actually *do*?" The architecture has to answer that without hand-waving.

**Technical comfort.** Founder is product-literate but not SQL-literate. Bookkeeper is spreadsheet-literate. Neither writes Python. The product surface is a chat copilot, a live ledger view, and an approval queue.

---

## 4. MVP Scope

### In Scope ✅

**Core functionality**

- ✅ Swan webhook ingress endpoint with signature verification, idempotency keying on `eventId`, sub-10s response budget
- ✅ Declarative event-type → pipeline routing table (YAML), no per-event-type handler code
- ✅ Two-database SQLite backbone: `accounting.db` (domain) + `orchestration.db` (run history)
- ✅ Append-only `swan_events` table; re-query Swan GraphQL on receipt for full state
- ✅ YAML pipeline DSL with topologically ordered nodes, named conditions, tool/agent dispatch
- ✅ Tool registry (string → `module:function`) and agent registry (string → async callable)
- ✅ Decision trace as first-class table, joined to every `journal_line`
- ✅ Confidence cascade: rule lookup → identifier match → fuzzy match → AI fallback, with cache writeback
- ✅ Double-entry invariant check (`SUM(debits) = SUM(credits)`) per entry, per transaction
- ✅ Live SSE stream of pipeline events to the UI
- ✅ Pipeline replay from `run_id` (re-trigger with same payload)
- ✅ One end-to-end demo path: `Transaction.Booked` → classify counterparty → match doc → assign GL account → post entry → decrement envelope → push UI update, all under 5 seconds

**Technical**

- ✅ Python 3.10+ runtime, FastAPI, SQLite WAL, async pipeline executor
- ✅ Anthropic SDK for agents (Claude Sonnet 4.6 default; explicit model pinning per agent)
- ✅ Sandbox-only Swan integration (mocked SCA flows for the demo)
- ✅ Seed dataset that exercises the full path (cards, SEPA in, SEPA out, supplier invoice match, budget decrement)

**Integration**

- ✅ Swan webhook subscription managed programmatically via `addWebhookSubscription`
- ✅ Swan GraphQL re-query of `transaction(id)` and `account(id)` after every event
- ✅ Virtual IBAN issuance for one demo customer, to show deterministic inbound matching

**Deployment**

- ✅ Single container, single host (hackathon). HTTPS via reverse proxy. SSE-stateful host.

### Out of Scope ❌

- ❌ Postgres / multi-tenant production hardening
- ❌ Multi-currency, multi-entity consolidation
- ❌ Production OAuth, SCA browser flows on stage (mocked)
- ❌ Full French PCG chart of accounts (subset only)
- ❌ Receipt OCR for arbitrary supplier invoices (seeded structured data instead)
- ❌ E-invoicing Plateforme Agréée certification (Pennylane has it; we don't compete here)
- ❌ Active treasury actions (sweep, FX) in the foundation PRD — deferred to Phase 4
- ❌ Goal-driven campaigns engine — deferred to Phase 3
- ❌ Agentic DD pack / board pack generators — deferred to Phase 4
- ❌ Per-API-key AI cost allocation — deferred to Phase 3
- ❌ LangGraph integration — explicit non-goal for MVP; revisit only if Anthropic SDK + named checkpoint pattern proves insufficient
- ❌ Dead-letter UI / retry dashboard — log-and-replay-only for MVP

---

## 5. User Stories

### Primary

1. **As a founder**, I want a transaction that hits my Swan account to appear in my live ledger within 5 seconds, **so that** my balance sheet is never out of date when I'm making a decision.
   *Example:* a customer pays €4,950 by SEPA at 14:03:12; by 14:03:16 the GL shows the entry, the AR envelope drops by €4,950, and my phone notification fires.

2. **As a founder**, I want the AI to explain *why* it categorized a transaction the way it did — model used, prompt, alternatives, confidence — **so that** I can trust the books and contest a wrong call.
   *Example:* I tap the journal line; I see "matched counterparty 'Acme SAS' via IBAN exact (confidence 1.0); GL account 706000 via rule R-12 (confidence 1.0)" — no LLM was called.

3. **As a founder**, I want to correct the AI once and have it stick **so that** the same merchant never gets miscategorized again.
   *Example:* I rename "ACM CORP" → "Acme Corp"; the system writes a `counterparty_identifiers` row with `source = 'user'`; next inbound transaction hits the cache, no LLM.

4. **As a bookkeeper**, I want low-confidence entries queued for my review **so that** I see what the AI was unsure about and the books still post the rest.
   *Example:* a novel merchant arrives; classification confidence is 0.62 (below floor); the entry lands in `needs_review` with the trace; the rest of the day's transactions post automatically.

5. **As a developer extending the system**, I want to add a new Swan event type by writing one YAML pipeline and registering one tool **so that** I'm not editing the ingress controller.
   *Example:* `Card.Created` arrives; I drop `pipelines/card_created.yaml` and add `CardLifecycleTool` to the registry; nothing else changes.

6. **As a hackathon judge**, I want every numeric claim in the demo to drill back to a journal line and a decision trace **so that** I can verify the agentic behavior is real, not theater.

7. **As an SRE**, I want every external event keyed on its provider event ID with at-least-once safety **so that** webhook redelivery never doubles a journal entry.

### Technical

8. **As an agent author**, I want to write a Python function with signature `async def run(ctx) -> dict` and have it become a pipeline node **so that** the contract is one rule and the framework handles tracing.

9. **As a pipeline author**, I want named conditions (`when: needs_review`) instead of expression strings **so that** branch logic is testable Python and not stringly-typed YAML.

---

## 6. Core Architecture & Patterns

### High-level architecture

```
┌──────────────────────────────────────────────────────────────────────┐
│  AGENT RUNTIME — planner, tool registry, named conditions, traces    │
│  (Anthropic SDK + thin async DAG executor; HITL via condition gates) │
└──────────────────────────────────────────────────────────────────────┘
        │                     │                       │
        ▼                     ▼                       ▼
 ┌─────────────┐       ┌─────────────┐         ┌─────────────┐
 │ Injection   │       │ Classify &  │         │ Reports &   │
 │ pipelines   │       │ Post (GL)   │         │ Read agents │
 │ (Swan,      │       │ pipelines   │         │ (live Q&A,  │
 │  invoices)  │       │             │         │  later: DD) │
 └─────────────┘       └─────────────┘         └─────────────┘
        │                     │                       │
        └───────────┬─────────┴───────────────────────┘
                    ▼
   ┌──────────────────────────────────────────────────────────┐
   │  DECISION TRACE — first-class table joined to every line │
   │  Every agent action propose→checkpoint→commit through it │
   └──────────────────────────────────────────────────────────┘
                    │
                    ▼
   ┌──────────────────────────────────────────────────────────┐
   │  GL (double-entry)  ←  Budgets (Domain F)  ←  Entities   │
   │       ↑                                          ↑       │
   │  Bank mirror (Swan webhooks)            Documents (PDF)  │
   └──────────────────────────────────────────────────────────┘
```

### Design patterns

- **Two-database split.** `accounting.db` is the canonical domain. `orchestration.db` is the audit/event store. Both run WAL, both have `foreign_keys=ON`. Path injected per pipeline run via `FingentContext`.
- **Append-only event tables.** `swan_events`, `pipeline_runs`, `pipeline_events`. No UPDATE, no DELETE. Replay is reading them back.
- **Topologically-ordered DAG executor.** Kahn's algorithm at parse time; layers run in parallel via `asyncio.gather`; fail-fast within a run.
- **Tool registry over decorators.** `_TOOL_REGISTRY: dict[str, str]` mapping YAML class name → `module.path:run`. Lazy import. No magic.
- **Cache-warmer cascade.** Resolution stages — exact identifier → curated synonym → fuzzy match → LLM fallback. Each LLM resolution writes back to a deterministic cache so the next occurrence skips the LLM.
- **Compound (multiplicative) confidence.** A pipeline's overall confidence is the product, not the average, of its node confidences. Weak links collapse the chain; this is the surfacing mechanism for "needs review."
- **Named conditions.** `when:` references a registered Python function `def cond(ctx) -> bool`; never an expression string. Conditions are unit-tested.

### Directory structure

```
backend/
  api/
    main.py                  # FastAPI app, /swan/webhook, /payment-control, /pipelines/run/{name}
    dag_executor.py          # async DAG runner
    pipeline_loader.py       # YAML → Pipeline dataclass
    agent_registry.py        # _TOOL_REGISTRY, _AGENT_REGISTRY
    conditions.py            # named condition functions
    fingent_context.py         # FingentContext dataclass
  ingress/
    swan_webhook.py          # signature check, idempotent insert, route by event_type
    swan_client.py           # GraphQL re-query layer
    routing.yaml             # event_type → pipeline_name table (THE meta-layer)
  agents/
    counterparty_classifier.py
    gl_account_classifier.py
    document_extractor.py
    copilot_qa.py
  tools/
    counterparty_resolver.py
    gl_journal_writer.py
    invariant_checker.py
    budget_envelope.py
    swan_query.py
  pipelines/
    transaction_booked.yaml
    transaction_released.yaml
    card_created.yaml
    document_ingested.yaml
  schema/
    accounting.sql
    orchestration.sql
    seeds/
      pcg_subset.sql
      demo_seed.sql
  migrations/
    001_init.py
    002_add_decision_trace.py
    ...
frontend/                    # later phase
```

---

## 7. Tools / Features

### 7.1 Meta-layer webhook ingress (the core)

**Purpose.** A single webhook endpoint that handles every Swan event type via a YAML routing table. Adding a new event type does not require code changes in the ingress.

**Operations**

- `POST /swan/webhook` — verify `x-swan-secret` (constant-time compare against subscription secret), reject if invalid.
- Insert into `swan_events` with `INSERT OR IGNORE` keyed on `event_id` — idempotency boundary.
- Look up `event_type` in `ingress/routing.yaml`; resolve to pipeline name(s).
- Enqueue pipeline run with `trigger_payload = {event_id, event_type, resource_id}` via `asyncio.create_task`.
- Return `200` within ~50ms (well under Swan's 10s budget).

**Routing table example (`ingress/routing.yaml`)**

```yaml
routes:
  Transaction.Booked:    [transaction_booked]
  Transaction.Released:  [transaction_released]
  Transaction.Canceled:  [transaction_released]   # same compensation pipeline
  Transaction.Enriched:  [transaction_reclassify]
  Card.Created:          [card_lifecycle]
  Account.Updated:       [reconcile_balance]
defaults:
  unknown_event: [log_and_continue]
```

**Key behaviors**

- **At-least-once tolerant.** Duplicate `event_id` is ignored at insert time.
- **Out-of-order tolerant.** Each pipeline is idempotent on `(event_id, pipeline_version)`.
- **Re-query, don't trust the envelope.** Pipelines must re-fetch `transaction(resource_id)` from Swan; the webhook payload only carries IDs.
- **Two webhook endpoints, two secrets.** `/swan/webhook` (async, 10s budget) is separate from `/swan/payment-control` (synchronous, 1.5s budget). Different secrets in env (`SWAN_WEBHOOK_SECRET`, `SWAN_PAYMENT_CONTROL_SECRET`). Payment-control is **deferred to Phase 3** but the endpoint stub exists from Phase 1 to claim the URL.

### 7.2 SQLite backbone

**Purpose.** Durable, replayable, append-only event store + canonical domain DB.

**`orchestration.db` core tables**

```sql
CREATE TABLE pipeline_runs (
  id              INTEGER PRIMARY KEY,
  pipeline_name   TEXT NOT NULL,
  pipeline_version TEXT NOT NULL,
  trigger_source  TEXT NOT NULL,   -- 'webhook' | 'manual' | 'schedule' | 'chat' | 'data_update'
  trigger_payload TEXT NOT NULL,   -- JSON
  status          TEXT NOT NULL,   -- 'running' | 'completed' | 'failed'
  error           TEXT,
  started_at      TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  completed_at    TIMESTAMP,
  metadata        TEXT
);
CREATE INDEX idx_pipeline_runs_status ON pipeline_runs(status);

CREATE TABLE pipeline_events (
  id          INTEGER PRIMARY KEY,
  run_id      INTEGER NOT NULL REFERENCES pipeline_runs(id),
  event_type  TEXT NOT NULL,        -- pipeline_started | node_started | node_completed | node_skipped | node_failed | pipeline_completed | pipeline_failed
  node_id     TEXT,
  data        TEXT NOT NULL,        -- JSON: input/output/error
  elapsed_ms  INTEGER,
  created_at  TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX idx_pipeline_events_run_id ON pipeline_events(run_id);

CREATE TABLE swan_events (
  id           INTEGER PRIMARY KEY,
  event_id     TEXT NOT NULL UNIQUE,    -- Swan's eventId; idempotency key
  event_type   TEXT NOT NULL,
  project_id   TEXT NOT NULL,
  resource_id  TEXT NOT NULL,
  payload      TEXT NOT NULL,            -- raw envelope
  processed    INTEGER NOT NULL DEFAULT 0,
  created_at   TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX idx_swan_events_unprocessed ON swan_events(processed) WHERE processed = 0;
```

**`accounting.db` core tables (Domains A–F from the architecture doc)**

```sql
-- Domain A: bank mirror
CREATE TABLE swan_transactions ( ... );

-- Domain B: counterparties
CREATE TABLE counterparties (
  id           INTEGER PRIMARY KEY,
  legal_name   TEXT NOT NULL,
  vat_number   TEXT,
  primary_iban TEXT,
  mcc          TEXT,
  confidence   REAL,
  sources      TEXT     -- JSON array
);
CREATE TABLE counterparty_identifiers (
  id              INTEGER PRIMARY KEY,
  counterparty_id INTEGER NOT NULL REFERENCES counterparties(id),
  identifier_type TEXT NOT NULL,    -- 'iban' | 'vat' | 'mcc' | 'merchant_id' | 'email_domain' | 'name_alias'
  identifier      TEXT NOT NULL,
  source          TEXT NOT NULL,    -- 'rule' | 'config' | 'ai' | 'user'
  confidence      REAL,
  created_at      TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(identifier_type, identifier)
);

-- Domain D: GL + decision trace
CREATE TABLE journal_entries ( ... );
CREATE TABLE journal_lines (
  id          INTEGER PRIMARY KEY,
  entry_id    INTEGER NOT NULL REFERENCES journal_entries(id),
  account     TEXT NOT NULL,        -- PCG account code
  debit_cents INTEGER NOT NULL DEFAULT 0,
  credit_cents INTEGER NOT NULL DEFAULT 0,
  description TEXT,
  CHECK (debit_cents >= 0 AND credit_cents >= 0),
  CHECK (NOT (debit_cents > 0 AND credit_cents > 0))
);
CREATE TABLE decision_traces (
  id              INTEGER PRIMARY KEY,
  line_id         INTEGER REFERENCES journal_lines(id),
  source          TEXT NOT NULL,   -- 'webhook' | 'agent' | 'rule' | 'human'
  agent_run_id    INTEGER,         -- FK to orchestration.pipeline_runs.id (logical, cross-DB)
  model           TEXT,
  prompt_hash     TEXT,
  alternatives    TEXT,            -- JSON
  rule_id         INTEGER,
  confidence      REAL,
  approver_id     INTEGER,
  approved_at     TIMESTAMP,
  parent_event_id TEXT,            -- swan event_id or campaign id
  created_at      TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- Domain E: rules / policies
CREATE TABLE account_rules ( ... );
CREATE TABLE confidence_thresholds ( ... );

-- Domain F: budgets (Phase 2 expansion)
CREATE TABLE budget_envelopes ( ... );
CREATE TABLE budget_allocations ( ... );

-- Migration tracking (Day-1 fix vs. the reference doc)
CREATE TABLE _migrations (
  id          INTEGER PRIMARY KEY,
  name        TEXT NOT NULL UNIQUE,
  applied_at  TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
```

**Hard invariants enforced at write time**

1. `SUM(debit_cents) = SUM(credit_cents)` for every `entry_id` (checked in transaction before commit).
2. After every entry posts, the recorded bank-mirror balance equals Swan's `account.balances.booked` (re-fetched and compared).
3. Every `journal_lines.id` has at least one `decision_traces` row.

### 7.3 YAML pipeline DSL

**Purpose.** Pipelines are declarative DAGs of nodes. The same DSL drives ingress, classification, posting, and (later) reports and campaigns.

**Minimal pipeline (`pipelines/transaction_booked.yaml`)**

```yaml
name: transaction_booked
version: "1.0"
trigger: webhook
nodes:
  - id: fetch-transaction
    tool_class: SwanQueryTool
    depends_on: []

  - id: resolve-counterparty
    tool_class: CounterpartyResolverTool
    depends_on: [fetch-transaction]

  - id: ai-counterparty-fallback
    agent_class: CounterpartyClassifierAgent
    depends_on: [resolve-counterparty]
    when: counterparty_unresolved

  - id: classify-gl-account
    tool_class: GLAccountClassifierTool
    depends_on: [resolve-counterparty, ai-counterparty-fallback]

  - id: build-entry
    tool_class: JournalEntryBuilderTool
    depends_on: [classify-gl-account]

  - id: gate-confidence
    tool_class: ConfidenceGateTool
    depends_on: [build-entry]

  - id: post-entry
    tool_class: GLPosterTool
    depends_on: [build-entry, gate-confidence]
    when: passes_confidence

  - id: queue-review
    tool_class: ReviewQueueTool
    depends_on: [build-entry, gate-confidence]
    when: needs_review

  - id: assert-balance
    tool_class: BalanceInvariantTool
    depends_on: [post-entry]
    when: posted

  - id: decrement-envelope
    tool_class: BudgetEnvelopeTool
    depends_on: [post-entry]
    when: posted
```

**Rules of the DSL**

- Exactly one of `tool_class` or `agent_class` per node.
- `depends_on` is empty or references upstream node IDs only.
- `when` references a function in `conditions.py`; if false, node is `node_skipped` and downstream `ctx.get(node_id, {})` returns `{}`.
- Outputs flow via `ctx.node_outputs[node_id]` — no parameter binding, no expressions.

### 7.4 Agent pipelines

**Archetypes for MVP**

| Archetype             | Type    | Purpose                                                        |
| --------------------- | ------- | -------------------------------------------------------------- |
| `ClassifierAgent`     | agent   | Counterparty / GL-account fallback when rules+cache miss       |
| `ExtractorAgent`      | agent   | Pull structured fields from PDF invoices (Phase 2)             |
| `WriterAgent`         | agent   | Narratives for review queue, copilot answers (Phase 2)         |
| `PlannerAgent`        | agent   | Decompose a goal into a sequence of tool calls (Phase 3)       |
| `ResolverTool`        | tool    | Deterministic identifier match → cache writeback               |
| `JournalEntryBuilder` | tool    | Apply one of seven booking patterns, emit balanced lines       |
| `InvariantChecker`    | tool    | `SUM(D)=SUM(C)`, balance reconcile, freeze on failure          |
| `EnvelopeTool`        | tool    | Compute envelope balance from GL, decrement, push notification |

**Composition primitives**

- **Sequential.** `B depends_on [A]`.
- **Parallel.** Multiple nodes share `depends_on`.
- **Fan-out / fan-in.** N siblings depend on one upstream; a joiner depends on all N.
- **HITL via condition.** `gate-confidence` returns `{posted: true|false, needs_review: true|false}`; downstream nodes guard on those flags.

**Tool registry contract**

```python
# api/agent_registry.py
_TOOL_REGISTRY: dict[str, str] = {
    "SwanQueryTool":            "tools.swan_query:run",
    "CounterpartyResolverTool": "tools.counterparty_resolver:run",
    "GLAccountClassifierTool":  "tools.gl_account_classifier:run",
    "JournalEntryBuilderTool":  "tools.gl_journal_writer:build",
    "GLPosterTool":             "tools.gl_journal_writer:post",
    "ConfidenceGateTool":       "tools.confidence_gate:run",
    "BalanceInvariantTool":     "tools.invariant_checker:run",
    "BudgetEnvelopeTool":       "tools.budget_envelope:decrement",
    "ReviewQueueTool":          "tools.review_queue:enqueue",
}
_AGENT_REGISTRY: dict[str, str] = {
    "CounterpartyClassifierAgent": "agents.counterparty_classifier:run",
}
```

### 7.5 Injection pipelines

**Purpose.** Normalize raw external events (Swan webhooks, supplier invoices, manual CSV imports) into the GL through the same DAG runtime.

**Three injection paths in MVP**

1. **Swan webhook → GL.** `Transaction.Booked` → `transaction_booked.yaml`. Drives the demo.
2. **Document drop → AR/AP match.** Supplier invoice PDF (or seeded JSON) lands in a watched folder → `document_ingested.yaml` → `ExtractorAgent` → `expected_payments` row → matched against next inbound SEPA. Phase 2.
3. **Manual chat → query.** User asks the copilot a question → router classifies intent → `chat_query.yaml` runs read-only SQL tools → `WriterAgent` frames the answer with citations back to journal lines. Phase 2.

**Each path is fully replayable:** every input lands in an append-only table (`swan_events`, `documents`, `chat_messages`); the pipeline reads from there; reprocessing is `execute_pipeline(name, run_id=previous)`.

---

## 8. Technology Stack

### Backend (locked for MVP)

- **Python** 3.10+
- **FastAPI** (HTTP + SSE)
- **SQLite** with WAL, two databases (`accounting.db`, `orchestration.db`)
- **Anthropic SDK** (`anthropic` Python) — Claude Sonnet 4.6 default; pin per agent
- **`gql` or `httpx`** for Swan GraphQL re-query
- **`pydantic`** for tool/agent output dataclasses
- **`pyyaml`** for pipeline + routing table parsing
- **`rapidfuzz`** for fuzzy counterparty matching (token_set_ratio, threshold 85)
- **`uvicorn`** as ASGI server

### Frontend (Phase 2)

- **Vite + React + TypeScript**
- **Zustand** for state, EventSource for SSE
- **Tailwind** for speed

### Optional / deferred

- **LangGraph** — explicitly NOT in the MVP. Re-evaluate at Phase 3 if checkpointable graph state is needed for campaigns.
- **Postgres** — production migration target; not for the demo.
- **S3 Object Lock / external SIEM** — for tamper-evident audit; deferred to post-MVP.

### Third-party integrations

- **Swan GraphQL API** (sandbox: `api.swan.io/sandbox-partner/graphql`)
- **Swan OAuth2** (`oauth.swan.io/oauth2/token`, `client_credentials`)
- **Claude API** for agent LLM calls
- **(Phase 2)** Document OCR — out of scope; seeded structured data instead

### Versioning & migrations

- `_migrations` table tracked from Day 1 (the reference doc flagged its absence as a gap).
- Each migration is a Python module with `def migrate(conn): ...` and an idempotent `ALTER TABLE` body guarded by `PRAGMA table_info`.
- Schema files (`schema/*.sql`) are bootstrap-only; migrations carry the running system forward.

---

## 9. Security & Configuration

### Authentication

- **Swan OAuth2 client_credentials.** Token cached in-process, refreshed at expiry-60s or on 401. **Three separate secrets** (do not conflate):
  - `SWAN_CLIENT_ID` / `SWAN_CLIENT_SECRET` — OAuth
  - `SWAN_WEBHOOK_SECRET` — async ingress shared secret
  - `SWAN_PAYMENT_CONTROL_SECRET` — sync hook shared secret
- **Application-level auth.** Out of scope for hackathon (single-user demo, basic `Authorization: Bearer <static>` token on internal API). Production: OIDC.

### Webhook signature

- **Constant-time string compare** (`hmac.compare_digest`) of `x-swan-secret` against the persisted subscription secret.
- **No HMAC-SHA256** — Swan uses shared-secret equality. No replay protection from Swan; mitigate via `event_id` dedup.
- **IP allowlist** (firewall): `52.210.172.90`, `52.51.125.72`, `54.194.47.212`. Defense in depth.

### Configuration management

- All secrets via `.env.local` (loaded with `python-dotenv`) — never committed. Canonical names listed in §8.
- Pipeline DSL files and the routing table are version-controlled; changes are PRs.
- `confidence_thresholds` table is the runtime knob — UI-tunable in Phase 2.

### Security in scope

- ✅ Webhook signature verification
- ✅ Idempotency on every external event
- ✅ Integer-cents money — no float drift
- ✅ Append-only audit tables
- ✅ Decision trace per AI write (EU AI Act readiness)

### Security out of scope (MVP)

- ❌ Tamper-evident audit (deferred — append-only is structural, not cryptographic)
- ❌ Multi-tenant isolation
- ❌ Production SCA flows (mocked)
- ❌ Secrets rotation playbook (Swan's secret rotation grace period is unconfirmed — flagged risk)

### Deployment

- Single container, single host for the hackathon.
- HTTPS via reverse proxy (Caddy or Nginx).
- SSE requires sticky / stateful host (not edge functions).

---

## 10. API Specification

### Inbound (Swan → us)

**`POST /swan/webhook`** — async event ingress

- Headers: `x-swan-secret: <subscription secret>`, `x-swan: present`
- Body (Swan envelope):
  ```json
  {
    "eventType": "Transaction.Booked",
    "eventId": "<uuid>",
    "eventDate": "2026-04-25T13:21:04.673Z",
    "projectId": "<project-id>",
    "resourceId": "<transaction-id>"
  }
  ```
- Behavior: verify signature → `INSERT OR IGNORE` into `swan_events` → enqueue pipeline → return `200` (≤ 50ms target, 10s hard ceiling).
- Errors: `401` on bad signature, `200` on duplicate (idempotent), `200` on unknown event_type (logged for review).

**`POST /swan/payment-control`** — synchronous policy hook (Phase 3)

- 1.5s budget. Deterministic-rules-first; LLM only with a tight timeout fallback.

### Internal

**`POST /pipelines/run/{name}`** — manual / test trigger

- Body: `{ "trigger_payload": { ... } }`
- Returns: `{ "run_id": <int>, "stream_url": "/runs/<run_id>/stream" }` immediately.

**`GET /runs/{run_id}/stream`** — SSE live updates (events: `node_started`, `node_completed`, `node_failed`, `pipeline_completed`).

**`GET /runs/{run_id}`** — full run reconstruction (joined `pipeline_runs` + `pipeline_events`, JSON).

**`GET /journal_entries/{id}/trace`** — drill from a journal line to its decision trace, source pipeline run, and source webhook event.

---

## 11. Success Criteria

**MVP success definition.** A judge can:

1. Watch a Swan sandbox `Transaction.Booked` event fire.
2. See the journal entry appear in the live UI within 5 seconds.
3. Click the entry → see the full decision trace (rule R-12 fired, confidence 1.0, no LLM call).
4. Click a low-confidence entry from the same demo run → see the LLM trace (model, prompt hash, alternatives, confidence 0.62, queued for review).
5. Watch the operator approve the queued entry → trace updates with `approver_id`, entry posts.
6. Watch a `Transaction.Released` event fire → see the compensating entry post automatically through the *same pipeline runtime*, *no new code path*.

### Functional requirements

- ✅ End-to-end webhook → GL latency under 5 seconds (p95) for the booked-transaction path
- ✅ Idempotent on duplicate `event_id` (verified by replaying the same webhook)
- ✅ Out-of-order tolerance (verified by injecting `Booked` before `Pending`)
- ✅ Adding a new event type requires zero changes outside `routing.yaml` and `pipelines/<new>.yaml`
- ✅ Every journal line has exactly one decision trace; no orphans, no nulls
- ✅ Balance invariant holds across the demo run (recorded balance = Swan booked balance)
- ✅ Confidence gate routes < threshold to review and ≥ threshold to auto-post
- ✅ Cache warmer: second occurrence of a previously-AI-classified merchant skips the LLM (verified in event log: no `agent_started` for that node on second run)

### Quality indicators

- Pipeline run reconstructable end-to-end from `(pipeline_runs, pipeline_events)` join — no separate log files needed
- Zero floating-point arithmetic on money paths (grep audit on PR)
- Every external event keyed on its provider event ID (no internal sequence numbers as idempotency keys)

### UX goals

- Founder sees a live-updating ledger view; events animate in.
- Click any number → drill to source. Click a decision → see the trace.
- Review queue is the only UI surface for low-confidence entries; everything else auto-posts.

---

## 12. Implementation Phases

### Phase 1 — Meta-layer foundation (Days 1–2 of hackathon, ~16 working hours)

**Goal:** End-to-end Swan-webhook → GL → SSE for one transaction type, with the meta-layer in place.

**Deliverables**

- ✅ Repo skeleton, `.env.local`, secrets handling
- ✅ `accounting.db` + `orchestration.db` schemas + bootstrap + `_migrations` table
- ✅ Swan OAuth client (token cache, refresh-on-401)
- ✅ `/swan/webhook` endpoint: signature verify, idempotent insert, route via `routing.yaml`, return 200
- ✅ DAG executor with topological layering, `asyncio.gather` per layer, append-only event writes
- ✅ Tool / agent registries; `FingentContext` dataclass
- ✅ Three tools: `SwanQueryTool`, `CounterpartyResolverTool` (rules-only), `JournalEntryBuilderTool`, `GLPosterTool`, `BalanceInvariantTool`
- ✅ One pipeline: `transaction_booked.yaml`
- ✅ One demo: card spend → journal entry → balance assert
- ✅ Unit tests for the executor, the routing table, idempotency

**Validation**

- Two identical webhooks → one journal entry. ✓
- `Transaction.Booked` for a known merchant (rule cache hit) → journal entry posted, `agent_started` event count = 0 for that run.
- Recorded balance equals re-queried Swan balance after every post.
- Adding `Card.Created` to the demo: write `card_lifecycle.yaml`, add `CardLifecycleTool` to the registry, edit one line in `routing.yaml`. Total LOC change in ingress: 0.

### Phase 2 — Agentic classification + UI + injection breadth (Days 3–4, ~16 hours)

**Goal:** AI fallback with cache writeback; live UI; document and chat injection.

**Deliverables**

- ✅ `CounterpartyClassifierAgent` (Claude SDK) — fallback path, writes to `counterparty_identifiers`
- ✅ `GLAccountClassifierAgent` — fallback for novel merchants, writes to `account_rules`
- ✅ `ConfidenceGateTool` + `ReviewQueueTool` + `decision_traces` populated
- ✅ React + Vite frontend: live ledger, drill-to-trace, review queue
- ✅ SSE wiring `/runs/{id}/stream`
- ✅ `document_ingested.yaml` pipeline with seeded supplier invoice JSON (no OCR)
- ✅ `chat_query.yaml` for the read-only copilot
- ✅ Virtual IBAN for one demo customer; deterministic inbound match demonstration

**Validation**

- Novel merchant arrives → AI classifies, confidence ≥ floor, posts. Same merchant arrives again → cache hit, no LLM call.
- Low-confidence entry → review queue → operator clicks approve → trace gains `approver_id` and `approved_at`.
- Click any number in the UI → drill to journal line → drill to decision trace → drill to source webhook event.

### Phase 3 — Per-employee budget envelopes + campaign engine (post-hackathon, weeks 1–2)

**Goal:** The wedge against Pennylane/Ramp.

**Deliverables**

- ✅ Domain F (`budget_envelopes`, `budget_allocations`); envelope balance computed from GL
- ✅ Per-employee envelopes wired to Swan card spending limits via `updateCard`
- ✅ `Campaign` object + `PlannerAgent` that re-runs forecasts and proposes adjustments at checkpoints
- ✅ Synchronous `/swan/payment-control` endpoint with policy-as-code (deterministic-first, LLM with 1.2s timeout)
- ✅ Per-API-key AI-cost allocation as a first-class supplier in the GL

**Validation**

- "Save €15k for CNC by Q3" → planner proposes envelope adjustments at weekly checkpoint; founder approves; envelopes update; trace recorded.
- Card swipe at 14:03 → payment-control hook decides under 1.5s → demoable on stage.

### Phase 4 — Agentic reports (weeks 3–6 post-hackathon)

**Goal:** Convert "better accounting" → "automation of Big-4 DD work."

**Deliverables**

- ✅ `ReportPlannerAgent` — decomposes a DD-pack template into sub-reports
- ✅ Sub-agents per section (EBITDA bridge, cohort retention, etc.) — each cites back to journal lines
- ✅ PDF + interactive web pack with click-through to source
- ✅ Monthly board pack and management commentary using the same scaffolding

**Validation**

- Generate a synthetic DD pack from a 12-month seed. Every figure drills to a journal line. Total wall time < 4 hours for ~25 sub-reports.

---

## 13. Future Considerations

- **LangGraph adoption.** If campaign and planner state machines outgrow the named-condition pattern, port the agent runtime to LangGraph. Decision trace and tool registry stay; only the executor changes.
- **Postgres migration.** SQLite is correct for MVP. At ~10k transactions/day or first paying customer, migrate. Schema is portable; the WAL-specific ergonomics are not.
- **Tamper-evident audit.** Mirror `pipeline_events` to S3 Object Lock or QLDB. Hook point: `db.write_event`. EU AI Act will require this.
- **OCR for arbitrary invoices.** Document AI / Mistral OCR / paddleOCR for receipts. Out of scope for MVP; deferred until campaigns drive enough manual upload volume.
- **Multi-entity consolidation.** Pennylane offloads to Joiin; we should own this primitive at Phase 5+.
- **E-invoicing PA certification.** September 2026 mandate; only relevant if we sell to French SMEs as primary buyer (vs. selling to founders who use Pennylane for the books). Strategic choice, not technical.
- **Campaign budgets driving Swan card limits live.** Already on the Phase 3 plan; the production hardening is rate-limit and SCA-flow management on `updateCard`.
- **Vector store for past decisions / RAG over contracts.** Phase 4 prerequisite for the DD pack agent.

---

## 14. Risks & Mitigations

1. **Risk: Swan credential / SCA friction kills the demo on stage.**
   - *Mitigation:* mock all SCA flows in the sandbox demo path; pre-record any flow that requires user consent; use sandbox-admin shortcuts. Keep a video fallback.

2. **Risk: Webhook signature / replay subtleties cause a duplicate or missed entry mid-demo.**
   - *Mitigation:* `INSERT OR IGNORE` on `swan_events.event_id` is the idempotency boundary. Test with a duplicate-fire script before stage. Test out-of-order delivery (`Booked` before `Pending`).

3. **Risk: Agentic claims collapse under judge questioning ("what does the AI actually *do*?").**
   - *Mitigation:* the decision trace UI is the answer. Click any AI-touched line, see the prompt hash, model, alternatives, confidence. Build the trace UI before any campaign feature.

4. **Risk: Pipeline executor under-engineered for HITL — no native pause/resume.**
   - *Mitigation:* HITL is a `when:` condition that reads from a review-decision tool that polls a `decision_pending` table. Tool blocks (with timeout) until human acts. Document this as the pattern; revisit only if it breaks.

5. **Risk: SQLite WAL contention as we add concurrent ingestion paths.**
   - *Mitigation:* single writer per DB by convention; tools use short transactions; pipelines within a layer avoid writing the same downstream `node_id`. Profile before Phase 3.

6. **Risk: LangGraph debate consumes hackathon hours.**
   - *Mitigation:* explicitly NOT in MVP (§4 out-of-scope). Anthropic SDK + named-condition gates is the lock-in. Re-evaluate at Phase 3, not before.

7. **Risk: Decision trace becomes a JSON sidecar (the doc warned against this).**
   - *Mitigation:* `decision_traces` is a real table with FKs from Day 1. Every `GLPosterTool` invocation MUST write the trace before it commits. Lint rule: PR fails if `journal_lines` insert without a sibling `decision_traces` insert in the same code path.

---

## 15. Appendix

### Related documents (read in this order if onboarding)

1. `pennylane_vs_us.md` — strategic positioning, demo wow-moments, infrastructure decisions
2. `projectbriefing.md` — product vision, personas, MVP scope, deterministic-first discipline
3. `architecure.md` — Domains A–F, invariants, booking patterns, payment-control hook
4. `Dev orchestration/_exports_for_b2b_accounting/01_ORCHESTRATION_REFERENCE.md` — Kahn scheduler, executor loop, tool registry
5. `Dev orchestration/_exports_for_b2b_accounting/02_YAML_WORKFLOW_DSL.md` — pipeline DSL, named conditions, fan-out/in
6. `Dev orchestration/_exports_for_b2b_accounting/03_SQLITE_BACKBONE.md` — schemas, indexes, migration gap (now patched in §7.2)
7. `Dev orchestration/_exports_for_b2b_accounting/04_AGENT_PATTERNS.md` — ToolResult shape, multiplicative confidence, cache-warmer cascade, refusal log
8. `Dev orchestration/swan/SWAN_API_REFERENCE.md` — events catalog, GraphQL surface, OAuth, payment-control synchronous hook

### Key dependencies

- Swan sandbox project + regenerated client secret (open question per `SWAN_API_REFERENCE.md` §13)
- Anthropic API key (Claude Sonnet 4.6)
- Python 3.10+, FastAPI, SQLite ≥ 3.35

### Open questions to close before Phase 3

1. **Payment-control scope.** Synchronous LLM in 1.5s is risky. Phase 3 scope: deterministic-rules-only by default; LLM only on opt-in card profiles.
2. **Virtual IBAN strategy at scale.** Per-customer issuance is elegant but quota-bounded. Confirm Swan production limits before relying on it for inbound matching at >100 customers.
3. **Webhook secret rotation.** Swan's grace-period behavior unconfirmed (§F gap from the brief). Get answer before any production rollout.
4. **Confidence threshold UX.** Where does the founder set them? Phase 2 ships hardcoded; Phase 3 surfaces a settings UI.
5. **Chart of accounts subset.** PCG which accounts? Locked at Phase 1 kickoff for the demo seed.

### Repository structure (target)

See §6 directory structure. Each subdirectory has a `README.md` describing its contract.

---

*Draft v1. Revise as Phase 1 lands and the agent runtime question is answered in code, not in docs.*
