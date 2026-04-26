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
SSE, demo seed dataset, Vite/React dashboard under `frontend/`),
Phase 3 (reporting layer: six SQL-only `/reports/*` endpoints, three
agentic reporting pipelines `period_close` / `vat_return` /
`year_end_close`, period-lock enforcement on `gl_poster.post`,
`accounting_periods` + `period_reports` tables, and a "Reports"
frontend tab), and Phase 4.B (gamification layer ported from
TACL-GROUP/pulse-ai-grow without Supabase: per-call coin auto-credit
on every `agent_decisions` insert, manual self-declared completions
behind manager approval, leaderboard / rewards / redemptions /
adjustments, "Adoption" frontend tab + live strip on AI-spend page)
are all in place.

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
                                 #   wiki (GET pages + revisions; POST /wiki/pages,
                                 #   PUT /wiki/pages/{id} — Phase 4.A write surface
                                 #   gated by `x-agnes-author` header),
                                 #   gamification (Phase 4.B — /gamification/{tasks,
                                 #   completions, rewards, redemptions, leaderboard,
                                 #   today/{id}, balance/{id}, coin_adjustments};
                                 #   manager-only POSTs gated by employees.is_manager
                                 #   resolved from `x-agnes-author`),
                                 #   CORSMiddleware (allows Vite dev origin :5173)
  ingress/                       # routing.yaml — event_type → pipeline mapping
  orchestration/
    swan/                        # OAuth + GraphQL client (Phase 2.A)
    runners/                     # anthropic_runner (Sonnet — default + vision),
                                 #   pydantic_ai_runner (Cerebras OpenAI-compat,
                                 #   gpt-oss-120b — live), adk_runner (stub),
                                 #   cerebras_impl.py (pure schema/parse helpers),
                                 #   base.py (AgentResult / TokenUsage)
    tools/                       # 18 production tools (resolver, classifier,
                                 #   builder, gl_poster, invariants, envelope,
                                 #   period_aggregator, vat_calculator,
                                 #   retained_earnings_builder, report_renderer,
                                 #   wiki_reader, wiki_search (BM25 over FTS5),
                                 #   wiki_writer (post-mortem commit/queue))
    agents/                      # 5 production agents (counterparty, GL, document,
                                 #   anomaly_flag, wiki_post_mortem) — counterparty/
                                 #   GL/anomaly route via default_runner()
                                 #   (env: AGNES_LLM_PROVIDER); all four reasoning
                                 #   agents read the Living Rule Wiki and stamp
                                 #   wiki_references on AgentResult.
    wiki/                        # schema (WikiFrontmatter + parse), loader
                                 #   (load_pages_for_tags, resolve_references),
                                 #   writer (upsert_page — single chokepoint),
                                 #   maintenance (auto-maintained log.md +
                                 #   index.md after every upsert).
    conditions/                  # gating, counterparty, gl, documents, reporting
    pipelines/                   # transaction_booked, transaction_released,
                                 #   document_ingested, external_event,
                                 #   period_close, vat_return, year_end_close
                                 #   (each closing pipeline now ships with
                                 #   read-wiki + draft-post-mortem +
                                 #   write-post-mortem nodes — Phase 4.A),
                                 #   log_and_continue
    store/migrations/            # accounting 0001..0009 (0009 adds
                                 #   accounting_periods, period_reports,
                                 #   journal_entries.posted_at, vat_rates seed,
                                 #   CoA 120 retained earnings, demo periods),
                                 #   audit 0001..0005 (0004 adds wiki_page_id
                                 #   + wiki_revision_id on agent_decisions;
                                 #   0005 adds employees.is_manager + the five
                                 #   gamification tables: gamification_tasks,
                                 #   task_completions, rewards,
                                 #   reward_redemptions, coin_adjustments;
                                 #   seeds Tim as manager + 9 demo tasks +
                                 #   4 demo rewards),
                                 #   orchestration 0001..0004 (0002/0003 ship
                                 #   wiki_pages + wiki_revisions; 0004 adds the
                                 #   FTS5 contentless virtual table for search)
    gamification.py              # Phase 4.B — auto-credit hook
                                 #   (auto_credit_for_decision, called from
                                 #   inside audit.write_decision's write_tx;
                                 #   AUTO_COIN_REWARD = 5/call, idempotent on
                                 #   agent_decision_id), coin_balance helper
                                 #   (computed on read = approved completions
                                 #   + adjustments − pending/approved
                                 #   redemptions), leaderboard, today_summary,
                                 #   is_manager check.
    (executor, dag, registries, cache, audit, event_bus — Phase 1)
  scripts/                       # replay_swan_seed.py — Phase 3 demo replay CLI
  mcp/                           # FastMCP server exposing the agentic surface
                                 #   (run/inspect pipelines, query GL, approve
                                 #   entries, read wiki). Wraps the FastAPI
                                 #   routers in-process via httpx ASGI — no
                                 #   business-logic duplication. Run:
                                 #   `python -m backend.mcp` (stdio) or
                                 #   `python -m backend.mcp --http`.
                                 #   Optional dep: `uv sync --extra mcp`.
  tests/                         # 50+ test files
frontend-lovable/                # PRIMARY frontend (Vite + React 18 + TS + shadcn/ui +
                                 #   Radix + TanStack Query + Zustand 5 + react-router).
                                 #   Cloned from github.com/timbtz/agnes-finance-hub.
                                 #   Dev :5174, proxies every backend prefix → :8000;
                                 #   src/lib/api.ts uses VITE_API_BASE_URL (.env.local
                                 #   points it at the dev origin so the proxy handles
                                 #   routing and CORS is sidestepped).
                                 #   Phase 4.B adds /adoption tab (Today / Leaderboard
                                 #   / Task library / Rewards / Manager queue) plus
                                 #   a live AdoptionStrip on /ai-spend pulling
                                 #   /gamification/leaderboard.
frontend/                        # SECONDARY (kept for reference). Vite + React 18 +
                                 #   TS + Tailwind v4 + Zustand 5 + Motion. Dev :5173.
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
- The Living Rule Wiki is **re-discovered every call** — `wiki.loader.
  load_pages_for_tags` joins `wiki_pages × wiki_revisions` on
  `MAX(revision_number)`, so any page added between runs (via `POST
  /wiki/pages` from the CFO or via the `wiki_post_mortem` agent) is
  visible on the very next call. Never cache wiki structure in memory.
  Every reasoning agent passes `wiki_context=[(page_id, revision_id), …]`
  into `runner.run`; the runner threads it into `prompt_hash` and stamps
  `result.wiki_references`. A wiki edit therefore invalidates exactly
  the agent calls that read that page (cache-key correctness for tool
  nodes flows via the `read-wiki` dep edge — agents are non-cacheable).
- The `wiki.writer.upsert_page` chokepoint auto-maintains `log.md`
  (append-on-edit, prefix `## [YYYY-MM-DD HH:MM] <kind> | <title>`) and
  `index.md` (rebuilt only when a structurally new page is created).
  Both meta-pages are themselves wiki rows; a recursion guard in
  `wiki/maintenance.py` prevents an upsert of `log.md`/`index.md` from
  triggering more upserts. Maintenance failures are soft-fail (warning
  logged) so a maintenance bug never crashes an actual edit.
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

## How to run the dev stack (local demo)

- **Backend** runs on **`127.0.0.1:8001`** in this dev environment —
  port `:8000` is held by another local service (returns a `/login`
  redirect, not Agnes). Start with:
  `AGNES_DATA_DIR=./data .venv/bin/uvicorn backend.api.main:app --workers 1 --host 127.0.0.1 --port 8001`
- **Frontend** (Lovable) runs on **`:5174`** with `npm run dev` from
  `frontend-lovable/`. The Vite proxy default in `vite.config.ts`
  already points at `http://127.0.0.1:8001`. If you start uvicorn on a
  different port, override it on the Vite side:
  `AGNES_BACKEND_URL=http://127.0.0.1:<port> npm run dev`.
- The secondary `frontend/` (Tailwind v4) on `:5173` is kept for
  reference only; primary work is `frontend-lovable/`.
- **Demo seeder.** `python -m backend.scripts.enrich_demo_seed` is the
  one-shot enrichment that turns the rule-only Swan replay into a
  believable agent-attributed dataset (40 agent_decisions linked to
  journal entries, 6 entries flipped to `review`, budget_allocations
  populated so envelopes burn 12–94%, employee_id propagated to
  agent_costs). Idempotent on rerun. Required before the AI Spend
  page, trace drawer, Review tab, and envelope rings show real numbers.
- **Killer SQL endpoint.** `GET /reports/ai-costs?group_by=employee,
  provider` (also `model`, `pipeline`, `node`) drives the AI Spend
  page. Whitelisted group keys, parameter-bound SQL, NULL-employee
  rows surface as `(unattributed)`.

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
