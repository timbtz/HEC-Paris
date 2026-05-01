# Fingent

YAML-driven DAG executor over a three-database SQLite backbone, with an
audit/cost spine that records every agent decision — and every cent of
provider spend — against an individual employee. The wedge: when an
Anthropic invoice lands, you can answer **"who ordered which workflow"**
in a single SQL `GROUP BY`.

**Status.** All four phases live:
- **Phase 1** — metalayer foundation (three SQLite DBs with single-writer
  locks, YAML pipeline DSL + DAG executor, registries, prompt-hash, audit
  spine, in-process event bus).
- **Phase 2** — Swan webhook → live ledger, PDF invoice → accrual, demo
  seed dataset, Vite/React dashboard.
- **Phase 3** — reporting layer: six SQL-only `/reports/*` endpoints,
  three agentic reporting pipelines (`period_close`, `vat_return`,
  `year_end_close`), period-lock enforcement on `gl_poster.post`,
  frontend "Reports" tab.
- **Phase 4.A** — self-improving Living Rule Wiki (Karpathy-style
  markdown corpus the agents both read and write back to). Every
  reasoning agent loads role-tagged pages before constructing its
  prompt, threads the `(page_id, revision_id)` tuples into the
  prompt-hash and the `agent_decisions` row, and every closing pipeline
  files a post-mortem back into the wiki. CFO can edit policies through
  `POST /wiki/pages` / `PUT /wiki/pages/{id}`.
- **Phase 4.B** — gamification layer: per-call coin auto-credit on every
  `agent_decisions` insert (one `write_tx`, idempotent on
  `agent_decision_id`), leaderboard, rewards, redemptions, manager
  approval queue, "Adoption" frontend tab.

**MCP edition.** `backend/mcp/` is a FastMCP server that wraps the
FastAPI routers in-process via httpx ASGI — no business-logic
duplication. Exposes pipeline run/inspect, GL queries, approvals, and
wiki reads to MCP clients. Run with `python -m backend.mcp` (stdio) or
`python -m backend.mcp --http`. Optional dep: `uv sync --extra mcp`.

**Frontends.** Two live in the tree. `frontend-lovable/` is **primary**
(Vite + React 18 + shadcn/ui + Radix + TanStack Query + Zustand,
includes the Adoption tab and the AdoptionStrip on the AI-Spend page).
`frontend/` is kept secondary for reference (Tailwind v4 stack). Both
proxy every backend prefix to the FastAPI dev server.

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
  `audit/0001..0005` (init, seed employees, swan_iban links,
  `wiki_page_id` / `wiki_revision_id` columns on `agent_decisions`,
  gamification — adds `employees.is_manager` plus the five tables
  `gamification_tasks` / `task_completions` / `rewards` /
  `reward_redemptions` / `coin_adjustments`, seeds Tim as manager + 9
  demo tasks + 4 demo rewards), and `orchestration/0001..0004`
  (init, `wiki_pages`, `wiki_revisions`, FTS5 contentless virtual
  table for BM25 search).
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
  `FINGENT_LLM_PROVIDER=cerebras` (default `anthropic`). Translates the
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
- **Production tools (18).** `swan_query`, `counterparty_resolver`
  (4-stage cascade with cache writeback), `gl_account_classifier`
  (rule lookup), `journal_entry_builder` (cash/accrual/match/reversal/
  find_original/mark_reversed), `gl_poster` (single chokepoint for
  `journal_entries` writes; Phase 3 added period-lock enforcement and
  `posted_at` stamping), `invariant_checker` (the five
  `RealMetaPRD §7.6` asserts), `budget_envelope.decrement`,
  `confidence_gate` (multiplicative with None=0.5),
  `review_queue.enqueue`, `document_extractor.validate_totals`,
  `external_payload_parser`, the four Phase 3 reporting tools
  (`period_aggregator.{compute_trial_balance,compute_open_entries,summarize_period}`,
  `vat_calculator.compute_vat_return`,
  `retained_earnings_builder.build_closing_entry`,
  `report_renderer.render`), and the three Phase 4.A wiki tools
  (`wiki_reader` for role-tag lookup, `wiki_search` for BM25 over
  the FTS5 index, `wiki_writer` for the post-mortem
  commit/queue chokepoint).
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
  `POST /wiki/pages` / `PUT /wiki/pages/{id}` (auth-shim `x-fingent-author`
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
- **Phase 4.B gamification layer.** `backend/orchestration/gamification.py`
  is the engine. The `auto_credit_for_decision` hook is called from
  inside `audit.write_decision`'s `write_tx`, so every
  `agent_decisions` insert atomically credits 5 coins to the responsible
  employee in the same commit, idempotent on `agent_decision_id`. Coin
  balance is computed on read (approved completions + adjustments −
  pending/approved redemptions). Manager-only writes are gated by
  `employees.is_manager` resolved from the `x-fingent-author` header.
  Frontend exposes a full Adoption tab (Today / Leaderboard / Task
  library / Rewards / Manager queue) plus a live `AdoptionStrip` on
  the AI-Spend page that polls `/gamification/leaderboard`.
- **MCP server.** `backend/mcp/` (FastMCP) exposes the agentic surface
  to MCP clients — pipeline run/inspect, GL queries, approvals, wiki
  reads. Implemented as httpx ASGI wrappers around the existing
  FastAPI routers in-process; **no business-logic duplication**, so
  every MCP tool inherits the same auth, validation, and audit-spine
  guarantees as the REST surface. `python -m backend.mcp` (stdio) or
  `python -m backend.mcp --http`. Optional dep: `uv sync --extra mcp`.
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
  spend), `GET /accounting_periods`, `GET /period_reports`,
  `GET /period_reports/{id}`,
  `GET /period_reports/{id}/artifact?format=md` (PDF/CSV → 415 until
  the renderer learns those formats), `POST /period_reports/{id}/approve`,
  `POST /review/{id}/approve`, `GET /dashboard/stream`
  (long-lived SSE; pipeline lifecycle events fan out here so the Today
  card updates without polling), the Phase 3 SQL-only reports
  surface: `GET /reports/{trial_balance,balance_sheet,income_statement,
  cashflow,budget_vs_actuals,vat_return,ai-costs}` (the last drives
  the AI-Spend page; whitelisted `group_by` keys, parameter-bound SQL),
  the Phase 4.A wiki surface: `GET /wiki/pages`,
  `GET /wiki/pages/{id}{,/revisions{,/{rev_id}}}`, `POST /wiki/pages`,
  `PUT /wiki/pages/{id}` (write surface gated by `x-fingent-author`),
  and the Phase 4.B gamification surface: `GET/POST /gamification/{tasks,
  completions, rewards, redemptions, coin_adjustments}`,
  `GET /gamification/leaderboard`, `GET /gamification/today/{id}`,
  `GET /gamification/balance/{id}`. `CORSMiddleware` allows the Vite
  dev origins.
- **Replay script.** `python -m backend.scripts.replay_swan_seed`
  iterates the seeded `swan_transactions` and POSTs synthetic webhooks
  through `/swan/webhook`, end-to-end populating the ledger for the
  demo. Idempotent (the second run dedups via
  `external_events.UNIQUE`). Set `FINGENT_SWAN_LOCAL_REPLAY=1` to make
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
  the 15s `timeout` ceiling configured in `pytest.ini`.**

## Project structure

```
backend/
  api/                         # FastAPI app + 14 routers wired in main.py
    main.py                    # lifespan, CORSMiddleware, include_router(*)
    swan_webhook.py            # POST /swan/webhook
    external_webhook.py        # POST /external/webhook/{provider}
    documents.py               # /documents (upload, GET row, GET /blob)
    runs.py                    # /pipelines (catalog/DAG), /runs (list, get, /stream)
    employees.py               # /employees + /employees/{id}
    accounting_periods.py      # /accounting_periods
    period_reports.py          # /period_reports + /artifact + /approve
    reports.py                 # Phase 3 SQL-only /reports/* (incl. /reports/ai-costs)
    audit_traces.py            # /journal_entries + /journal_entries/{id}/trace
    wiki.py                    # Phase 4.A: GET/POST/PUT /wiki/pages, revisions
    gamification.py            # Phase 4.B: tasks, completions, rewards, leaderboard
    dashboard.py               # GET /dashboard/stream (SSE; pipeline_* fan-out)
    demo_webhook.py            # /review/{id}/approve + demo helpers
  ingress/
    routing.yaml               # event_type → pipeline mapping
  mcp/                         # FastMCP server (Phase 4-bonus)
    __main__.py                # python -m backend.mcp [--http]
    server.py                  # tools wrap FastAPI routers via httpx ASGI
  orchestration/
    context.py / yaml_loader.py / dag.py / executor.py
    registries.py / cache.py / cost.py / prompt_hash.py
    audit.py                   # propose → checkpoint → commit (calls auto-credit)
    event_bus.py               # in-process pub/sub + dashboard channel
    gamification.py            # Phase 4.B engine: auto_credit_for_decision,
                               #   coin_balance, leaderboard, today_summary,
                               #   is_manager (called from inside write_tx)
    runners/
      base.py                  # AgentResult, TokenUsage, AgentRunner Protocol
      anthropic_runner.py      # AsyncAnthropic singleton, vision support
      pydantic_ai_runner.py    # raw AsyncOpenAI against api.cerebras.ai
      cerebras_impl.py         # schema/parse helpers (pure)
      adk_runner.py            # stub
    swan/                      # OAuth + GraphQL client (Phase 2.A)
    tools/                     # 18 production tools — see "What works today"
    agents/                    # counterparty_classifier, gl_account_classifier_agent,
                               #   document_extractor, anomaly_flag_agent,
                               #   wiki_post_mortem_agent (+ noop_agent)
    wiki/                      # Phase 4.A
      schema.py                # WikiFrontmatter + parse
      loader.py                # load_pages_for_tags, resolve_references
      writer.py                # upsert_page (single chokepoint)
      maintenance.py           # auto-maintained log.md + index.md
    conditions/                # gating, counterparty, gl, documents, reporting
    pipelines/                 # transaction_booked, transaction_released,
                               #   document_ingested, external_event,
                               #   period_close, vat_return, year_end_close
                               #   (each closing pipeline ends with read-wiki +
                               #   draft-post-mortem + write-post-mortem nodes),
                               #   noop_demo, log_and_continue
    store/
      bootstrap.py             # open_dbs() → StoreHandles (3 conns + 3 locks)
      writes.py                # write_tx async ctx mgr (BEGIN IMMEDIATE)
      schema/{accounting,orchestration,audit}.sql
      migrations/
        accounting/0001..0009  # init … periods + VAT seed + retained earnings CoA
        audit/0001..0005       # init … wiki citations … gamification tables
        orchestration/0001..0004  # init … wiki_pages … wiki_revisions … FTS5
  scripts/
    replay_swan_seed.py        # Phase 3 demo replay CLI
    enrich_demo_seed.py        # one-shot agent-attribution enrichment
    seed_adoption_demo.py      # Phase 4.B demo data
    seed_wiki.py / seed_demo_post_mortem.py
    backfill_employee_attribution.py / reset_demo_state.py
  tests/                       # 50+ test files

data/
  blobs/                       # PDF storage
  {accounting,orchestration,audit}.db   # created on first boot

frontend-lovable/              # PRIMARY — Vite + React 18 + TS + shadcn/ui +
                               #   Radix + TanStack Query + Zustand 5 + react-router
  vite.config.ts               # dev :5174, proxies every backend prefix
  src/
    lib/api.ts                 # uses VITE_API_BASE_URL (.env.local)
    pages/                     # Dashboard, AI Spend, Reports, Adoption, Wiki, …
    components/adoption/       # AdoptionStrip on AI-Spend, Adoption tab tiles

frontend/                      # SECONDARY (kept for reference) — Tailwind v4 stack
  vite.config.ts               # dev :5173, proxies every backend prefix → :8000
  src/                         # 4-tab layout: Dashboard | Review | Reports | Infra

wiki/                          # Seed markdown corpus loaded into orchestration.db
                               #   on first boot (policies, post-mortem templates).

pyproject.toml                 # python>=3.12; aiosqlite, anthropic, fastapi,
                               #   openai>=1.30 (Cerebras), optional [adk] /
                               #   [pydantic_ai] / [dev] / [mcp] extras
pytest.ini                     # asyncio_mode = auto, timeout = 15 (thread)
.env.example                   # canonical env-var names (SWAN_*, STRIPE_*,
                               #   CEREBRAS_API_KEY, FINGENT_LLM_PROVIDER, …)
```

## Run it

```bash
# Install dev extras (pulls pytest + pytest-timeout, required by pytest.ini):
uv sync --extra dev

# Tests — single-writer + 15s per-test ceiling are non-negotiable:
.venv/bin/pytest backend/tests/ -q

# Boot the API (single worker is mandatory).
# In this dev environment :8000 is held by another local service, so we
# default to :8001:
FINGENT_DATA_DIR=./data .venv/bin/uvicorn backend.api.main:app \
  --workers 1 --host 127.0.0.1 --port 8001
curl http://127.0.0.1:8001/healthz   # → {"status":"ok"}
```

## Run the frontend

`frontend-lovable/` is **primary** (Vite + React 18 + shadcn/ui + Radix
+ TanStack Query, cloned from
[agnes-finance-hub](https://github.com/timbtz/agnes-finance-hub),
includes the Phase 4.B Adoption tab and AdoptionStrip).
`frontend/` is kept secondary for reference (Tailwind v4 stack).

```bash
# Backend (terminal 1):
FINGENT_DATA_DIR=./data .venv/bin/uvicorn backend.api.main:app \
  --workers 1 --host 127.0.0.1 --port 8001

# Primary (Lovable) frontend (terminal 2) — defaults proxy to :8001:
cd frontend-lovable
bun install        # first time only (npm install also works)
bun run dev        # → http://localhost:5174

# Secondary frontend (terminal 2 alt) — defaults proxy to :8000:
cd frontend
npm install        # first time only
npm run dev        # → http://localhost:5173
```

If you start uvicorn on a non-default port, override the proxy target on
the Vite side: `FINGENT_BACKEND_URL=http://127.0.0.1:<port> bun run dev`.
Both dev servers proxy every backend prefix, so the browser sees a
single origin (no CORS). The Lovable frontend's `src/lib/api.ts` reads
`VITE_API_BASE_URL` from `.env.local`. For a production-style smoke
test: `bun run build` emits a static bundle into `frontend-lovable/dist/`.

## Demo seed flow

For a believable end-to-end demo (live ledger + agent attribution +
envelope rings + AI-Spend page + Adoption tab):

```bash
# Replay seeded swan transactions through /swan/webhook (idempotent):
FINGENT_SWAN_LOCAL_REPLAY=1 python -m backend.scripts.replay_swan_seed

# One-shot enrichment — links 40 agent_decisions to journal entries,
# flips 6 entries to review, populates budget_allocations so envelopes
# burn 12–94%, propagates employee_id onto agent_costs (idempotent):
python -m backend.scripts.enrich_demo_seed

# Phase 4.B adoption demo data (tasks, completions, redemptions):
python -m backend.scripts.seed_adoption_demo
```

## MCP

```bash
uv sync --extra mcp                # optional dep
python -m backend.mcp              # stdio transport
python -m backend.mcp --http       # HTTP transport
```

Tools wrap the FastAPI routers in-process via httpx ASGI — see
`backend/mcp/server.py`. They never duplicate router business logic; if
a route changes, the MCP tool inherits the change automatically.
