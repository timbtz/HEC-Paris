# Agnes

YAML-driven DAG executor over a three-database SQLite backbone, with an
audit/cost spine that records every agent decision against an employee.

**Status:** Phase 1 (metalayer foundation) complete. Phase D (Swan path),
Phase E (document path), and Phase F (frontend) are not yet implemented.

## What works today

- **Three SQLite databases** open at startup with the canonical PRAGMA
  block (WAL, foreign_keys, busy_timeout, etc.) and a per-DB
  `asyncio.Lock` enforcing single-writer discipline.
  - `accounting.db` — GL, counterparties, documents, budgets (16 tables).
  - `orchestration.db` — pipeline runs, append-only events, external-event
    idempotency, cross-run node cache (5 tables).
  - `audit.db` — employees, agent decisions, agent costs (4 tables).
- **Migration runner.** Per-DB `_migrations` table; bootstrap-from-SQL
  and migration-replay produce identical schemas. Ships `0001_init` for
  each DB and `0002_seed_employees` (Tim / Marie / Paul).
- **YAML pipeline DSL.** `yaml.safe_load` → strict-key validation →
  `Pipeline` / `PipelineNode` dataclasses. Mutual exclusion between
  `tool:` and `agent:`, depends-on closure check, filename-must-match-
  name rule.
- **DAG executor.** Kahn topological-layer build at parse time; layers run
  under `asyncio.gather(..., return_exceptions=True)` with fail-fast
  cancellation. Cycle detection raises `PipelineLoadError` naming the
  offending nodes.
- **Four registries** — tools / agents / runners / conditions — backed by
  flat dicts of `module.path:symbol` strings, lazily imported and
  `lru_cache`-resolved.
- **Cross-run node cache.** Content-addressed by
  `sha256(node_id|code_version|canonical(input))`; floats canonicalized
  via `repr(float)` to defeat platform drift; NaN/Inf rejected.
- **Three runners, one shape.** `AnthropicRunner` is real
  (`AsyncAnthropic(timeout=4.5, max_retries=2)` singleton, submit-tool
  extraction, Anthropic-cache-token mapping). `AdkRunner` and
  `PydanticAiRunner` are stubs that produce identical `AgentResult`
  shape under fake `_run_impl`.
- **Audit spine.** `propose_checkpoint_commit` writes one
  `agent_decisions` + one `agent_costs` row in a single
  `BEGIN IMMEDIATE` transaction. Cost via integer-micro-USD
  `COST_TABLE_MICRO_USD` per (provider, model).
- **In-process event bus.** Multi-fanout per `run_id`, drop-on-full,
  TTL reaper. Phase F SSE endpoint will subscribe; the bus exists now
  so the executor can publish on every node transition.
- **FastAPI lifespan.** `/healthz` is the only endpoint shipped; webhook
  routes, document upload, runs API, and SSE arrive in later phases.
- **70 unit + integration tests** covering PRAGMAs, bootstrap-replay,
  YAML strict-key, DAG cycles, executor 8-event invariant, fail-fast,
  cache hit, registry resolution, audit atomicity, cost math, prompt
  hash invariants, runner-shape parity, and the per-employee wedge SQL.
  Suite runs in ~1.5s.

## Project structure

```
backend/
  api/main.py                  # FastAPI app: lifespan + /healthz only
  orchestration/
    context.py                 # AgnesContext dataclass
    yaml_loader.py             # safe_load → Pipeline dataclass
    dag.py                     # Kahn topological-layer build
    executor.py                # async layer-by-layer runner
    registries.py              # tool / agent / runner / condition lookup
    cache.py                   # cross-run node cache (canonical + sha256)
    cost.py                    # COST_TABLE_MICRO_USD + micro_usd()
    prompt_hash.py             # sha256[:16] over (model, system, tools, last user)
    audit.py                   # propose → checkpoint → commit
    event_bus.py               # in-process pub/sub + TTL reaper
    runners/
      base.py                  # AgentResult, TokenUsage, AgentRunner Protocol
      anthropic_runner.py      # real Anthropic runtime
      adk_runner.py            # stub
      pydantic_ai_runner.py    # stub
    store/
      bootstrap.py             # open_dbs() → StoreHandles (3 conns + 3 locks)
      writes.py                # write_tx async ctx mgr (BEGIN IMMEDIATE)
      schema/{accounting,orchestration,audit}.sql
      migrations/
        __init__.py            # MigrationRunner + split_sql_statements
        accounting/0001_init.py
        orchestration/0001_init.py
        audit/0001_init.py
        audit/0002_seed_employees.py
    pipelines/noop_demo.yaml   # smoke-test pipeline (3 nodes)
    tools/noop.py              # smoke-test tool
    agents/noop_agent.py       # smoke-test agent
    conditions/gating.py       # passes_confidence, needs_review, posted (stubs)
  tests/
    conftest.py                # tmp_path stores + fake_anthropic fixture
    test_*.py                  # 12 files, 70 cases

data/
  blobs/                       # PDF storage (used Phase E onwards)
  {accounting,orchestration,audit}.db   # created on first boot

pyproject.toml                 # python>=3.12; aiosqlite, anthropic, fastapi, …
pytest.ini                     # asyncio_mode = auto
.env.example                   # canonical env-var names
```

## Run it

```bash
# Install (system Python 3.12 already has aiosqlite/yaml/pydantic/anthropic/
# fastapi/uvicorn; only pytest needs adding):
pip install --break-system-packages pytest pytest-asyncio

# Tests:
python3 -m pytest backend/tests/ -q

# Boot the API (single worker is mandatory — see CLAUDE.md):
AGNES_DATA_DIR=./data uvicorn backend.api.main:app --workers 1 --port 8000
curl http://127.0.0.1:8000/healthz   # → {"status":"ok"}
```

## Not yet implemented

Phase D (Swan webhook → GL), Phase E (PDF invoice → accrual), Phase F
(live dashboard + SSE), and everything that depends on those: webhook
routes, document upload, runs API, journal-entry tools, GL poster, the
review queue UI. See `Orchestration/PRDs/RealMetaPRD.md` §12 for the
sequenced phase plan.
