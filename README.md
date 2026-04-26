# Agnes

YAML-driven DAG executor over a three-database SQLite backbone, with an
audit/cost spine that records every agent decision against an employee.

**Status:** Phase 1 (metalayer foundation), Phase 2 (Swan webhook →
live ledger, PDF invoice → accrual, internal API surface with SSE, demo
seed dataset, Vite/React dashboard) and Phase 3 (reporting layer:
SQL-only `/reports/*` endpoints, three agentic reporting pipelines —
`period_close`, `vat_return`, `year_end_close` — and the frontend
"Reports" tab) are complete. The frontend lives under `frontend/` and is
wired to the backend via a Vite dev proxy.

## What works today

- **Three SQLite databases** open at startup with the canonical PRAGMA
  block (WAL, foreign_keys, busy_timeout, etc.) and a per-DB
  `asyncio.Lock` enforcing single-writer discipline.
  - `accounting.db` — GL, counterparties, documents, budgets, review
    queue, demo seed (~200 swan transactions, 10+ counterparties, 60
    monthly envelopes).
  - `orchestration.db` — pipeline runs, append-only events,
    external-event idempotency, cross-run node cache.
  - `audit.db` — employees (Tim / Marie / Paul with `swan_iban` +
    `swan_account_id` set), agent decisions, agent costs.
- **Migration runner.** Per-DB `_migrations` table; bootstrap-from-SQL
  and migration-replay produce identical schemas. Ships
  `accounting/0001..0009` (init, chart of accounts, account rules,
  envelope_category, demo counterparties, demo swan transactions,
  envelopes, review_queue, periods + VAT seed + retained earnings CoA),
  `audit/0001..0003`, `orchestration/0001`.
- **YAML pipeline DSL + DAG executor.** Kahn topological-layer build,
  fail-fast cancellation, strict-key validation, filename-must-match-
  name rule, named conditions, cross-run cache writes after success.
- **Four registries**, all production tools / agents / conditions
  registered at boot via `_register_production()`.
- **Anthropic runner.** `AsyncAnthropic(timeout=4.5, max_retries=2)`
  singleton, `submit_*` tool forcing, `APITimeoutError` →
  `AgentResult(finish_reason='timeout', confidence=None)` deterministic
  fallback (no retry on timeout, per `RealMetaPRD §7.9`); per-call
  `deadline_s=15.0` override for the document extractor.
- **Cerebras runner** (`PydanticAiRunner`, raw `AsyncOpenAI` against
  `https://api.cerebras.ai/v1`, `timeout=4.0`). Live for the three
  classifier agents (anomaly, GL, counterparty) when
  `AGNES_LLM_PROVIDER=cerebras` (default `anthropic`). Translates the
  agents' Anthropic-shape tool dicts to OpenAI shape with
  `additionalProperties:false` recursive injection (`strict=true`,
  `parallel_tool_calls=false`). `claude-sonnet-4-6` remains the default
  until Phase-2 SLA validation closes; `document_extractor` always uses
  Anthropic (vision-only).
- **Audit spine.** `propose_checkpoint_commit` writes
  `agent_decisions` + `agent_costs` atomically via `write_tx`. Cost via
  integer-micro-USD `COST_TABLE_MICRO_USD` per (provider, model).
- **In-process event bus** with a long-lived dashboard channel
  (`subscribe_dashboard()`, `publish_event_dashboard()`) exempt from
  the TTL reaper.
- **Swan plumbing.** `SwanOAuthClient` (client_credentials, in-process
  cache, refresh-on-401-and-retry) and `SwanGraphQLClient`
  (httpx-based, `fetch_transaction`, `fetch_account`,
  `handle_mutation_result` for the union-error pattern).
- **Production tools (16).** `swan_query`, `counterparty_resolver`
  (4-stage cascade with cache writeback), `gl_account_classifier`
  (rule lookup), `journal_entry_builder` (cash/accrual/match/reversal/
  find_original/mark_reversed), `gl_poster` (single chokepoint for
  `journal_entries` writes; Phase 3 added period-lock enforcement and
  `posted_at` stamping), `invariant_checker` (the five
  `RealMetaPRD §7.6` asserts), `budget_envelope.decrement`,
  `confidence_gate` (multiplicative with None=0.5),
  `review_queue.enqueue`, `document_extractor.validate_totals`,
  `external_payload_parser`, plus the four Phase 3 reporting tools:
  `period_aggregator.{compute_trial_balance,compute_open_entries,summarize_period}`,
  `vat_calculator.compute_vat_return`,
  `retained_earnings_builder.build_closing_entry`,
  `report_renderer.render`.
- **Production agents (5).** `counterparty_classifier`,
  `gl_account_classifier_agent` (chart-of-accounts enum at request
  time), `document_extractor` (Claude vision + `submit_invoice` strict
  schema, `timeout=15.0`), `anomaly_flag_agent` (period-close /
  VAT-return anomaly detection with closed-enum `kind`), and
  `wiki_post_mortem_agent` (drafts a post-mortem markdown page from
  every reporting-pipeline run; the writer auto-files observations or
  routes proposed `policies/*` changes through `review_queue`).
- **Self-improving Living Rule Wiki (Phase 4.A).** Markdown corpus in
  `orchestration.db` (`wiki_pages` + `wiki_revisions` + FTS5 contentless
  virtual table). Every reasoning agent — `gl_account_classifier`,
  `counterparty_classifier`, `anomaly_flag`, `document_extractor` —
  calls `wiki_reader` with role-tags before constructing its prompt,
  and threads `wiki_context=[(page_id, revision_id), …]` into
  `runner.run`. The runner now hashes the citation list into
  `prompt_hash` and stamps `result.wiki_references` by construction, so
  a wiki edit invalidates exactly the agent calls that read that page.
  The reporting pipelines (`period_close`, `vat_return`,
  `year_end_close`) ship with terminal `draft-post-mortem` +
  `write-post-mortem` nodes — each run files an observation under
  `post_mortems/{period}/{pipeline}_{run_id}.md`, and subsequent runs
  read prior post-mortems as part of their wiki context. `tools.
  wiki_search:run` exposes BM25 over the FTS5 index when the right tag
  isn't known up front. `wiki/maintenance.py` keeps `log.md` and
  `index.md` updated automatically. The CFO can edit policies via
  `POST /wiki/pages` / `PUT /wiki/pages/{id}` (auth-shim `x-agnes-author`
  header).
- **Production conditions.** `gating.{passes_confidence,needs_review,
  posted}`, `counterparty.unresolved`, `gl.unclassified`,
  `documents.{totals_ok,totals_mismatch}`, plus
  `reporting.{period_open,period_closeable,has_anomalies,passes_report_confidence}`.
- **Pipelines (7 production + 2 stubs).** `transaction_booked`
  (12 nodes), `transaction_released` (8-node compensation, idempotent
  on double-release), `document_ingested` (13 nodes), `external_event`
  (stub for the routing claim), `period_close` (8 nodes, read-only
  reporting), `vat_return` (8 nodes), `year_end_close` (10 nodes; only
  pipeline that posts new journal entries during a close), plus
  `noop_demo` and `log_and_continue`.
- **API surface.** `/healthz`, `POST /swan/webhook` (constant-time
  `x-swan-secret`, idempotent on `(provider, event_id)`,
  background-dispatched), `POST /external/webhook/{provider}` (Stripe
  HMAC verifier in `_VERIFIER_REGISTRY`), `POST /documents/upload`
  (multipart, SHA256 idempotency, `data/blobs/`),
  `GET /documents/{id}` (row + line items),
  `GET /documents/{id}/blob` (PDF/PNG/JPEG inline; allow-listed MIME +
  blob root constraint),
  `POST /pipelines/run/{name}`, `GET /pipelines` (catalog),
  `GET /pipelines/{name}` (DAG topology), `GET /runs` (paginated list
  with cost + review aggregates), `GET /runs/{id}`,
  `GET /runs/{id}/stream` (per-run SSE),
  `GET /journal_entries` (paginated ledger list),
  `GET /journal_entries/{id}/trace` (cross-DB merged view),
  `GET /envelopes` (envelope state with rolled-up `used_cents`),
  `GET /employees`, `GET /employees/{id}` (envelope summary + 30-day
  spend), `GET /period_reports`, `GET /period_reports/{id}`,
  `GET /period_reports/{id}/artifact?format=md` (PDF/CSV → 415 until
  the renderer learns those formats), `POST /period_reports/{id}/approve`,
  `POST /review/{id}/approve`, `GET /dashboard/stream`
  (long-lived SSE; pipeline lifecycle events fan out here so the Today
  card updates without polling), and the Phase 3 SQL-only reports
  surface: `GET /reports/{trial_balance,balance_sheet,income_statement,
  cashflow,budget_vs_actuals,vat_return}`, plus the Phase 4.A wiki
  surface: `GET /wiki/pages`, `GET /wiki/pages/{id}{,/revisions{,/{rev_id}}}`,
  `POST /wiki/pages`, `PUT /wiki/pages/{id}`. `CORSMiddleware` allows
  the Vite dev origin (`http://localhost:5173`).
- **Replay script.** `python -m backend.scripts.replay_swan_seed`
  iterates the seeded `swan_transactions` and POSTs synthetic webhooks
  through `/swan/webhook`, end-to-end populating the ledger for the
  demo. Idempotent (the second run dedups via
  `external_events.UNIQUE`). Set `AGNES_SWAN_LOCAL_REPLAY=1` to make
  `swan_query.fetch_transaction` skip the Swan API and read from the
  local seed.
- **Routing.** `backend/ingress/routing.yaml` maps event types to
  pipelines; unknown events fall back to `defaults.unknown_event`.
- **Test suite (50+ files).** Unit + integration coverage of PRAGMAs,
  bootstrap-replay, YAML strict-key, DAG cycles, executor event
  contract, fail-fast, cache hit, registry resolution, audit atomicity,
  cost math, prompt hash invariants, runner-shape parity, Swan OAuth /
  GraphQL, webhook signature verify + idempotency, document upload,
  every tool / agent / condition, every pipeline end-to-end, runs API,
  trace drilldown, dashboard SSE generator, and the per-employee wedge
  SQL. **All pytest invocations must use `--workers 1` semantics and
  the 15s `timeout` ceiling configured in `pytest.ini`** — see
  `CLAUDE.md` for the full operating procedure.

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

frontend/                      # Vite + React 18 + TS + Tailwind v4 + Zustand 5 + Motion
  vite.config.ts               # proxies /healthz, /swan, /documents, /pipelines,
                               #   /runs, /journal_entries, /envelopes, /review,
                               #   /dashboard → :8000
  src/
    App.tsx                    # 4-tab layout (Dashboard | Review | Reports | Infra) + global SSE
    api.ts                     # typed fetch wrappers
    types/index.ts             # response + DashboardEvent + RunEvent shapes
    types/reports.ts           # Phase 3 report response shapes
    hooks/useSSE.ts            # generic EventSource hook (StrictMode-safe)
    store/{dashboard,runProgress}.ts   # Zustand stores
    components/                # Ledger, EnvelopeRing(s), UploadZone,
                               #   RunProgressOverlay, TraceDrawer,
                               #   ReviewQueue, ReportsTab,
                               #   ReportTypeSelect, PeriodPicker,
                               #   ReportTable, InfraTab, Tabs, Skeleton

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

## Run the frontend

There are now **two** frontends in the tree. `frontend-lovable/` is the
**primary** one (cloned from
[agnes-finance-hub](https://github.com/timbtz/agnes-finance-hub) — Vite +
React 18 + shadcn/ui + Radix + TanStack Query). `frontend/` is kept as
secondary for reference.

```bash
# Backend (terminal 1):
AGNES_DATA_DIR=./data uvicorn backend.api.main:app --workers 1 --port 8000

# Primary (Lovable) frontend dev server (terminal 2):
cd frontend-lovable
bun install        # first time only (npm install also works)
bun run dev        # → http://localhost:5174

# Secondary frontend (terminal 2 alt):
cd frontend
npm install        # first time only
npm run dev        # → http://localhost:5173
```

Both Vite dev servers proxy every backend prefix to `:8000`, so the
browser sees a single origin (no CORS). The Lovable frontend's
`src/lib/api.ts` reads `VITE_API_BASE_URL` from `.env.local`; it's set
to the dev server's own origin so requests come back through the proxy.
For a production-style smoke test: `bun run build` (or `npm run build`)
emits a static bundle into `frontend-lovable/dist/`.
