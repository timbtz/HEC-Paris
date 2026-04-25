# CLAUDE.md

> **Maintenance flag — read first.**
> This file and `README.md` describe the live state of the project.
> Whenever a new piece of functionality lands (new module, new pipeline,
> new schema migration, new endpoint, new dependency), update both:
> - `README.md` — what works today + project layout.
> - `CLAUDE.md` (this file) — directory map + reference-doc pointers.
> Out-of-date scaffolding here is worse than missing scaffolding.

## Product, in one paragraph

Agnes is a YAML-driven DAG executor over three SQLite databases
(`accounting.db` / `orchestration.db` / `audit.db`). Every agent call
writes a `(decision, cost, employee)` triple, so the wedge query
"how much did Anthropic bill us this month, per employee" is a single
SQL `GROUP BY`. Phase 1 (metalayer foundation), Phase 2 (Swan
webhook → live ledger, PDF invoice → accrual, internal API surface with
SSE, demo seed dataset, Vite/React dashboard under `frontend/`), and
Phase 3 (reporting layer: six SQL-only `/reports/*` endpoints, three
agentic reporting pipelines `period_close` / `vat_return` /
`year_end_close`, period-lock enforcement on `gl_poster.post`,
`accounting_periods` + `period_reports` tables, and a "Reports"
frontend tab) are all in place.

## Repository layout

```
backend/                         # implementation (see README.md for detail)
  api/                           # FastAPI: /healthz, swan webhook, external webhook,
                                 #   documents (upload, GET row, GET /blob),
                                 #   runs (+ SSE), /pipelines catalog + DAG, /runs list,
                                 #   employees (list + detail w/ envelopes + 30d spend),
                                 #   period_reports (list, get, /artifact, /approve),
                                 #   dashboard (SSE; pipeline_* events fanned in), trace,
                                 #   list endpoints (/journal_entries, /envelopes),
                                 #   reports (/reports/* — Phase 3 SQL-only),
                                 #   CORSMiddleware (allows Vite dev origin :5173)
  ingress/                       # routing.yaml — event_type → pipeline mapping
  orchestration/
    swan/                        # OAuth + GraphQL client (Phase 2.A)
    runners/                     # anthropic_runner (Sonnet — default + vision),
                                 #   pydantic_ai_runner (Cerebras OpenAI-compat,
                                 #   gpt-oss-120b — live), adk_runner (stub),
                                 #   cerebras_impl.py (pure schema/parse helpers),
                                 #   base.py (AgentResult / TokenUsage)
    tools/                       # 16 production tools (resolver, classifier,
                                 #   builder, gl_poster, invariants, envelope,
                                 #   period_aggregator, vat_calculator,
                                 #   retained_earnings_builder, report_renderer)
    agents/                      # 4 production agents (counterparty, GL, document,
                                 #   anomaly_flag) — counterparty/GL/anomaly route
                                 #   via default_runner() (env: AGNES_LLM_PROVIDER)
    conditions/                  # gating, counterparty, gl, documents, reporting
    pipelines/                   # transaction_booked, transaction_released,
                                 #   document_ingested, external_event,
                                 #   period_close, vat_return, year_end_close,
                                 #   log_and_continue
    store/migrations/            # accounting 0001..0009 (0009 adds
                                 #   accounting_periods, period_reports,
                                 #   journal_entries.posted_at, vat_rates seed,
                                 #   CoA 120 retained earnings, demo periods),
                                 #   audit 0001..0003, orchestration 0001
    (executor, dag, registries, cache, audit, event_bus — Phase 1)
  scripts/                       # replay_swan_seed.py — Phase 3 demo replay CLI
  tests/                         # 50+ test files
frontend/                        # Vite + React 18 + TS + Tailwind v4 + Zustand 5 + Motion
  vite.config.ts                 # dev proxies every backend prefix → :8000
                                 #   (incl. /reports for Phase 3)
  src/{App.tsx, api.ts, types/, hooks/useSSE.ts, store/, components/}
                                 # 4-tab layout: Dashboard | Review | Reports | Infra
                                 # Reports tab uses ReportsTab + ReportTypeSelect +
                                 # PeriodPicker + ReportTable
data/                            # runtime DB files + PDF blobs
Orchestration/
  PRDs/RealMetaPRD.md            # the contract — every line cites a § here
  PRDs/MetaPRD.md                # predecessor PRD
  PRDs/PRD1.md                   # original Phase 1 framing
  Plans/phase1-metalayer-foundation.md         # Phase 1 executed plan
  Plans/phase-1-critical-gap-remediation.md    # wiring lessons baked into Phase 2
  Plans/phase-1-gap-audit.md                   # state at Phase 2 start
  Plans/phase-2-swan-document-frontend.md      # Phase 2 plan (executed)
  Plans/phase-2-list-endpoints-and-frontend.md # Phase 2.F frontend + list endpoints (executed)
  Plans/phase-3-reporting-pipelines.md         # Phase 3 reporting plan (executed)
  lovable/backend-gap-plan.md                  # Phase 4 backend gaps. Week 1
                                               # tranche (§§1, 2, 3, 10, 11) executed:
                                               # /runs list, /pipelines catalog,
                                               # /period_reports surface,
                                               # /employees, /documents/{id}{,/blob},
                                               # dashboard pipeline_* fan-out
  pitch/capabilities.md                        # customer-facing capabilities promise
  research/                      # ANTHROPIC_SDK_STACK_REFERENCE, CEREBRAS_*
  from agents for agents/PRD1_VALIDATION_BRIEFING.md
Dev orchestration/
  _exports_for_b2b_accounting/   # 01..05 — orchestrator, DSL, sqlite, agents, swan
  tech framework/                # REF-FASTAPI, REF-SQLITE, REF-SSE, REF-ADK, briefing
  swan/                          # Swan API reference (Phase 2.A onwards)
pyproject.toml                   # deps incl. openai>=1.30 (Cerebras runner);
                                 #   optional [adk] / [pydantic_ai] / [dev] extras
pytest.ini                       # asyncio_mode = auto, timeout = 15
.env.example                     # canonical env-var names (incl. SWAN_*, STRIPE_*,
                                 #   CEREBRAS_API_KEY, AGNES_LLM_PROVIDER)
```

## Hard rules carried over from RealMetaPRD

- Money is **integer cents**. No floats on a money path. Enforced by a
  CI grep audit (see plan §VALIDATION).
- DB writes go through `store.writes.write_tx` (BEGIN IMMEDIATE +
  per-DB lock). Never call `conn.commit()` directly.
- Run `uvicorn` with `--workers 1`. The per-DB `asyncio.Lock` does not
  coordinate across processes (RealMetaPRD §9.5).
- Pipelines are data, not code. New event types ship as YAML + a tool +
  a routing.yaml line — never executor surgery.
- `gl_poster.post` is the **single chokepoint** for `journal_entries`
  writes outside migrations. CI grep enforces it. Phase 3 added a
  period-lock guard inside `gl_poster.post`: posting an `entry_date`
  that falls inside a `closed` `accounting_periods` row raises
  `RuntimeError`. Backdate-protection is therefore on by default.
- SSE generators must check `await request.is_disconnected()` and use
  short polling intervals (≤ 1s). httpx `ASGITransport` buffers the
  whole response — never test SSE through `AsyncClient.stream`; invoke
  the route function directly (see `backend/tests/test_dashboard_sse.py`).
- Setting `AGNES_SWAN_LOCAL_REPLAY=1` makes
  `tools.swan_query.fetch_transaction` skip the Swan API and read from
  the locally-persisted seed. Used by `backend/scripts/replay_swan_seed.py`
  for the demo; never set in production.
- `AGNES_LLM_PROVIDER=anthropic|cerebras` (default `anthropic`) selects
  the classifier runtime. `cerebras` requires `CEREBRAS_API_KEY` and
  routes `anomaly_flag_agent`, `gl_account_classifier_agent`, and
  `counterparty_classifier` through `PydanticAiRunner` (raw
  `AsyncOpenAI` against `https://api.cerebras.ai/v1`, model
  `gpt-oss-120b`). `document_extractor` always uses Anthropic — Cerebras
  has no multimodal model. Keep a Cerebras Developer-tier key for
  demos; the free tier silently caps context to 8k. The runner registry
  keeps the `pydantic_ai` key for compat with the executor's provider
  mapping (`pydantic_ai → cerebras`); the runner is raw OpenAI-compat
  underneath.

## How to run tests (read before invoking pytest)

- The suite has SSE / asyncio.wait_for tests that **will hang forever**
  without a timeout. `pytest.ini` enforces a 15s per-test ceiling via
  `pytest-timeout` (`timeout_method = thread`). Do **not** strip this.
- Always run pytest in **background** for full-suite invocations
  (Bash `run_in_background: true`) and poll the output, instead of
  blocking the foreground tool call. Foreground is fine only for a
  single `-k <pattern>` selection that you already know terminates fast.
- If you need a longer budget for one test, mark it locally with
  `@pytest.mark.timeout(N)` — never widen the global default.
- Install the dev extras first: `uv sync --extra dev` (this pulls
  `pytest-timeout`; without it `--timeout=...` errors out).

## When to update this file

- New Phase lands → refresh "in one paragraph" + add the new top-level
  module to the layout.
- New reference doc dropped under `Dev orchestration/` or
  `Orchestration/research/` → list it.
- New env var or runtime invariant → add a bullet under "Hard rules".
