---
title: Phase 1 — Metalayer Foundation (Schemas + DAG Executor + Audit/Cost Spine)
status: ready-to-execute
date: 2026-04-25
parent_prd: Orchestration/PRDs/RealMetaPRD.md
covers_prd_phases: A + B + C  (RealMetaPRD §12, lines 1573–1618)
out_of_scope_phases: D (Swan path), E (Document path), F (Frontend) — separate plans
---

# Feature: Phase 1 — Metalayer Foundation

The following plan should be complete, but its important that you validate documentation
and codebase patterns and task sanity before you start implementing.

Pay special attention to naming of existing utils, types, and models. Import from the right
files etc. **There is no existing `backend/` code yet — this plan creates the package
from scratch.** Verify by running `ls backend/` at task start; if the directory exists,
stop and reconcile against this plan before overwriting.

## Feature Description

This is the **foundation layer** that the two demo paths (Swan webhook → GL,
PDF invoice → accrual) and the live dashboard sit on top of. After Phase 1 lands,
no business logic exists yet — but the DAG executor runs YAML pipelines layer-by-
layer with named-condition gates, the three SQLite databases open with the right
PRAGMAs and migration tooling, every agent call writes a `(decision, cost,
employee)` triple to `audit.db`, and the wedge SQL — *"how much did Anthropic
bill us this month, split per employee"* — returns sensible numbers from a
fixture run.

This plan covers **RealMetaPRD §12 Phase A + Phase B + Phase C** as one unit
because each depends on the previous: schemas without an executor are dead
storage; an executor without an audit spine is just code that runs; and the
audit spine without the schemas has nowhere to write.

## User Story

As a **backend engineer building the demo paths next**, I want a YAML-driven DAG
executor with three migration-managed SQLite databases, four registries, a
cross-run cache, and an end-to-end agent-decision/cost-recording spine,
**so that** when I write `transaction_booked.yaml` (Phase D) and
`document_ingested.yaml` (Phase E) the only new code is tools, agents,
conditions, and one routing.yaml line — never executor surgery.

## Problem Statement

The hackathon-weekend MVP succeeds only if Phases D, E, and F can each be
isolated to writing tools/agents/YAML. The single biggest risk to that is
arriving at Saturday afternoon with the executor still leaking abstractions:
runners that don't share an `AgentResult` shape, cache keys that drift across
runs, an audit table that requires custom write paths per call site, or
schemas that bootstrap one way but migrate another. Phase 1 exists to make
those failure modes structurally impossible before any domain code ships.

## Solution Statement

Land the entire metalayer in three sequenced sub-phases, each with its own
exit test:

1. **Phase 1.A — Schemas, store, migrations.** Three SQLite DBs
   (`accounting.db` / `orchestration.db` / `audit.db`) open with all eight
   PRAGMAs from `REF-SQLITE-BACKBONE.md:199–230`; each has a canonical
   `schema/{name}.sql` and an idempotent `migrations/{name}/0001_init.py`;
   bootstrap-replay round-trip test passes on all three.
2. **Phase 1.B — Metalayer.** YAML DSL + Kahn DAG parser + layered async
   executor + four registries (tools/agents/runners/conditions) + cross-run
   cache + three runners (Anthropic real; ADK + Pydantic AI behind optional
   extras). A `noop_demo.yaml` runs end-to-end and emits the canonical event
   sequence into `orchestration.db`.
3. **Phase 1.C — Audit + cost + employees.** Every agent runner writes one
   `agent_decisions` + one `agent_costs` row through a single `propose →
   checkpoint → commit` helper; `employees` table seeded; the wedge SQL
   from RealMetaPRD §7.11 returns one row per employee against a fixture
   run.

The three sub-phases lock together as: A creates writable persistence, B
creates a runtime that uses it, C creates the audit shape that every
domain-side write later inherits.

## Feature Metadata

**Feature Type**: New Capability (greenfield foundation)
**Estimated Complexity**: High (~10 hours of focused engineering;
RealMetaPRD §12 Phase A 3h + Phase B 5h + Phase C 2h)
**Primary Systems Affected**: All — every later phase imports from this one
**Dependencies**:

- Python 3.12 (async-first; RealMetaPRD §8 line 1330)
- `aiosqlite >= 0.19` (`REF-SQLITE-BACKBONE.md:78`)
- `pyyaml` (safe_load only)
- `pydantic >= 2.5` (v2 only; `REF-FASTAPI-BACKEND.md:299–314`)
- `anthropic >= 1.0.0` (`ANTHROPIC_SDK_STACK_REFERENCE.md:34`) — real runner
- `google-adk >= 1.29.0` (`REF-GOOGLE-ADK.md:23`) — optional extra, stub OK in tests
- `pydantic-ai-slim[cerebras]` (`CEREBRAS_STACK_REFERENCE.md:420`) — optional extra, stub OK
- `fastapi`, `uvicorn` (Phase 1 ships only the lifespan + `/healthz`; full routes
  arrive in Phase D)
- `pytest`, `pytest-asyncio`

---

## CONTEXT REFERENCES

### Relevant Codebase Files — IMPORTANT: YOU MUST READ THESE BEFORE IMPLEMENTING!

> **Note:** Phase 1 is greenfield Python. There is no existing `backend/` to
> mirror. The "files to read" below are **specification documents**, not source
> files. Treat each line range as required reading.

**Master PRD (the contract for this plan):**

- `Orchestration/PRDs/RealMetaPRD.md` (lines 140–272)
  — **Why:** §4 in-scope / out-of-scope decisions are non-negotiable. Phase
  1 implements only the metalayer rows; Phase D/E/F implement the rest.
- `Orchestration/PRDs/RealMetaPRD.md` (lines 411–525)
  — **Why:** §6.1–§6.4 lay out the architecture, three-DB rationale,
  cash/accrual `basis` column, and design patterns (compound confidence,
  named conditions, propose→checkpoint→commit). Read every word.
- `Orchestration/PRDs/RealMetaPRD.md` (lines 535–650)
  — **Why:** §6.5 directory structure is the *exact* tree to create;
  §6.6 lists every PRAGMA with cites back to REF-SQLITE-BACKBONE.
- `Orchestration/PRDs/RealMetaPRD.md` (lines 845–1172)
  — **Why:** §7.5 is the canonical SQL for all three databases. Copy-paste
  these DDL blocks into `backend/orchestration/store/schema/*.sql`.
- `Orchestration/PRDs/RealMetaPRD.md` (lines 1186–1322)
  — **Why:** §7.7 cost helper (with rates table), §7.8 prompt-hash
  canonicalization, §7.9 retry/timeout/idempotency, §7.10 `AgentResult`
  cross-runtime normalization, §7.11 wedge query.
- `Orchestration/PRDs/RealMetaPRD.md` (lines 1573–1618)
  — **Why:** §12 Phase A/B/C exit checklists. Map each item to a task below.
- `Orchestration/PRDs/RealMetaPRD.md` (lines 1715–1727)
  — **Why:** §14 risks #5, #7, #8, #10 directly affect Phase 1 design.

**Reference guides — READ THEM, AGENT WHO IMPLEMENTS THIS PLAN:**

The five `Dev orchestration/_exports_for_b2b_accounting/*.md` and four `Dev
orchestration/tech framework/REF-*.md` files contain the patterns, schemas,
PRAGMAs, function signatures, and gotchas distilled below. The plan cites
them by relative path + line range. **You must open each cited section before
writing the corresponding task's code.**

- `Dev orchestration/_exports_for_b2b_accounting/01_ORCHESTRATION_REFERENCE.md`
  - Lines 19–42 — Kahn topological sort (`_topological_layers`)
  - Lines 49–68 — Per-node execution wrapper (`_run_node`)
  - Lines 79–105 — Orchestrator loop (`_execute`)
  - Lines 114–124 — `AgnesContext` dataclass shape
  - Lines 138–151 — Registry pattern (flat dict, importlib, no decorators)
  - Lines 164–189 — Condition evaluator pattern
  - Lines 197–227 — Failure semantics; flags missing per-node timeout

- `Dev orchestration/_exports_for_b2b_accounting/02_YAML_WORKFLOW_DSL.md`
  - Lines 18–34 — YAML loader (`safe_load` + dataclass projection)
  - Lines 54–68 — `PipelineNode` / `Pipeline` dataclasses
  - Lines 71–92 — Tool vs. agent dispatch table; mandatory/optional fields
  - Lines 96–120 — Named conditions surface (`when:` resolves to function)
  - Lines 128–191 — Two end-to-end YAML examples
  - Lines 218–225 — Things the DSL deliberately omits (no loops, no params)

- `Dev orchestration/_exports_for_b2b_accounting/03_SQLITE_BACKBONE.md`
  - Lines 7–22 — Two-DB rationale (we extend to three; see RealMetaPRD §6.2)
  - Lines 30–94 — Confidence-scored identity tables + append-only events
  - Lines 98–142 — Decision-trace table shape (the audit spine pattern)
  - Lines 150–162 — Bootstrap & migration pattern
  - Lines 211–226 — `API_Response_Cache` shape (lift verbatim for `node_cache`)
  - Lines 229–244 — Minimal table set for new project
  - Lines 386 — **CRITICAL: add `_migrations` table from day one**

- `Dev orchestration/_exports_for_b2b_accounting/04_AGENT_PATTERNS.md`
  - Lines 9–32 — Tool contract (`def run(ctx) -> dict`, sync, no globals)
  - Lines 34–54 — Agent contract (`async def run(ctx) -> dict`)
  - Lines 55–64 — Error propagation (full traceback into event log)
  - Lines 119–140 — `ToolResult` shape + `compound_confidence` (multiplicative)
  - Lines 167–174 — Rule-based `justification.render()` (NOT LLM-generated)
  - Lines 204–210 — Gate thresholds; `RefusalEngine.CONFIDENCE_FLOOR = 0.50`
  - Lines 222–311 — End-to-end cache-warmer flow (template for counterparty
    resolution; cite for Phase D, not Phase 1, but read so the schema choices
    you make in Phase 1 don't trap us)
  - Lines 315–325 — What to lift verbatim
  - Lines 329 — **What NOT to lift:** ADK runner, regex JSON extraction

- `Dev orchestration/_exports_for_b2b_accounting/05_swan_integration.md`
  - Lines 24–30 — Swan object model (Account / AccountMembership / Card)
  - Lines 184–231 — Hard rules (money/idempotency/booking/webhooks/SCA)
  - Lines 235–248 — Canonical environment variable names
  - **Phase 1 only needs the env-var names + the integer-cents rule.**
    Full Swan integration is Phase D.

- `Dev orchestration/tech framework/REF-SQLITE-BACKBONE.md`
  - Lines 78–87 — Version pins (`aiosqlite >= 0.19`, SQLite 3.38+, Python 3.10+)
  - Lines 95–137 — Two-DB split + cross-DB logical FKs (extend to three)
  - Lines 149–195 — Connection lifecycle in FastAPI lifespan
  - Lines 199–230 — **Every PRAGMA explained — copy into `bootstrap.open_dbs()`**
  - Lines 237–288 — WAL + single-writer rule; `BEGIN IMMEDIATE` mandatory
  - Lines 295–316 — `write_tx` async context manager (the only sanctioned
    write path)
  - Lines 322–337 — WAL file growth mitigations
  - Lines 340–388 — Append-only + idempotency (two-statement claim pattern)
  - Lines 419–472 — Integer cents end-to-end + VAT split
  - Lines 518–557 — Decision trace as real table
  - Lines 598–687 — Migration runner + 12-step rewrite (CRITICAL: re-enable
    `PRAGMA foreign_keys=ON` after rewrite — line 685)
  - Lines 745–762 — Backup via `VACUUM INTO`
  - Lines 791–814 — Gotchas summary

- `Dev orchestration/tech framework/REF-FASTAPI-BACKEND.md`
  - Lines 31–82 — Lifespan pattern (replace `@app.on_event`); long-lived
    `aiosqlite` connections on `app.state`
  - Lines 84–85 — CORS gotcha (`allow_origins=["*"]` + `allow_credentials=True`
    are mutually exclusive)
  - Lines 727–765 — Single-worker uvicorn (Phase 1 commits to `--workers=1`)
  - **Phase 1 only ships the lifespan + a `/healthz` endpoint;** the full
    webhook surface is Phase D.

- `Dev orchestration/tech framework/REF-SSE-STREAMING-FASTAPI.md`
  - Lines 77–189 — Event-bus singleton + functions
  - Lines 217–249 — `write_event()` dual-write pattern (SQLite + bus)
  - **Phase 1 lands `write_event()` and the in-process bus;** the SSE endpoint
    is Phase F. The bus must exist in Phase 1 because the executor calls
    `publish_event()` on every node transition.

- `Orchestration/research/ANTHROPIC_SDK_STACK_REFERENCE.md`
  - Lines 41–56 — Client init (`AsyncAnthropic(timeout=4.5, max_retries=2)`)
  - Lines 70–82 — Messages API request shape
  - Lines 91–93 — `usage` object (input/output/cache_read/cache_write)
  - Lines 186–269 — Tool-use loop with deadline (Phase 1 ships the
    AgentResult-shaping wrapper; the loop is Phase D)
  - Lines 300–323 — `submit-tool` pattern for structured output
  - Lines 519–565 — Token counting + cost helper (the rate table goes into
    `cost.py`; PRD §7.7 has the integer-micro-USD pinned values)
  - Lines 864–914 — Determinism: temperature/version/prompt_hash
  - Lines 901–914 — `prompt_hash` formula (model + system + tools + last
    user message, sorted JSON, sha256[:16])
  - Lines 1067–1083 — Gotchas (per-app `AsyncAnthropic` singleton, never
    per-request)
  - Lines 1087–1107 — `decision_traces` capture fields

- `Orchestration/research/CEREBRAS_STACK_REFERENCE.md`
  - Lines 41–80 — Client init (`AsyncCerebras(timeout=4.0)`)
  - Lines 89–105 — Models + pricing pinning
  - Lines 149–200 — Tool calling with `strict: true` (constrained decoding)
  - Lines 258–286 — `submit-tool` pattern (because `tools` and
    `response_format` are mutually exclusive — line 161)
  - Lines 409–578 — Pydantic AI orchestration pattern (Option A)
  - Lines 826–839 — Comparison table; **line 840: avoid ADK unless committing
    to Vertex migration** (PRD §12 Phase B keeps ADK as stub for tests)
  - Lines 859–875 — Cerebras vs. Claude parity gaps (system-prompt placement,
    tool-result shape — relevant when normalizing into `AgentResult`)

- `Dev orchestration/tech framework/REF-GOOGLE-ADK.md`
  - Lines 23–51 — Auth + model strings + `LlmAgent` constructor
  - Lines 213–249 — Per-node `InMemoryRunner` pattern
  - Lines 258–275 — `PipelineContext` dataclass
  - **Phase 1 only ships a stub ADK runner** (raises `NotImplementedError`
    when called, but its module imports cleanly so optional-extra tests pass).
    Real ADK wiring is post-hackathon (PRD §14 risk #4).

- `Dev orchestration/swan/CLAUDE.md`
  - Lines 14–18 — Auth env vars (`SWAN_CLIENT_ID`, `SWAN_CLIENT_SECRET`)
  - Lines 22–27 — Money rule (decimal string → integer cents)
  - **Phase 1 only consumes the env-var names** for `bootstrap.open_dbs()`'s
    sibling `bootstrap.load_env()`. No Swan calls happen in Phase 1.

- `Orchestration/from agents for agents/PRD1_VALIDATION_BRIEFING.md`
  - **Read in full.** Every defect (C1–C5, G1–G6, A1–A4) was rolled into
    RealMetaPRD; this doc is the audit trail. Pay especially close attention
    to:
    - **C2 Path A vs. B** — RealMetaPRD took Path B (uses `tool:` /
      `agent:` with `module.path:symbol` strings, **not** `tool_class:` /
      `agent_class:` with CamelCase registry). The DSL in Phase 1.B follows
      RealMetaPRD §7.3 / §7.4 (e.g. `tool: tools.swan_query:fetch_transaction`).
    - **C4** — `prompt_hash` formula; copy from RealMetaPRD §7.8 verbatim.
    - **C5** — ADK is a stub; tests must not require live ADK calls.
    - **G3** — `agent_decisions` has seven extra columns beyond the original
      PRD1 shape. RealMetaPRD §7.5 (`audit.db`) already includes them; this
      plan inherits.

### New Files to Create

> **Convention:** Phase 1 creates only the files explicitly listed below.
> Every other file in `backend/` arrives in Phase D / E / F.

```
backend/
  api/
    main.py                          # FastAPI app: lifespan + /healthz only (Phase 1)
  orchestration/
    __init__.py
    context.py                       # AgnesContext dataclass (RealMetaPRD §6.5)
    dag.py                           # Kahn parser, layer computation, cycle detection
    executor.py                      # Layer-by-layer asyncio.gather, fail-fast
    registries.py                    # Four flat-dict registries + lazy importer
    cache.py                         # Cross-run node cache (read/write helpers)
    cost.py                          # COST_TABLE_MICRO_USD + micro_usd(usage,...)
    prompt_hash.py                   # Canonical prompt_hash() per RealMetaPRD §7.8
    audit.py                         # propose → checkpoint → commit helper
    event_bus.py                     # In-process pub/sub (REF-SSE-STREAMING:77-189)
    yaml_loader.py                   # safe_load → Pipeline dataclass
    runners/
      __init__.py
      base.py                        # AgentRunner Protocol + AgentResult dataclass
      anthropic_runner.py            # Real runner — default
      adk_runner.py                  # Stub (raises NotImplementedError on call)
      pydantic_ai_runner.py          # Stub (raises NotImplementedError on call)
    store/
      __init__.py
      bootstrap.py                   # open_dbs() with PRAGMAs + per-DB asyncio.Lock
      writes.py                      # write_tx async context manager
      migrations.py                  # _migrations runner (per-DB)
      schema/
        accounting.sql               # RealMetaPRD §7.5 accounting.db block, verbatim
        orchestration.sql            # RealMetaPRD §7.5 orchestration.db block, verbatim
        audit.sql                    # RealMetaPRD §7.5 audit.db block, verbatim
      migrations/
        accounting/
          __init__.py
          0001_init.py               # idempotent re-application of accounting.sql
        orchestration/
          __init__.py
          0001_init.py
        audit/
          __init__.py
          0001_init.py
    pipelines/
      noop_demo.yaml                 # 3-node smoke pipeline used by Phase 1.B tests
    tools/
      __init__.py
      noop.py                        # one tool used by noop_demo.yaml
    agents/
      __init__.py
      noop_agent.py                  # one agent used by noop_demo.yaml; uses anthropic runner
    conditions/
      __init__.py
      gating.py                      # passes_confidence, needs_review, posted (stubs OK)
  tests/
    __init__.py
    conftest.py                      # tmp-DB fixtures, fake AsyncAnthropic
    test_bootstrap.py                # PRAGMAs set; three connections opened; locks distinct
    test_migrations.py               # bootstrap-replay round-trip on all 3 DBs
    test_yaml_loader.py              # strict-key rejection; required-field validation
    test_dag.py                      # cycle detection; layer ordering
    test_executor.py                 # noop pipeline; event sequence; fail-fast
    test_registries.py               # lazy import; missing-key error
    test_cache.py                    # cache_key round-trip across float drift; hit event
    test_audit.py                    # propose→checkpoint→commit triple
    test_cost.py                     # micro_usd matches PRD §7.7 fixtures
    test_prompt_hash.py              # model swap → different hash; whitespace → different
    test_employee_ledger.py          # wedge SQL returns one row per employee
    test_runner_shape.py             # all three runners produce identical AgentResult shape
                                     # (ADK + Pydantic AI use stub clients)
  pyproject.toml                     # python>=3.12; deps; optional extras (adk, pydantic_ai)
  pytest.ini                         # asyncio_mode=auto
  .env.example                       # canonical env-var names from CLAUDE.md & PRD §9.3
data/
  blobs/
    .gitkeep                         # PDF storage location (used by Phase E only)
```

**Files NOT created in Phase 1** (deferred to D/E/F): every Swan file,
`documents.py`, `swan_webhook.py`, `external_webhook.py`, `runs.py`,
`dashboard.py`, `journal_entry_builder.py`, `gl_poster.py`, `swan/oauth.py`,
`swan/graphql.py`, frontend, etc.

### Relevant Documentation YOU SHOULD READ BEFORE IMPLEMENTING!

External library docs the agent should consult during implementation:

- [aiosqlite docs](https://aiosqlite.omnilib.dev/en/latest/)
  - Specific: connection, row_factory, executemany, executescript
  - Why: Long-lived connection per DB on `app.state` (REF-SQLITE-BACKBONE:165-179)
- [SQLite PRAGMA reference](https://www.sqlite.org/pragma.html)
  - Specific: `journal_mode=WAL`, `foreign_keys`, `busy_timeout`, `synchronous`,
    `wal_autocheckpoint`, `journal_size_limit`, `mmap_size`, `cache_size`,
    `temp_store`
  - Why: All eight set on every connection (REF-SQLITE-BACKBONE:199-230;
    RealMetaPRD §6.6 lines 633-643)
- [SQLite STRICT tables](https://www.sqlite.org/stricttables.html)
  - Specific: `STRICT` clause; `typeof` enforcement
  - Why: Every CHECK constraint in RealMetaPRD §7.5 lives inside `STRICT`
    tables; integer-cents columns gain `CHECK(typeof(x)='integer')` paranoia
    (REF-SQLITE-BACKBONE:431)
- [Anthropic Python SDK — Messages](https://docs.anthropic.com/en/api/messages)
  - Specific: `client.messages.create`, response shape, `usage` object
  - Why: Phase 1 wraps it in `AnthropicRunner.run()` to produce `AgentResult`
    (ANTHROPIC_SDK_STACK_REFERENCE:41-105)
- [Anthropic SDK — token counting](https://docs.anthropic.com/en/api/messages-count-tokens)
  - Specific: `client.messages.count_tokens(...)`
  - Why: Pre-flight cost estimation in `cost.py` (ANTHROPIC_SDK_STACK_REFERENCE:519-565)
- [Pydantic v2 — field validators](https://docs.pydantic.dev/2.5/concepts/validators/)
  - Specific: `@field_validator`, classmethod syntax (NOT v1 `@validator`)
  - Why: Pipeline DSL validation (REF-FASTAPI-BACKEND:299-314 calls this out
    as a v1→v2 trap)
- [PEP 654 — Exception Groups](https://peps.python.org/pep-0654/) (informational)
  - Why: `asyncio.gather(..., return_exceptions=True)` semantics for
    layer fail-fast (RealMetaPRD §6.4 line 504)

### Patterns to Follow

> The patterns below are pulled from the cited reference guides. **Match them
> exactly.** Deviations need a comment explaining why.

**Naming Conventions** (all snake_case Python; CamelCase only for classes):

```python
# Files: snake_case (executor.py, dag.py, prompt_hash.py)
# Classes: CamelCase (AgnesContext, PipelineNode, AgentResult, AgentRunner)
# Functions: snake_case (open_dbs, write_tx, micro_usd, _topological_layers)
# Module-private: leading underscore (_topological_layers, _CONDITION_REGISTRY)
# Constants: UPPER_SNAKE (COST_TABLE_MICRO_USD, _BUS_QUEUE_MAXSIZE)
# YAML keys: snake_case lower (id, tool, agent, depends_on, when, runner, cacheable)
```

**Error Handling** (from `04_AGENT_PATTERNS.md:55-64` and RealMetaPRD §6.4):

```python
# Tools/agents/runners DO NOT swallow exceptions. Executor catches at one place.
# Per-node failure short-circuits the run; full traceback into pipeline_events.data.

# In _run_node:
try:
    output = await _dispatch(node, ctx)
except Exception as exc:                                         # noqa: BLE001
    return node.id, None, f"{type(exc).__name__}: {exc}\n{traceback.format_exc()}"

# In runner.run() — translate API errors to AgentResult with finish_reason:
try:
    msg = await client.messages.create(...)
except anthropic.APITimeoutError:
    # ANTHROPIC_SDK_STACK_REFERENCE:1255 — DO NOT retry the LLM call.
    return AgentResult(output=None, finish_reason="timeout", ...)

# In write paths — only `write_tx` may begin transactions:
async with write_tx(db, lock) as conn:                           # REF-SQLITE-BACKBONE:295-316
    await conn.execute(...)                                       # auto BEGIN IMMEDIATE
# commit on context exit; rollback on exception
```

**Logging Pattern** (deliberately spartan in Phase 1):

```python
# stdlib logging.getLogger(__name__); INFO for happy path, ERROR for failure.
# DO NOT log full payloads — they live in pipeline_events.data already.
# DO NOT log secrets — webhook secrets and bearer tokens are NEVER printed.
import logging
logger = logging.getLogger(__name__)
logger.info("dag.layer_started", extra={"run_id": run_id, "layer_index": i})
```

**Money Pattern** (RealMetaPRD §6 line 99 + REF-SQLITE-BACKBONE:419-472):

```python
# NEVER float. Convert at boundaries:
from decimal import Decimal
int_cents = int(round(Decimal(amount_str) * 100))                # str → int cents

# Schemas declare INTEGER NOT NULL with paranoia CHECK:
# debit_cents INTEGER NOT NULL DEFAULT 0,
# credit_cents INTEGER NOT NULL DEFAULT 0,
# CHECK(typeof(debit_cents)='integer'),
# CHECK(NOT (debit_cents > 0 AND credit_cents > 0))

# AI cost is integer micro-USD; cost.py uses // 1_000_000 floor division.
```

**Append-only event pattern** (REF-SQLITE-BACKBONE:340-388):

```python
# pipeline_events: insert-only; never UPDATE, never DELETE.
# pipeline_runs: only the status, completed_at, error columns are mutable;
# every other field is set at INSERT time and frozen.

# external_events / documents: idempotency via INSERT OR IGNORE on the unique key:
await conn.execute(
    "INSERT OR IGNORE INTO external_events (provider, event_id, ...) VALUES (?,?,?...)",
    (provider, event_id, ...),
)
# Re-delivery → no-op. Same shape for documents.sha256.
```

**Single-writer discipline** (REF-SQLITE-BACKBONE:237-288, RealMetaPRD §6.6):

- One `aiosqlite.Connection` per DB on `app.state`.
- One `asyncio.Lock` per DB on `app.state`.
- All writes go through `write_tx(conn, lock)` which:
  1. acquires the lock,
  2. starts `BEGIN IMMEDIATE` (mandatory — busy_timeout does NOT protect
     read→write upgrades),
  3. yields the connection,
  4. `COMMIT` on success, `ROLLBACK` on exception.
- Three locks total (accounting / orchestration / audit). Held independently;
  never nested.

**Compound (multiplicative) confidence** (`04_AGENT_PATTERNS.md:132-140`):

```python
def compound_confidence(values: Iterable[Optional[float]]) -> float:
    """Multiplicative aggregation. None → 0.5 (unknown = half-trust)."""
    vs = [0.5 if v is None else max(0.0, min(1.0, float(v))) for v in values]
    if not vs:
        return 0.0
    out = 1.0
    for v in vs:
        out *= v
    return out
```

Phase 1 only ships the helper; the `confidence_gate` tool that uses it is
Phase D. Floor (`RefusalEngine.CONFIDENCE_FLOOR = 0.50`) lives in
`accounting.confidence_thresholds`, seeded by `0001_init.py`.

**Registry pattern** (`01_ORCHESTRATION_REFERENCE.md:138-151`,
`02_YAML_WORKFLOW_DSL.md:18-34`, RealMetaPRD §6.4):

```python
# Plain dict[str, str] mapping registry_key -> 'module.path:symbol'.
# Lazy import on first call. NO filesystem-scanning decorators.

_TOOL_REGISTRY: dict[str, str] = {
    "tools.noop:run": "backend.orchestration.tools.noop:run",
    # populated by hand; later phases just add lines
}

def get_tool(key: str) -> Callable[..., dict]:
    dotted = _TOOL_REGISTRY[key]                                  # KeyError = misconfigured pipeline
    module_path, attr = dotted.rsplit(":", 1)
    return getattr(importlib.import_module(module_path), attr)
```

**Other Relevant Patterns:**

- **YAML DSL is RealMetaPRD's, not 02_YAML_WORKFLOW_DSL.md's.** The reference
  guide uses `tool_class:` / `agent_class:` (CamelCase keys); RealMetaPRD §7.3
  / §7.4 uses `tool:` / `agent:` with `module.path:symbol` strings. **Follow
  RealMetaPRD.** (PRD1_VALIDATION_BRIEFING C2 chose Path B explicitly.)
- **Conditions are `module.path:symbol` too** — `when: conditions.gating:posted`.
  No `when_class:` / camel-case. The condition function signature is
  `def cond(ctx: AgnesContext) -> bool` (`02_YAML_WORKFLOW_DSL.md:96-120`).
- **No per-node parameters in YAML** (`02_YAML_WORKFLOW_DSL.md:218-225`). All
  state flows through `AgnesContext.trigger_payload` and
  `AgnesContext.node_outputs[upstream_id]`. Phase 1 enforces this by simply
  not adding a `params:` key.
- **Three runners, one shape.** `AgentResult` (RealMetaPRD §7.10) is the
  contract. `test_runner_shape.py` runs the same fixture prompt through all
  three (real Anthropic + stub ADK + stub Pydantic AI) and asserts every
  field is populated.

---

## IMPLEMENTATION PLAN

### Phase 1.A — Schemas, Store, Migrations  (RealMetaPRD §12 Phase A; ~3h)

**Goal:** three DBs open with all PRAGMAs, every schema both bootstrap-able
and migration-replayable.

**Tasks (high level — see STEP-BY-STEP below for atomic units):**

- Create `pyproject.toml`, `pytest.ini`, `.env.example`, `data/blobs/.gitkeep`.
- Create three `schema/{accounting,orchestration,audit}.sql` files by
  copy-pasting the DDL blocks from RealMetaPRD §7.5 (lines 849–1172) verbatim.
- Create `_migrations` runner (`store/migrations.py`) that tracks applied
  migrations per-DB in the `_migrations` table.
- Create `0001_init.py` migrations that simply re-apply each `schema/*.sql`
  inside a guard (idempotent: `CREATE TABLE IF NOT EXISTS` everywhere).
- Implement `store/bootstrap.py:open_dbs()` returning three
  `(connection, lock)` pairs with all eight PRAGMAs.
- Implement `store/writes.py:write_tx(db, lock)` async context manager.
- Bootstrap-replay round-trip test: schema-from-bootstrap == schema-from-
  migration-replay on each DB.

### Phase 1.B — Metalayer (DSL + Executor + Registries + Cache)  (RealMetaPRD §12 Phase B; ~5h)

**Goal:** `noop_demo.yaml` runs end-to-end, emits the canonical event
sequence, caches deterministically, fails fast on cycles.

**Tasks (high level):**

- Implement `context.py` (`AgnesContext` dataclass).
- Implement `yaml_loader.py` with strict-key rejection and required-field
  validation.
- Implement `dag.py` (Kahn topological sort, cycle detection).
- Implement `event_bus.py` (in-process pub/sub; per-run subscriber list with
  TTL reaper) — copy structure from
  `REF-SSE-STREAMING-FASTAPI.md:77–189`.
- Implement `executor.py` with layer-by-layer `asyncio.gather`, fail-fast
  semantics, and `write_event()` dual-write (SQLite + bus per
  REF-SSE-STREAMING-FASTAPI.md:217–249).
- Implement `registries.py` (four flat-dict registries + `_import_dotted`
  helper).
- Implement `cache.py` (read-before-dispatch, write-after-success;
  cache key = `sha256(node_id|code_version|canonical_input)`).
- Implement `runners/base.py` (`AgentResult` dataclass + `AgentRunner`
  Protocol).
- Implement `runners/anthropic_runner.py` with `AsyncAnthropic(timeout=4.5,
  max_retries=2)` and the `submit-tool` extraction pattern.
- Stub `runners/adk_runner.py` and `runners/pydantic_ai_runner.py` that
  import successfully but raise `NotImplementedError` when `.run()` is
  called (live wiring is post-hackathon).
- Tests: cycle rejection, missing registry key, cache hit, fail-fast
  cancellation, `cache_key()` float-drift fixture (RealMetaPRD §12 Phase B
  line 1599–1605).

### Phase 1.C — Audit + Cost + Employees  (RealMetaPRD §12 Phase C; ~2h)

**Goal:** every agent runner writes `(decision, cost, employee_id)` through
one helper; the wedge SQL returns one row per employee from a fixture run.

**Tasks (high level):**

- Implement `prompt_hash.py` (RealMetaPRD §7.8 verbatim).
- Implement `cost.py` (`COST_TABLE_MICRO_USD` and `micro_usd(usage,
  provider, model)` — RealMetaPRD §7.7 verbatim).
- Implement `audit.py:propose_checkpoint_commit(db, lock, *, decision,
  cost, employee_id)` — single function every runner calls *after* the
  upstream model call returns. Writes both `agent_decisions` and
  `agent_costs` rows in one `BEGIN IMMEDIATE` transaction, returning the
  `decision_id` for use in the executor's event payload.
- Wire `AnthropicRunner.run()` to call `propose_checkpoint_commit` after
  every successful (or finish_reason='timeout') invocation.
- Seed `employees` table with three rows in a separate
  `migrations/audit/0002_seed_employees.py` (Tim, Marie, Paul; canonical
  emails per RealMetaPRD §15.2 line 1757; `swan_iban` left NULL for now —
  Phase D fills in).
- Wedge SQL test: build a fixture pipeline run that triggers two stubbed
  agent calls for two different `employee_id`s, then run the SQL from
  RealMetaPRD §7.11 and assert the row shape.

---

## STEP-BY-STEP TASKS

> **Execute every task in order, top to bottom.** Each task is atomic and
> independently testable. Mark a task done only when its `VALIDATE` command
> exits zero. The validation commands assume `pwd = "/home/developer/Projects/HEC Paris"`.

### Task Format Guidelines

- **CREATE**: New files or directories
- **UPDATE**: Modify existing files
- **ADD**: Insert new functionality into existing code
- **REMOVE**: Delete deprecated code
- **REFACTOR**: Restructure without changing behavior
- **MIRROR**: Copy a pattern from elsewhere in the codebase / references

---

### Task 0 — CREATE `pyproject.toml` + `pytest.ini` + `.env.example`

- **IMPLEMENT**: Project metadata for `agnes` package (name, version 0.1.0),
  Python `>= 3.12`, runtime deps:
  `aiosqlite>=0.19`, `pyyaml>=6`, `pydantic>=2.5`, `anthropic>=1.0.0`,
  `httpx>=0.27`, `python-dotenv>=1`, `fastapi>=0.115`, `uvicorn[standard]>=0.30`.
  Dev deps: `pytest>=8`, `pytest-asyncio>=0.23`. Optional extras
  `[adk]=["google-adk>=1.29.0"]`, `[pydantic_ai]=["pydantic-ai-slim[cerebras]"]`.
  `pytest.ini` sets `asyncio_mode = auto`. `.env.example` lists every var from
  RealMetaPRD §9.3 (line 1395–1400) and `Dev orchestration/swan/CLAUDE.md` lines
  94–106 (`SWAN_*`, `ANTHROPIC_API_KEY`, `AGNES_DATA_DIR`, `AGNES_RUNNERS_ENABLED`).
- **PATTERN**: `REF-FASTAPI-BACKEND.md:31-82` for FastAPI version pin.
- **IMPORTS**: n/a (config files).
- **GOTCHA**: Pin `aiosqlite >= 0.19` exactly per `REF-SQLITE-BACKBONE.md:78`.
  `anthropic >= 1.0.0` per `ANTHROPIC_SDK_STACK_REFERENCE.md:34`. Don't add
  `langgraph` (RealMetaPRD §4 explicitly out of scope, line 302).
- **VALIDATE**:
  ```bash
  cd "/home/developer/Projects/HEC Paris" && python -m pip install -e . && python -c "import aiosqlite, yaml, pydantic, anthropic; print('ok')"
  ```

---

### Task 1 — CREATE three `schema/{accounting,orchestration,audit}.sql` files

- **IMPLEMENT**: Copy the DDL blocks from `Orchestration/PRDs/RealMetaPRD.md`
  verbatim:
  - `accounting.sql` ← lines 849–1044 (everything from `swan_transactions`
    down to `_migrations`).
  - `orchestration.sql` ← lines 1048–1109 (`pipeline_runs`,
    `pipeline_events`, `external_events`, `node_cache`, `_migrations`).
  - `audit.sql` ← lines 1114–1171 (`employees`, `agent_decisions`,
    `agent_costs`, `_migrations`).
- **PATTERN**: All tables `STRICT` (RealMetaPRD §7.5 throughout); every JSON
  column has a `CHECK(json_valid(...))` guard; every money column is
  `INTEGER NOT NULL` with paranoia CHECK (`REF-SQLITE-BACKBONE.md:431`).
  Append the three indexes called out in RealMetaPRD lines 869–870, 975–976,
  991, 1076, 1149–1150, 1165–1166.
- **IMPORTS**: n/a.
- **GOTCHA**: SQLite STRICT requires SQLite 3.37+ for STRICT and 3.38+ for
  JSON `->`/`->>` — pin in CI (REF-SQLITE-BACKBONE:86–91). The `_migrations`
  table appears once per DB; do NOT use `CREATE TABLE IF NOT EXISTS` for
  domain tables in `schema/*.sql` — leave them as `CREATE TABLE` (the
  bootstrap path runs against an empty DB; the migration path checks
  `_migrations` first).
- **VALIDATE**:
  ```bash
  cd "/home/developer/Projects/HEC Paris" && for f in backend/orchestration/store/schema/*.sql; do echo "== $f"; sqlite3 :memory: < "$f" && echo OK; done
  ```

---

### Task 2 — CREATE `backend/orchestration/store/bootstrap.py`

- **IMPLEMENT**: `async def open_dbs(data_dir: Path) -> StoreHandles`.
  Returns a frozen dataclass:
  ```python
  @dataclass(frozen=True)
  class StoreHandles:
      accounting: aiosqlite.Connection
      orchestration: aiosqlite.Connection
      audit: aiosqlite.Connection
      accounting_lock: asyncio.Lock
      orchestration_lock: asyncio.Lock
      audit_lock: asyncio.Lock
  ```
  Opens each `aiosqlite.connect(data_dir / "{name}.db")` and runs the
  PRAGMA block in order:
  ```sql
  PRAGMA journal_mode       = WAL;
  PRAGMA foreign_keys       = ON;
  PRAGMA synchronous        = NORMAL;
  PRAGMA busy_timeout       = 5000;
  PRAGMA temp_store         = MEMORY;
  PRAGMA cache_size         = -65536;
  PRAGMA mmap_size          = 134217728;
  PRAGMA wal_autocheckpoint = 1000;
  PRAGMA journal_size_limit = 67108864;
  ```
  Sets `row_factory = aiosqlite.Row`. Creates one `asyncio.Lock` per DB.
  Apply schema if `_migrations` table is empty.
- **PATTERN**: `REF-SQLITE-BACKBONE.md:149-195` (connection lifecycle);
  `REF-SQLITE-BACKBONE.md:199-230` (PRAGMA list with rationale);
  RealMetaPRD §6.6 lines 633–643 (canonical PRAGMA block).
- **IMPORTS**: `aiosqlite`, `asyncio`, `pathlib.Path`, `dataclasses.dataclass`.
- **GOTCHA**: `PRAGMA foreign_keys = ON` is **per-connection**, not per-DB.
  If you ever close and reopen a connection (don't, in Phase 1) you must
  re-set it. Don't open multiple connections per DB — single-writer
  discipline relies on one connection per DB
  (`REF-SQLITE-BACKBONE.md:186-190`).
- **VALIDATE**:
  ```bash
  cd "/home/developer/Projects/HEC Paris" && python -m pytest backend/tests/test_bootstrap.py -q
  ```

---

### Task 3 — CREATE `backend/orchestration/store/writes.py`

- **IMPLEMENT**: `@asynccontextmanager async def write_tx(conn, lock):`
  acquires `lock`, runs `await conn.execute("BEGIN IMMEDIATE")`, yields
  `conn`, then `await conn.commit()` on success or `await conn.rollback()`
  on exception.
- **PATTERN**: `REF-SQLITE-BACKBONE.md:295-316` verbatim.
- **IMPORTS**: `from contextlib import asynccontextmanager`.
- **GOTCHA**: `BEGIN IMMEDIATE` is mandatory — `BEGIN DEFERRED` (the default)
  upgrades to writer mid-transaction and can fail with SQLITE_BUSY even
  with `busy_timeout` set (`REF-SQLITE-BACKBONE.md:237-288`). Never call
  `conn.commit()` outside `write_tx`.
- **VALIDATE**:
  ```bash
  cd "/home/developer/Projects/HEC Paris" && python -m pytest backend/tests/test_bootstrap.py::test_write_tx_commits_and_rolls_back -q
  ```

---

### Task 4 — CREATE `backend/orchestration/store/migrations.py`

- **IMPLEMENT**: `MigrationRunner` class:
  ```python
  class MigrationRunner:
      def __init__(self, db: aiosqlite.Connection, lock: asyncio.Lock,
                   migrations_dir: Path):
          ...
      async def applied(self) -> set[str]: ...
      async def run_unapplied(self) -> list[str]: ...
  ```
  Each migration is a Python module exposing `async def up(conn) -> None`.
  Runner discovers files in `migrations_dir`, sorted by filename, applies
  unapplied ones inside `write_tx`, then inserts a row into `_migrations`
  with `name=<file stem>` and `applied_at=datetime.now(timezone.utc).isoformat()`.
- **PATTERN**: `REF-SQLITE-BACKBONE.md:598-655`; idempotent migrations per
  REF line 644 (each `up()` may be re-applied without error).
- **IMPORTS**: `import importlib.util`, `pathlib`, `datetime`.
- **GOTCHA**: Use `datetime.now(timezone.utc).isoformat()`, NOT
  `datetime.now()` (`REF-SQLITE-BACKBONE.md:705`). If a migration ever does
  a 12-step ALTER rewrite, **re-enable `PRAGMA foreign_keys=ON` as the last
  step** (`REF-SQLITE-BACKBONE.md:685`).
- **VALIDATE**:
  ```bash
  cd "/home/developer/Projects/HEC Paris" && python -m pytest backend/tests/test_migrations.py::test_round_trip_three_dbs -q
  ```

---

### Task 5 — CREATE three `migrations/{accounting,orchestration,audit}/0001_init.py`

- **IMPLEMENT**: Each module exposes `async def up(conn):` that reads the
  sibling `schema/<name>.sql` and runs `await conn.executescript(text)`. The
  same SQL applied either way (bootstrap or migration replay) yields
  identical schema; the round-trip test asserts this.
- **PATTERN**: `REF-SQLITE-BACKBONE.md:609-642`.
- **IMPORTS**: `pathlib.Path`.
- **GOTCHA**: `executescript` does not honour transactions — it `COMMIT`s
  any open transaction at the start. Apply `0001_init.py` outside
  `write_tx` (the bootstrap path), or wrap it in a manual `SAVEPOINT`. For
  Phase 1, run it via `MigrationRunner` which uses `write_tx`; the runner
  must use `await conn.execute(stmt)` per-statement, NOT `executescript`,
  to keep the `BEGIN IMMEDIATE` valid.
- **VALIDATE**:
  ```bash
  cd "/home/developer/Projects/HEC Paris" && python -m pytest backend/tests/test_migrations.py -q
  ```

---

### Task 6 — CREATE `backend/orchestration/context.py`

- **IMPLEMENT**: `AgnesContext` dataclass per RealMetaPRD §6.5 line 547:
  ```python
  @dataclass
  class AgnesContext:
      run_id: int
      pipeline_name: str
      trigger_source: str
      trigger_payload: dict
      node_outputs: dict[str, dict]            # mutated between layers
      store: StoreHandles                      # from bootstrap.py
      employee_id: int | None = None           # populated when triggered with one

      def get(self, node_id: str, default=None) -> dict | None:
          return self.node_outputs.get(node_id, default)
  ```
- **PATTERN**: `01_ORCHESTRATION_REFERENCE.md:114-124`. Adapt: drop
  per-DB-path fields (we hold `StoreHandles` instead); add `employee_id`
  for cost attribution (RealMetaPRD §11 line 1542–1544).
- **IMPORTS**: `dataclasses.dataclass`.
- **GOTCHA**: Phase 1 makes `node_outputs` a plain dict. **Nodes never
  reach into another node's output to write** (`02_YAML_WORKFLOW_DSL.md`
  lines 198–199). Enforced by convention; lint rule lives in PR review.
- **VALIDATE**:
  ```bash
  cd "/home/developer/Projects/HEC Paris" && python -c "from backend.orchestration.context import AgnesContext; print(AgnesContext.__dataclass_fields__.keys())"
  ```

---

### Task 7 — CREATE `backend/orchestration/yaml_loader.py`

- **IMPLEMENT**: `Pipeline` and `PipelineNode` dataclasses + `load(path:
  Path) -> Pipeline`. Per RealMetaPRD §6.4 (lines 521–523) and §7.3
  (lines 716–776), node fields are: `id` (str), `tool` (str | None),
  `agent` (str | None), `runner` (str | None), `depends_on` (list[str]),
  `when` (str | None), `cacheable` (bool, default `False` for agent
  nodes / `True`-ish convention for tools — but **set explicitly per
  pipeline, not auto-defaulted**).
  ```python
  @dataclass(frozen=True)
  class PipelineNode:
      id: str
      tool: str | None = None        # 'tools.x:run'
      agent: str | None = None       # 'agents.x:run'
      runner: str | None = None      # 'anthropic' | 'adk' | 'pydantic_ai'
      depends_on: tuple[str, ...] = ()
      when: str | None = None        # 'conditions.x:fn'
      cacheable: bool = False

  @dataclass(frozen=True)
  class Pipeline:
      name: str
      version: int
      trigger: dict                  # {'source': '...'}
      nodes: tuple[PipelineNode, ...]
  ```
  Loader rejects:
  - both `tool:` and `agent:` set (mutual exclusion;
    `02_YAML_WORKFLOW_DSL.md:71-81`);
  - neither set;
  - duplicate node ids;
  - `depends_on` referencing missing ids;
  - any unknown top-level or per-node key (strict-key rejection;
    RealMetaPRD §12 Phase B line 1590).
- **PATTERN**: `02_YAML_WORKFLOW_DSL.md:18-34, 54-92`; YAML examples in
  RealMetaPRD §7.3 / §7.4 (lines 716–843).
- **IMPORTS**: `yaml.safe_load`, `dataclasses.dataclass`.
- **GOTCHA**: **Use the RealMetaPRD DSL (`tool:` / `agent:` as
  `module.path:symbol`), NOT the reference-guide DSL (`tool_class:` /
  `agent_class:` as CamelCase).** PRD1_VALIDATION_BRIEFING C2 chose Path B
  explicitly. Filename stem must equal the `name:` field — assert in the
  loader (`02_YAML_WORKFLOW_DSL.md:38`).
- **VALIDATE**:
  ```bash
  cd "/home/developer/Projects/HEC Paris" && python -m pytest backend/tests/test_yaml_loader.py -q
  ```

---

### Task 8 — CREATE `backend/orchestration/dag.py`

- **IMPLEMENT**:
  ```python
  def topological_layers(nodes: tuple[PipelineNode, ...]) -> list[list[PipelineNode]]:
      """Kahn's algorithm. Returns layers; raises PipelineLoadError on cycle."""
  ```
  Order: layer 0 = roots; layer N+1 = nodes whose deps are all in earlier
  layers. Detect cycles by checking remaining nodes after the queue empties.
- **PATTERN**: `01_ORCHESTRATION_REFERENCE.md:19-42` verbatim.
- **IMPORTS**: `from collections import defaultdict, deque`.
- **GOTCHA**: Cycle detection error must point to the offending node ids
  ("cycle involving: a, b, c") so pipeline authors can fix it. Use a
  custom `PipelineLoadError` exception, NOT `ValueError`.
- **VALIDATE**:
  ```bash
  cd "/home/developer/Projects/HEC Paris" && python -m pytest backend/tests/test_dag.py -q
  ```

---

### Task 9 — CREATE `backend/orchestration/registries.py`

- **IMPLEMENT**: Four flat-dict registries — `_TOOL_REGISTRY`,
  `_AGENT_REGISTRY`, `_RUNNER_REGISTRY`, `_CONDITION_REGISTRY` — and
  resolver helpers:
  ```python
  def get_tool(key: str) -> Callable[[AgnesContext], dict | Awaitable[dict]]: ...
  def get_agent(key: str) -> Callable[[AgnesContext], Awaitable[AgentResult]]: ...
  def get_runner(key: str) -> "AgentRunner": ...
  def get_condition(key: str) -> Callable[[AgnesContext], bool]: ...
  ```
  Each does `module_path, attr = dotted.rsplit(":", 1); getattr(import_module(module_path), attr)`,
  with an `lru_cache(maxsize=None)` on the resolver.

  Phase 1 seeds:
  ```python
  _TOOL_REGISTRY = {"tools.noop:run": "backend.orchestration.tools.noop:run"}
  _AGENT_REGISTRY = {"agents.noop:run": "backend.orchestration.agents.noop_agent:run"}
  _RUNNER_REGISTRY = {
      "anthropic": "backend.orchestration.runners.anthropic_runner:AnthropicRunner",
      "adk":       "backend.orchestration.runners.adk_runner:AdkRunner",
      "pydantic_ai": "backend.orchestration.runners.pydantic_ai_runner:PydanticAiRunner",
  }
  _CONDITION_REGISTRY = {
      "conditions.gating:passes_confidence": "backend.orchestration.conditions.gating:passes_confidence",
      "conditions.gating:needs_review":      "backend.orchestration.conditions.gating:needs_review",
      "conditions.gating:posted":            "backend.orchestration.conditions.gating:posted",
  }
  ```
- **PATTERN**: `01_ORCHESTRATION_REFERENCE.md:138-151` and
  `04_AGENT_PATTERNS.md:9-32` for tool/agent contracts.
- **IMPORTS**: `importlib`, `functools.lru_cache`, `typing.Callable`,
  `typing.Awaitable`.
- **GOTCHA**: `KeyError` on missing key is the right behavior (signals
  pipeline-author misconfiguration); do not silently fall through. Cache
  resolution but cache the *callable*, not the imported module.
- **VALIDATE**:
  ```bash
  cd "/home/developer/Projects/HEC Paris" && python -m pytest backend/tests/test_registries.py -q
  ```

---

### Task 10 — CREATE `backend/orchestration/event_bus.py`

- **IMPLEMENT**: In-process pub/sub:
  ```python
  _event_bus: dict[int, list[asyncio.Queue]] = {}
  _bus_expiry: dict[int, float] = {}
  _bus_lock = asyncio.Lock()
  _BUS_QUEUE_MAXSIZE = 500
  _BUS_TTL_SECONDS = 120
  _TERMINAL_EVENT_TYPES = {"pipeline_completed", "pipeline_failed"}

  async def get_or_create_bus(run_id: int) -> list[asyncio.Queue]: ...
  async def publish_event(run_id: int, event: dict) -> None: ...
  async def remove_subscriber(run_id: int, q: asyncio.Queue) -> None: ...
  async def cleanup_expired_buses() -> None: ...
  async def bus_reaper_task() -> None: ...      # forever loop, sleep 60s
  ```
- **PATTERN**: `REF-SSE-STREAMING-FASTAPI.md:77-189` verbatim. Phase 1
  ships only the bus; the SSE endpoint that subscribes to it is Phase F.
- **IMPORTS**: `asyncio`, `time`.
- **GOTCHA**: `publish_event` uses `q.put_nowait()` and silently drops on
  `QueueFull`. **Never let a slow client block the producer**
  (`REF-SSE-STREAMING-FASTAPI.md:138`). Multiple queues per run_id (list,
  not single queue) is critical so two subscribers each receive every event
  (`REF-SSE-STREAMING-FASTAPI.md:192-194`).
- **VALIDATE**:
  ```bash
  cd "/home/developer/Projects/HEC Paris" && python -m pytest backend/tests/test_executor.py::test_event_bus_fanout -q
  ```

---

### Task 11 — CREATE `backend/orchestration/cache.py`

- **IMPLEMENT**:
  ```python
  CODE_VERSION = "v1"

  def cache_key(node_id: str, canonical_input: dict) -> str:
      payload = json.dumps(canonical_input, sort_keys=True, separators=(",", ":"),
                           default=_canonical_default)
      return hashlib.sha256(f"{node_id}|{CODE_VERSION}|{payload}".encode()).hexdigest()

  def _canonical_default(obj):
      if isinstance(obj, float):
          return repr(obj)                 # defeat platform float drift
      raise TypeError(...)

  async def lookup(orchestration_db, key: str) -> dict | None: ...
  async def store(orchestration_db, lock, *, key, node_id, pipeline_name,
                  input_json, output_json) -> None: ...
  async def record_hit(orchestration_db, lock, key: str) -> None: ...
  ```
- **PATTERN**: RealMetaPRD §6.4 line 525 (cache key shape); `node_cache`
  schema in §7.5 lines 1092–1104; `REF-SQLITE-BACKBONE.md:418` and
  PRD1_VALIDATION_BRIEFING A2 (float canonicalization).
- **IMPORTS**: `json`, `hashlib`.
- **GOTCHA**: `json.dumps(..., sort_keys=True)` does NOT canonicalize floats
  across platforms (`1.0` vs `1` representation drift). Use
  `repr(float(x))` via the `default=` hook, and forbid `NaN`/`Inf`
  (raise `ValueError` if encountered). `cache_key()` test must cover
  RealMetaPRD §12 Phase B lines 1599–1605 fixture exactly.
- **VALIDATE**:
  ```bash
  cd "/home/developer/Projects/HEC Paris" && python -m pytest backend/tests/test_cache.py -q
  ```

---

### Task 12 — CREATE `backend/orchestration/runners/base.py`

- **IMPLEMENT**: `AgentResult` dataclass + `AgentRunner` Protocol +
  `TokenUsage` dataclass:
  ```python
  @dataclass(frozen=True)
  class TokenUsage:
      input_tokens: int = 0
      output_tokens: int = 0
      cache_read_tokens: int = 0
      cache_write_tokens: int = 0
      reasoning_tokens: int = 0

  @dataclass(frozen=True)
  class AgentResult:
      output: Any
      model: str
      response_id: str | None
      prompt_hash: str
      alternatives: list[dict] | None
      confidence: float | None
      usage: TokenUsage
      latency_ms: int
      finish_reason: str | None
      temperature: float | None
      seed: int | None

  class AgentRunner(Protocol):
      async def run(self, *, ctx: AgnesContext, system: str, tools: list[dict],
                    messages: list[dict], model: str, temperature: float = 0.0,
                    max_tokens: int = 1024, deadline_s: float = 4.5,
                    seed: int | None = None) -> AgentResult: ...
  ```
- **PATTERN**: RealMetaPRD §7.10 (lines 1265–1301) verbatim;
  `ANTHROPIC_SDK_STACK_REFERENCE.md:1087-1107`.
- **IMPORTS**: `from typing import Protocol, Any`,
  `dataclasses.dataclass`.
- **GOTCHA**: Frozen dataclass — `AgentResult` is immutable. Runtimes
  zero-fill missing usage fields (Pydantic AI doesn't split cache_read
  vs cache_write — RealMetaPRD §7.10.1 line 1298–1301).
- **VALIDATE**:
  ```bash
  cd "/home/developer/Projects/HEC Paris" && python -c "from backend.orchestration.runners.base import AgentResult, TokenUsage, AgentRunner; print('ok')"
  ```

---

### Task 13 — CREATE `backend/orchestration/prompt_hash.py`

- **IMPLEMENT**: RealMetaPRD §7.8 lines 1227–1240 verbatim:
  ```python
  def prompt_hash(model: str, system: str, tools: list, messages: list) -> str:
      last_user = next((m for m in reversed(messages) if m["role"] == "user"), None)
      canonical = json.dumps(
          {"model": model, "system": system, "tools": tools, "user": last_user},
          sort_keys=True,
          separators=(",", ":"),
      )
      return hashlib.sha256(canonical.encode()).hexdigest()[:16]
  ```
- **PATTERN**: `ANTHROPIC_SDK_STACK_REFERENCE.md:901-914` — hash includes
  `model`, hashes only the *last* user message (PRD1_VALIDATION_BRIEFING C4).
- **IMPORTS**: `json`, `hashlib`.
- **GOTCHA**: Hashing all messages or omitting `model` causes silent cache
  collision on model swap. **Do not refactor "for clarity"** — this exact
  formula is the audit boundary.
- **VALIDATE**:
  ```bash
  cd "/home/developer/Projects/HEC Paris" && python -m pytest backend/tests/test_prompt_hash.py -q
  ```

---

### Task 14 — CREATE `backend/orchestration/cost.py`

- **IMPLEMENT**: RealMetaPRD §7.7 lines 1192–1216 verbatim. The
  `COST_TABLE_MICRO_USD` dict and `micro_usd(usage, provider, model) -> int`
  function. Add a `# verified 2026-04-25` comment at module top.
- **PATTERN**: `ANTHROPIC_SDK_STACK_REFERENCE.md:519-565`,
  `CEREBRAS_STACK_REFERENCE.md:329-366`.
- **IMPORTS**: none beyond dataclasses.
- **GOTCHA**: Integer division `// 1_000_000` floors. The PRD prices were
  pinned 2026-04-25; refresh monthly. `KeyError` on unknown
  `(provider, model)` is the right behavior — fail loudly.
- **VALIDATE**:
  ```bash
  cd "/home/developer/Projects/HEC Paris" && python -m pytest backend/tests/test_cost.py -q
  ```

---

### Task 15 — CREATE `backend/orchestration/audit.py`

- **IMPLEMENT**:
  ```python
  async def propose_checkpoint_commit(
      *, audit_db, audit_lock, run_id: int, node_id: str,
      result: AgentResult, runner: str, employee_id: int | None,
      provider: str,
  ) -> int:
      """Write one agent_decisions + one agent_costs row in a single
      BEGIN IMMEDIATE transaction. Returns the agent_decisions.id."""
      async with write_tx(audit_db, audit_lock) as conn:
          cur = await conn.execute(
              "INSERT INTO agent_decisions (run_id_logical, node_id, source, runner, "
              "model, response_id, prompt_hash, alternatives_json, confidence, "
              "latency_ms, finish_reason, temperature, seed, completed_at) "
              "VALUES (?, ?, 'agent', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
              (run_id, node_id, runner, result.model, result.response_id,
               result.prompt_hash,
               json.dumps(result.alternatives) if result.alternatives else None,
               result.confidence, result.latency_ms, result.finish_reason,
               result.temperature, result.seed,
               datetime.now(timezone.utc).isoformat()),
          )
          decision_id = cur.lastrowid
          cost = micro_usd(result.usage, provider, result.model)
          await conn.execute(
              "INSERT INTO agent_costs (decision_id, employee_id, provider, model, "
              "input_tokens, output_tokens, cache_read_tokens, cache_write_tokens, "
              "reasoning_tokens, cost_micro_usd) "
              "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
              (decision_id, employee_id, provider, result.model,
               result.usage.input_tokens, result.usage.output_tokens,
               result.usage.cache_read_tokens, result.usage.cache_write_tokens,
               result.usage.reasoning_tokens, cost),
          )
          return decision_id
  ```
- **PATTERN**: RealMetaPRD §6.4 lines 529–533 (`propose → checkpoint →
  commit`); `04_AGENT_PATTERNS.md:176-191` (append-only ledger);
  PRD1_VALIDATION_BRIEFING G3 (the seven extra agent_decisions columns).
- **IMPORTS**: `json`, `datetime`, `cost.micro_usd`,
  `store.writes.write_tx`.
- **GOTCHA**: Both INSERTs must be in **one** `write_tx` block — partial
  writes here corrupt the audit story (RealMetaPRD §14 risk #8). The
  `agent_costs.decision_id` is `PRIMARY KEY REFERENCES agent_decisions(id)`,
  so the order matters: decision first, cost second.
- **VALIDATE**:
  ```bash
  cd "/home/developer/Projects/HEC Paris" && python -m pytest backend/tests/test_audit.py -q
  ```

---

### Task 16 — CREATE `backend/orchestration/runners/anthropic_runner.py`

- **IMPLEMENT**: Real runner. One module-level `AsyncAnthropic(timeout=4.5,
  max_retries=2)` per RealMetaPRD §7.9 line 1249 and
  `ANTHROPIC_SDK_STACK_REFERENCE.md:41-56`. The `run()` method:

  1. records `start = time.monotonic()`,
  2. computes `prompt_hash(model, system, tools, messages)`,
  3. calls `client.messages.create(model=..., system=..., tools=...,
     messages=..., max_tokens=..., temperature=..., extra_headers={})`,
  4. on `APITimeoutError`: returns `AgentResult(output=None,
     finish_reason='timeout', usage=TokenUsage(), latency_ms=...,
     prompt_hash=...)` — does NOT retry the LLM call (RealMetaPRD §7.9
     line 1255–1259),
  5. on success: extracts the forced `submit_*` tool's input as `output`,
     reads `msg.usage` into `TokenUsage` (note Anthropic's `usage` has
     `cache_creation_input_tokens` / `cache_read_input_tokens` — map to
     `cache_write_tokens` / `cache_read_tokens`),
  6. returns `AgentResult(...)`.

  The runner does NOT call `propose_checkpoint_commit` directly; the
  *executor* does that, after receiving the `AgentResult` (Task 18).
- **PATTERN**: `ANTHROPIC_SDK_STACK_REFERENCE.md:186-269` (tool-use loop) for
  the structure — but Phase 1 is one-shot, no loop; the loop is Phase D.
  `:300-323` for `submit-tool` extraction.
- **IMPORTS**: `import anthropic`, `import time`,
  `from .base import AgentResult, TokenUsage`,
  `from ..prompt_hash import prompt_hash`.
- **GOTCHA**: Anthropic's `usage` exposes `cache_creation_input_tokens` and
  `cache_read_input_tokens` (not `cache_write_tokens` / `cache_read_tokens`).
  Map them in the runner (the `TokenUsage` dataclass uses our names).
  `max_tokens` is REQUIRED — ANTHROPIC_SDK_STACK_REFERENCE:1080 — set
  default 1024, override per call. Append assistant content **verbatim** —
  never modify blocks (line 237).
- **VALIDATE**:
  ```bash
  cd "/home/developer/Projects/HEC Paris" && python -m pytest backend/tests/test_runner_shape.py::test_anthropic_runner_shape -q
  ```

---

### Task 17 — CREATE stubs `runners/adk_runner.py` and `runners/pydantic_ai_runner.py`

- **IMPLEMENT**: Each module declares an `AdkRunner` (resp. `PydanticAiRunner`)
  class implementing `AgentRunner`. The `run()` method raises
  `NotImplementedError("ADK runner stub-only in Phase 1; see RealMetaPRD §14 risk #4")`
  but accepts the same kwargs and produces the same `AgentResult` shape
  in test fixtures (the tests inject a fake `_run_impl` that returns a
  fixture `AgentResult`; `test_runner_shape.py` asserts every field is
  populated identically across the three).
- **PATTERN**: PRD1_VALIDATION_BRIEFING C5 (ADK is stub-only);
  RealMetaPRD §4 line 162 (the runners ship "behind optional extras").
- **IMPORTS**: `from .base import AgentResult, AgentRunner, TokenUsage`.
- **GOTCHA**: Don't import `google.adk` or `pydantic_ai` at module top —
  optional extras may not be installed. Wrap `import` inside `_run_impl`
  if/when wiring real ADK/Pydantic-AI in a later phase.
- **VALIDATE**:
  ```bash
  cd "/home/developer/Projects/HEC Paris" && python -m pytest backend/tests/test_runner_shape.py -q
  ```

---

### Task 18 — CREATE `backend/orchestration/executor.py`

- **IMPLEMENT**:
  ```python
  async def execute_pipeline(
      pipeline_name: str, *, trigger_source: str, trigger_payload: dict,
      store: StoreHandles, employee_id: int | None = None,
  ) -> int:
      """Public entrypoint. Inserts pipeline_runs, schedules _execute as a
      background task via asyncio.create_task, returns run_id immediately."""
      ...

  async def _execute(ctx: AgnesContext, pipeline: Pipeline) -> None:
      """The orchestrator. Builds layers, runs them, writes events."""
      await write_event(ctx, "pipeline_started", None, {"pipeline_name": pipeline.name})
      try:
          for layer_index, layer in enumerate(topological_layers(pipeline.nodes)):
              results = await asyncio.gather(
                  *[_run_node(node, ctx) for node in layer],
                  return_exceptions=True,
              )
              for (node, outcome) in zip(layer, results):
                  if isinstance(outcome, Exception):
                      await write_event(ctx, "node_failed", node.id,
                                        {"error": repr(outcome)})
                      await update_run_status(ctx, "failed", str(outcome))
                      return
                  node_id, output, error, was_skipped = outcome
                  if error:
                      await write_event(ctx, "node_failed", node_id, {"error": error})
                      await update_run_status(ctx, "failed", error)
                      return
                  if was_skipped:
                      await write_event(ctx, "node_skipped", node_id, {})
                  else:
                      ctx.node_outputs[node_id] = output
                      await write_event(ctx, "node_completed", node_id,
                                        {"node_output": output})
          await write_event(ctx, "pipeline_completed", None, {})
          await update_run_status(ctx, "completed", None)
      except Exception as exc:                                     # noqa: BLE001
          await write_event(ctx, "pipeline_failed", None,
                            {"error": f"{type(exc).__name__}: {exc}"})
          await update_run_status(ctx, "failed", str(exc))

  async def _run_node(node: PipelineNode, ctx: AgnesContext):
      """Per-node wrapper. Evaluates `when`, dispatches to tool/agent,
      checks cache, captures elapsed_ms, returns (id, output, error, was_skipped)."""
      ...

  async def write_event(ctx, event_type, node_id, data: dict) -> None:
      """Dual-write: pipeline_events (orchestration.db) + event_bus.publish_event."""
      ...
  ```
  Cache integration: `_run_node` for a `cacheable: true` node first calls
  `cache.lookup(orchestration_db, key)`. On hit it emits `cache_hit` event,
  records hit, and returns the cached output as if the node ran. On miss
  it dispatches; on success it stores the output back.

  Agent dispatch: resolves `runner` via registry, calls `runner.run(...)`,
  takes the returned `AgentResult`, and immediately calls
  `audit.propose_checkpoint_commit(...)` to write
  `agent_decisions` + `agent_costs`. The `AgentResult.output` is what
  becomes `ctx.node_outputs[node.id]`.
- **PATTERN**: `01_ORCHESTRATION_REFERENCE.md:49-105` for the inner loop;
  RealMetaPRD §6.4 line 504 for `asyncio.gather` semantics;
  REF-SSE-STREAMING-FASTAPI:217–249 for `write_event` dual-write.
- **IMPORTS**: `asyncio`, `time`, `traceback`, all the modules above.
- **GOTCHA**: **`return_exceptions=True` in `asyncio.gather`** — first
  exception in a layer triggers `pipeline_failed` and we MUST NOT swallow
  remaining sibling exceptions silently. Iterate over results; record
  each. Tool dispatch must use `loop.run_in_executor(None, fn, ctx)` for
  sync tools so the event loop stays unblocked
  (`01_ORCHESTRATION_REFERENCE.md:65`). Agent calls are async — `await`
  them directly. **Skipped nodes return `None` as output and downstream
  reads `ctx.get('skipped_id', {})` defensively** (see RealMetaPRD §6.4
  line 504; conditions.gating must handle missing dependencies).
- **VALIDATE**:
  ```bash
  cd "/home/developer/Projects/HEC Paris" && python -m pytest backend/tests/test_executor.py -q
  ```

---

### Task 19 — CREATE `backend/orchestration/conditions/gating.py` (stubs)

- **IMPLEMENT**: Three condition functions:
  ```python
  def passes_confidence(ctx: AgnesContext) -> bool:
      gate = ctx.get("gate-confidence", {}) or {}
      return bool(gate.get("ok"))

  def needs_review(ctx: AgnesContext) -> bool:
      gate = ctx.get("gate-confidence", {}) or {}
      return bool(gate.get("needs_review"))

  def posted(ctx: AgnesContext) -> bool:
      pe = ctx.get("post-entry", {}) or {}
      return pe.get("status") == "posted"
  ```
  Phase 1 ships them as stubs (`gate-confidence` and `post-entry` aren't
  in `noop_demo.yaml`). They land here so Phase D doesn't have to think
  about wiring them.
- **PATTERN**: `02_YAML_WORKFLOW_DSL.md:96-120`; defensive read pattern
  (`ctx.get(..., {}) or {}`) per `02_YAML_WORKFLOW_DSL.md:198-199`.
- **IMPORTS**: `..context.AgnesContext`.
- **GOTCHA**: Conditions are pure functions of `ctx`. **No I/O, no
  randomness, no globals.** They must be unit-testable in isolation
  (`02_YAML_WORKFLOW_DSL.md:96`).
- **VALIDATE**:
  ```bash
  cd "/home/developer/Projects/HEC Paris" && python -c "from backend.orchestration.conditions.gating import passes_confidence, needs_review, posted; print('ok')"
  ```

---

### Task 20 — CREATE `pipelines/noop_demo.yaml` + `tools/noop.py` + `agents/noop_agent.py`

- **IMPLEMENT**:
  - `pipelines/noop_demo.yaml`:
    ```yaml
    name: noop_demo
    version: 1
    trigger: { source: manual }
    nodes:
      - id: tool-a
        tool: tools.noop:run
        cacheable: true

      - id: agent-b
        agent: agents.noop:run
        runner: anthropic
        depends_on: [tool-a]
        cacheable: false

      - id: tool-c
        tool: tools.noop:run
        depends_on: [agent-b]
    ```
  - `tools/noop.py`:
    ```python
    def run(ctx) -> dict:
        return {"echo": ctx.trigger_payload, "node_outputs_seen": list(ctx.node_outputs.keys())}
    ```
    (sync, returns dict; tested via `run_in_executor`).
  - `agents/noop_agent.py`:
    ```python
    async def run(ctx) -> AgentResult:
        runner = get_runner("anthropic")
        return await runner.run(
            ctx=ctx,
            system="You are a test agent. Reply with the literal string 'ok'.",
            tools=[],
            messages=[{"role": "user", "content": "ping"}],
            model="claude-haiku-4-5",
            max_tokens=64, temperature=0.0,
        )
    ```
    In tests, the `AnthropicRunner` is monkey-patched to return a
    deterministic `AgentResult`.
- **PATTERN**: `04_AGENT_PATTERNS.md:9-54` (tool/agent contracts). The
  YAML uses RealMetaPRD's `tool:` / `agent:` keys (NOT `tool_class:`).
- **IMPORTS**: per file above.
- **GOTCHA**: This pipeline is the smoke test for Phase 1. If it doesn't
  emit exactly: `pipeline_started`, `node_started(tool-a)`,
  `node_completed(tool-a)`, `node_started(agent-b)`,
  `node_completed(agent-b)`, `node_started(tool-c)`,
  `node_completed(tool-c)`, `pipeline_completed` (8 events), the
  executor is wrong. RealMetaPRD §11 line 1554: "A 5-node clean run
  produces exactly 12 `pipeline_events` rows."
- **VALIDATE**:
  ```bash
  cd "/home/developer/Projects/HEC Paris" && python -m pytest backend/tests/test_executor.py::test_noop_demo_full_run -q
  ```

---

### Task 21 — CREATE `migrations/audit/0002_seed_employees.py`

- **IMPLEMENT**:
  ```python
  async def up(conn):
      now = datetime.now(timezone.utc).isoformat()
      await conn.executemany(
          "INSERT OR IGNORE INTO employees (email, full_name, department, active) "
          "VALUES (?, ?, ?, 1)",
          [
              ("tim@hec.example",   "Tim",   "Founder"),
              ("marie@hec.example", "Marie", "Engineering"),
              ("paul@hec.example",  "Paul",  "Operations"),
          ],
      )
  ```
- **PATTERN**: RealMetaPRD §15.2 line 1757 (Tim / Marie / Paul);
  `04_AGENT_PATTERNS.md:176-191` (idempotent inserts).
- **IMPORTS**: `datetime`.
- **GOTCHA**: `swan_iban` and `swan_account_id` left NULL — Phase D
  populates them when seed Swan data lands. The `email` UNIQUE constraint
  + `INSERT OR IGNORE` makes re-application safe.
- **VALIDATE**:
  ```bash
  cd "/home/developer/Projects/HEC Paris" && python -m pytest backend/tests/test_employee_ledger.py -q
  ```

---

### Task 22 — CREATE `backend/api/main.py` (lifespan + /healthz only)

- **IMPLEMENT**:
  ```python
  @asynccontextmanager
  async def lifespan(app: FastAPI):
      data_dir = Path(os.environ.get("AGNES_DATA_DIR", "./data"))
      data_dir.mkdir(parents=True, exist_ok=True)
      app.state.store = await open_dbs(data_dir)
      app.state.bus_reaper = asyncio.create_task(bus_reaper_task())
      try:
          yield
      finally:
          app.state.bus_reaper.cancel()
          await app.state.store.close()

  app = FastAPI(lifespan=lifespan)

  @app.get("/healthz")
  async def healthz():
      return {"status": "ok"}
  ```
  Phase 1 ships ONLY `/healthz`. Webhook routes, document upload, runs API,
  and SSE arrive in Phase D / E / F.
- **PATTERN**: `REF-FASTAPI-BACKEND.md:31-82, 170-223` (lifespan + background
  tasks).
- **IMPORTS**: `from contextlib import asynccontextmanager`,
  `from fastapi import FastAPI`, `os`, `pathlib.Path`,
  `..orchestration.store.bootstrap.open_dbs`,
  `..orchestration.event_bus.bus_reaper_task`.
- **GOTCHA**: Single uvicorn worker only — RealMetaPRD §9.5 line 1419 and
  REF-FASTAPI-BACKEND:767. Multi-worker breaks the per-DB asyncio.Lock
  invariant. Document in code comment + README.
- **VALIDATE**:
  ```bash
  cd "/home/developer/Projects/HEC Paris" && uvicorn backend.api.main:app --workers 1 --port 8000 &
  sleep 2 && curl -fsS http://127.0.0.1:8000/healthz && kill %1
  ```

---

### Task 23 — CREATE `backend/tests/conftest.py` and shared fixtures

- **IMPLEMENT**:
  - `tmp_data_dir` fixture: `tmp_path` per test.
  - `store` fixture: `await open_dbs(tmp_data_dir)` then run all migrations
    via `MigrationRunner`.
  - `fake_anthropic` fixture: monkey-patches `anthropic_runner.client` with
    a stub that returns a fixture `AgentResult`. Records every call so
    tests can inspect `model`, `messages`, `tools`.
  - `pytest.ini` with `asyncio_mode = auto`.
- **PATTERN**: RealMetaPRD §12 Phase B test list (line 1606–1607).
- **IMPORTS**: `pytest`, `pytest_asyncio`.
- **GOTCHA**: Use `tmp_path` (per-test isolation), not a shared
  `data/` dir — the round-trip test must start from an empty directory.
- **VALIDATE**:
  ```bash
  cd "/home/developer/Projects/HEC Paris" && python -m pytest backend/tests/conftest.py --collect-only -q
  ```

---

### Task 24 — CREATE the test suite (12 files listed in "New Files to Create")

- **IMPLEMENT**: One test file per validation listed below. Each file
  imports from `backend.orchestration.*` and `backend.orchestration.store.*`.
  Use `pytest.mark.asyncio` (or rely on `asyncio_mode=auto`).

  - `test_bootstrap.py` — opens the three DBs in `tmp_path`, asserts every
    PRAGMA returns the expected value (`PRAGMA journal_mode` → "wal", etc.),
    asserts three distinct `asyncio.Lock` instances, asserts `write_tx`
    commits on success and rolls back on exception.
  - `test_migrations.py` — round-trip on all three DBs:
    `dump_schema(bootstrap_db) == dump_schema(replay_db)`, where
    `dump_schema` is `await conn.execute("SELECT sql FROM sqlite_master ORDER BY name")`.
  - `test_yaml_loader.py` — strict-key rejection (unknown top-level key,
    unknown per-node key); both `tool` and `agent` set → error;
    duplicate `id` → error; `depends_on` → missing id → error.
  - `test_dag.py` — cycle (A→B→A) raises `PipelineLoadError`; diamond
    (A→{B,C}→D) yields three layers `[[A],[B,C],[D]]`.
  - `test_executor.py` — `noop_demo` produces 8 events in order; agent-b
    produces one `agent_decisions` + one `agent_costs` row;
    fail-fast — inject a tool that raises in layer 1 and assert layer 2
    nodes never start; `cache_hit` event on second run (tool-a cacheable);
    bus fan-out (two queues, both receive every event).
  - `test_registries.py` — lazy import; missing key → `KeyError`; resolution
    cached.
  - `test_cache.py` — `cache_key` invariants per RealMetaPRD §12 Phase B
    lines 1599–1605: float `1.0` vs `1` → different keys; nested arrays in
    different insertion order → same key; whitespace in nested string →
    different key. `lookup`/`store`/`record_hit` round trip.
  - `test_audit.py` — `propose_checkpoint_commit` writes both rows
    atomically (induced exception leaves both absent).
  - `test_cost.py` — `micro_usd` for each model in the table; sample
    usage matches a hand-computed expected value.
  - `test_prompt_hash.py` — model swap → different hash; whitespace in
    last user message → different hash; tool reorder → different hash;
    leading messages don't change hash (only the *last* user message
    contributes).
  - `test_employee_ledger.py` — seeds three employees + three runs (one
    per employee) with stub agent results; runs the wedge SQL from
    RealMetaPRD §7.11 and asserts one row per employee with non-zero cost.
  - `test_runner_shape.py` — calls `AnthropicRunner.run()` with a fake
    client, calls `AdkRunner.run()` and `PydanticAiRunner.run()` with
    monkey-patched `_run_impl` that returns a fixture `AgentResult`,
    asserts every field of `AgentResult` is identically-typed across
    the three.
- **PATTERN**: RealMetaPRD §11 line 1547–1556 (functional + quality
  indicators); §12 Phase A/B/C exit checklists.
- **IMPORTS**: per test.
- **GOTCHA**: ATTACH for the wedge query —
  `ATTACH DATABASE 'audit.db' AS audit` requires the path; in tests use
  `tmp_path / "audit.db"`. The wedge SQL itself is RealMetaPRD §7.11
  lines 1305–1318 verbatim.
- **VALIDATE**:
  ```bash
  cd "/home/developer/Projects/HEC Paris" && python -m pytest backend/tests/ -q
  ```

---

## TESTING STRATEGY

> **Test framework:** `pytest` + `pytest-asyncio`. `asyncio_mode = auto`.
> **All tests run against `tmp_path` databases** — no shared state.

### Unit Tests

Per RealMetaPRD §11 line 1550 ("All tests pass under 30 seconds on a
developer laptop"), the full suite must complete fast:

- Bootstrap, PRAGMA assertions, lock identity (1s).
- Migration round-trip (1s per DB → 3s).
- YAML loader strict-key + structural validation (<1s).
- DAG cycle / diamond (<1s).
- Executor noop run (real PRAGMAs + fake AsyncAnthropic) (~2s).
- Cache key fixtures (<1s).
- Audit + cost + employee wedge (~2s).
- Runner shape parity (3 runners) (~1s).
- Prompt hash invariants (<1s).

Total target: ≤15s on dev laptop.

Design tests with the `store` fixture (per-test temp DBs, all migrations
applied) plus a `fake_anthropic` fixture that records calls.

### Integration Tests

Phase 1 has one integration-shape test: **`test_executor.py::test_noop_demo_full_run`**.
It exercises:
- bootstrap.open_dbs (real PRAGMAs)
- yaml_loader.load
- dag.topological_layers
- executor.execute_pipeline + _execute + _run_node
- registries.get_tool / get_agent / get_runner
- cache.lookup / store
- runners/anthropic_runner (with fake client)
- audit.propose_checkpoint_commit
- event_bus.publish_event

If this test passes, Phase 1 is done.

### Edge Cases

Phase 1 must cover:

- [ ] Bootstrap-replay parity on all three DBs (RealMetaPRD §11 line 1545).
- [ ] Idempotent migrations: re-running `MigrationRunner.run_unapplied()`
      twice has no effect.
- [ ] Cycle detection produces a useful error message (cite the offending
      node ids).
- [ ] Cache key differs across float representation (`1` vs `1.0`).
- [ ] Cache key matches across dict key insertion order.
- [ ] `BEGIN IMMEDIATE` rolls back on exception inside `write_tx`.
- [ ] `audit.propose_checkpoint_commit` is atomic — partial failure leaves
      both rows absent.
- [ ] Skipped node (when=false) emits `node_skipped`; downstream node with
      `depends_on: [skipped]` defensively reads via `ctx.get(..., {})`.
- [ ] Fail-fast — an exception in layer 1 prevents layer 2 from running;
      remaining layer 1 nodes complete or are recorded as cancelled.
- [ ] Three runners produce identical `AgentResult` field shape (RealMetaPRD
      §11 line 1538–1540).
- [ ] Wedge query (RealMetaPRD §7.11) returns one row per employee for a
      seeded fixture.
- [ ] Out-of-the-box single-process invariant: `--workers 1` documented;
      two workers would break per-DB locks (RealMetaPRD §9.5 line 1419).

---

## VALIDATION COMMANDS

> Execute every command. All must exit zero before Phase 1 is complete.

### Level 1: Syntax & Style

```bash
cd "/home/developer/Projects/HEC Paris" && python -m compileall backend/ -q && echo PASS
```

```bash
cd "/home/developer/Projects/HEC Paris" && python -c "
import importlib
for mod in [
    'backend.api.main',
    'backend.orchestration.context',
    'backend.orchestration.dag',
    'backend.orchestration.executor',
    'backend.orchestration.registries',
    'backend.orchestration.cache',
    'backend.orchestration.cost',
    'backend.orchestration.prompt_hash',
    'backend.orchestration.audit',
    'backend.orchestration.event_bus',
    'backend.orchestration.yaml_loader',
    'backend.orchestration.runners.base',
    'backend.orchestration.runners.anthropic_runner',
    'backend.orchestration.runners.adk_runner',
    'backend.orchestration.runners.pydantic_ai_runner',
    'backend.orchestration.store.bootstrap',
    'backend.orchestration.store.writes',
    'backend.orchestration.store.migrations',
]:
    importlib.import_module(mod)
print('PASS')
"
```

```bash
# Float-arithmetic-on-money guard (RealMetaPRD §11 line 1551)
cd "/home/developer/Projects/HEC Paris" && \
  ! grep -rEn '\b(float|/(?!/))[^\n]*(cents|amount|price|cost)' backend/ --include='*.py' || \
  (echo "FAIL: float arithmetic on money path detected"; false)
```

### Level 2: Unit Tests

```bash
cd "/home/developer/Projects/HEC Paris" && python -m pytest backend/tests/ -q --maxfail=1
```

Expected: all tests pass, total runtime ≤ 30s.

```bash
# Phase A — schemas + bootstrap-replay
cd "/home/developer/Projects/HEC Paris" && python -m pytest backend/tests/test_bootstrap.py backend/tests/test_migrations.py -v
```

```bash
# Phase B — metalayer
cd "/home/developer/Projects/HEC Paris" && python -m pytest backend/tests/test_yaml_loader.py backend/tests/test_dag.py backend/tests/test_executor.py backend/tests/test_registries.py backend/tests/test_cache.py backend/tests/test_runner_shape.py -v
```

```bash
# Phase C — audit + cost + employees
cd "/home/developer/Projects/HEC Paris" && python -m pytest backend/tests/test_audit.py backend/tests/test_cost.py backend/tests/test_prompt_hash.py backend/tests/test_employee_ledger.py -v
```

### Level 3: Integration Tests

```bash
# noop_demo end-to-end with the real executor + real PRAGMAs
cd "/home/developer/Projects/HEC Paris" && python -m pytest backend/tests/test_executor.py::test_noop_demo_full_run -v
```

```bash
# 12-event invariant (RealMetaPRD §11 line 1554) — adapt for noop's 3 nodes
# Expected: 1 pipeline_started + 3*2 (started+completed) + 1 pipeline_completed = 8 events
cd "/home/developer/Projects/HEC Paris" && python -m pytest backend/tests/test_executor.py::test_event_count_invariant -v
```

### Level 4: Manual Validation

```bash
# Boot the API, hit /healthz, observe lifespan tasks start/stop cleanly
cd "/home/developer/Projects/HEC Paris" && AGNES_DATA_DIR=./data uvicorn backend.api.main:app --workers 1 --port 8000 &
APP_PID=$!
sleep 2
curl -fsS http://127.0.0.1:8000/healthz
kill $APP_PID
```

```bash
# Schemas exist on disk after first boot
cd "/home/developer/Projects/HEC Paris" && ls -la data/*.db && \
  for db in accounting orchestration audit; do
    echo "== $db.db =="
    sqlite3 "data/$db.db" "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name;"
  done
```

```bash
# Wedge SQL on a seeded run (manual rehearsal of RealMetaPRD §11 line 1519)
cd "/home/developer/Projects/HEC Paris" && python -c "
import asyncio, aiosqlite
from pathlib import Path
async def main():
    async with aiosqlite.connect('data/audit.db') as conn:
        async for row in await conn.execute(
            'SELECT email, COUNT(*) as calls, COALESCE(SUM(c.cost_micro_usd),0)/1e6 as usd '
            'FROM employees e LEFT JOIN agent_costs c ON c.employee_id = e.id '
            'GROUP BY e.id ORDER BY usd DESC'
        ):
            print(row)
asyncio.run(main())
"
```

### Level 5: Additional Validation (Optional)

If `claude-code-guide` MCP is configured, ask the agent to verify the
plan's Anthropic-SDK usage pattern matches current best practice. If
`agent-browser` is configured, no Phase 1 use case (frontend is Phase F).

---

## ACCEPTANCE CRITERIA

- [ ] Three SQLite DB files (`accounting.db` / `orchestration.db` /
      `audit.db`) created on first boot under `${AGNES_DATA_DIR:-./data}/`
      with all eight PRAGMAs (RealMetaPRD §6.6).
- [ ] All three databases have populated `_migrations` rows after first
      boot (`0001_init` + `0002_seed_employees` for audit).
- [ ] Schema-from-bootstrap == schema-from-migration-replay on all three
      DBs (RealMetaPRD §12 Phase A line 1582).
- [ ] `noop_demo.yaml` runs end-to-end producing 8 `pipeline_events` rows
      in the correct order (RealMetaPRD §11 line 1554 invariant adapted).
- [ ] Executor fail-fast: a tool that raises in layer 1 produces
      `node_failed` + `pipeline_failed`, downstream layer never starts.
- [ ] `cache_hit` event emitted on second run of `noop_demo` for the
      cacheable `tool-a` node.
- [ ] Three runners (`anthropic` / `adk` / `pydantic_ai`) populate identical
      `AgentResult` fields under fake clients.
- [ ] Every agent invocation through `AnthropicRunner` produces exactly one
      `agent_decisions` row + exactly one `agent_costs` row (RealMetaPRD
      §11 line 1541).
- [ ] Wedge SQL (RealMetaPRD §7.11) returns one row per employee for the
      fixture run.
- [ ] `cache_key()` round-trip test passes the float-drift fixture
      (RealMetaPRD §12 Phase B lines 1599–1605).
- [ ] `prompt_hash()` test asserts model swap + last-message-only behavior
      (PRD1_VALIDATION_BRIEFING C4).
- [ ] `propose_checkpoint_commit` is atomic — induced exception leaves
      both rows absent.
- [ ] Single-uvicorn-worker assumption documented in code comment in
      `api/main.py` and in README (RealMetaPRD §9.5 line 1419).
- [ ] No floats anywhere on a money path (Level 1 grep guard passes).
- [ ] Zero linting errors; full suite ≤ 30s; `python -m compileall` exits 0.

---

## COMPLETION CHECKLIST

- [ ] Tasks 0–24 completed in order.
- [ ] Each task's `VALIDATE` command passed at the time of task completion.
- [ ] All Level 1 / 2 / 3 validation commands pass at end of Phase 1.
- [ ] `data/` directory contains three `.db` files, each readable by `sqlite3`.
- [ ] `pytest backend/tests/ -q` exits 0.
- [ ] `uvicorn backend.api.main:app --workers 1` boots; `/healthz` returns 200.
- [ ] Single-worker invariant documented.
- [ ] No TODO comments left in Phase 1 paths (Phase D/E/F TODOs are fine).
- [ ] PR body cites RealMetaPRD line ranges for every non-obvious decision.

---

## NOTES

### Reference guides relied on (with paths for the implementing agent)

The implementing agent should keep these tabs open. **Every line range
cited above is verified to be present in the indicated file as of
2026-04-25.** Re-verify if any of these files change before you start.

- `Orchestration/PRDs/RealMetaPRD.md`
- `Orchestration/PRDs/MetaPRD.md` *(predecessor; consult for sections RealMetaPRD elides)*
- `Orchestration/PRDs/PRD1.md` *(predecessor; consult for original Phase 1 framing)*
- `Orchestration/from agents for agents/PRD1_VALIDATION_BRIEFING.md`
- `Orchestration/research/ANTHROPIC_SDK_STACK_REFERENCE.md`
- `Orchestration/research/CEREBRAS_STACK_REFERENCE.md`
- `Dev orchestration/_exports_for_b2b_accounting/01_ORCHESTRATION_REFERENCE.md`
- `Dev orchestration/_exports_for_b2b_accounting/02_YAML_WORKFLOW_DSL.md`
- `Dev orchestration/_exports_for_b2b_accounting/03_SQLITE_BACKBONE.md`
- `Dev orchestration/_exports_for_b2b_accounting/04_AGENT_PATTERNS.md`
- `Dev orchestration/_exports_for_b2b_accounting/05_swan_integration.md`
- `Dev orchestration/tech framework/REF-FASTAPI-BACKEND.md`
- `Dev orchestration/tech framework/REF-GOOGLE-ADK.md`
- `Dev orchestration/tech framework/REF-SQLITE-BACKBONE.md`
- `Dev orchestration/tech framework/REF-SSE-STREAMING-FASTAPI.md`
- `Dev orchestration/swan/CLAUDE.md` *(env-var names + money rule only for Phase 1)*

### Reference guides flagged as **available locally but not exhaustively read** for this plan

These exist in the repo and may resolve open questions but were not
mined line-by-line during plan generation. The implementing agent is
expected to skim them when relevant:

- `architecure.md` — Three-domain model + seven booking patterns;
  RealMetaPRD §6 references it but the patterns themselves matter for
  Phase D, not Phase 1.
- `projectbriefing.md` — Wedge framing (per-employee budgets +
  AI-credit cost tracking is the demo wedge — see auto-memory note).
- `pennylane_vs_us.md` — Competitive positioning; useful for the demo
  script in Phase F, not Phase 1.
- `pitch_research.md` — Market positioning; out of scope for Phase 1.
- `Dev orchestration/swan/SWAN_API_REFERENCE.md` — Full Swan integration
  surface; **only §10 enums and §1.4 environments matter for Phase 1**
  (we don't make Swan calls yet); the rest is Phase D.

### Reference guides we may want to request

The implementing agent should ASK the human user for additional reference
material if it hits any of these gaps:

1. **Sample `pyproject.toml` for an existing reference project.** The plan
   pins versions; a reference layout would catch any subtle dep
   incompatibility before install time.
2. **An end-to-end sample of an `_migrations`-tracked migration runner**
   in another HEC-Paris project. The pattern is sketched in
   REF-SQLITE-BACKBONE:598–655 but a working analog would speed
   debugging.
3. **A working `AnthropicRunner` fixture from another team's code** that
   already returns the unified `AgentResult` shape — saves us from
   accidentally re-deriving the cache_creation_input_tokens →
   cache_write_tokens mapping.

If the user has none of these, **proceed without**; the plan is
self-contained.

### Architectural decisions made by this plan (not by RealMetaPRD)

- **Phase 1 = PRD Phases A + B + C.** The PRD treats them as separate but
  the dependencies are absolute (B can't run without A; C can't write
  without A's audit.db). Bundle for clean exit gate.
- **`adk_runner.py` and `pydantic_ai_runner.py` are stubs.** Real wiring
  is post-hackathon (PRD1_VALIDATION_BRIEFING C5). Phase 1 ships the
  *interface* — `test_runner_shape.py` proves all three runners produce
  identical `AgentResult` shape, but the underlying APIs aren't called.
- **`backend/api/main.py` ships only `/healthz`** — webhook routes,
  `/documents/upload`, `/runs/...` SSE, etc. arrive in Phase D / E / F.
  The lifespan + event-bus reaper are present so Phase D can drop in its
  routes without touching application bootstrap.
- **`employees.swan_iban` left NULL on seed.** Phase D fills it when
  Swan sandbox account IBANs are known. The `UNIQUE` constraint allows
  multiple NULLs in SQLite.
- **`pipelines/noop_demo.yaml` is the canary.** It exercises one
  cacheable tool, one agent (forcing the runner path), and one
  non-cacheable tool. Phase D's first action is *removing* it.

### Known soft architectural concerns inherited from PRD1_VALIDATION_BRIEFING

- **A2 — `cache_key` canonicalization across platforms.** Phase 1 task 11
  addresses with the `repr(float)` shim. If the test fixture passes on
  CI (Linux x86_64) and the dev laptop (Linux x86_64), the residual risk
  is macOS / ARM platforms — accept for the hackathon, revisit in Phase 2.
- **A3 — single-writer model not multi-process-safe.** RealMetaPRD §9.5
  line 1419 explicitly captures this. The README must include the line:
  > Run as `uvicorn backend.api.main:app --workers 1`. Multi-worker
  > deployment requires file-lock or advisory-lock layer; out of scope
  > for the hackathon.

### Confidence score for one-pass implementation

**8.5 / 10.** The plan is dense, every task has a validate command, the
references are pinpoint-cited, and the schemas are copy-paste from
RealMetaPRD §7.5. The 1.5-point haircut accounts for:
- platform-specific float canonicalization (`A2`),
- the `executescript` / `BEGIN IMMEDIATE` interaction in the migration
  runner (Task 5 GOTCHA — the agent may need one debug iteration),
- and the Anthropic SDK's cache-token field naming
  (`cache_creation_input_tokens` vs our `cache_write_tokens`) being a
  silent mapping bug if missed in Task 16.
