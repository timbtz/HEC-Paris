# Feature: Phase 3 — Reporting Pipelines (Balance Sheet, P&L, Cashflow, VAT, Period Close)

The following plan should be complete, but it's important that you validate documentation and codebase patterns and task sanity before you start implementing.

Pay special attention to naming of existing utils, types, and models. Import from the right files etc. Tools/agents/conditions are registered in `backend/orchestration/__init__.py` — every new registry key MUST be added there or the executor will raise `KeyError` at pipeline load.

## Feature Description

Add the reporting layer described in `Orchestration/pitch/capabilities.md`. The accounting schema is already report-ready (double-entry `journal_lines`, `chart_of_accounts.type`, `journal_entries.{basis, status, accrual_link_id, reversal_of_id}`, `vat_rates`, `decision_traces`, `budget_allocations`). What is missing is:

1. A populated ledger (the 200 seeded `swan_transactions` have not been replayed through `transaction_booked` yet, so `journal_entries` is empty).
2. A small period/posted-at schema delta + a VAT-rate seed so VAT and period-close work cleanly.
3. Six SQL-only report endpoints under `/reports/*` (no agent, pure read).
4. Three YAML-defined agent pipelines that add value over raw SQL: `period_close`, `vat_return`, `year_end_close`.
5. A Frontend "Reports" tab that hydrates from the new endpoints.

This is **Phase 3** in the project lineage (Phase 1 = metalayer foundation, Phase 2 = Swan/document/frontend, Phase 3 = reporting). The pitch wedge ("how much did Anthropic bill us this month, per employee") is already demonstrable via `/envelopes`; this phase adds the broader balance-sheet / P&L / VAT story that judges expect from a "B2B accounting agent."

## User Story

As an engineer demoing Fingent
I want a Reports tab that renders a balance sheet, P&L, cashflow, VAT return, and a period-close run with a click-through audit trace
So that judges see not just the live ledger and envelope rings, but the full standard-financial-report stack falling out of the same audited ledger — with the agent layer earning its keep on accrual proposals and VAT anomaly flagging.

## Problem Statement

`capabilities.md` claims the schema supports balance sheet, P&L, cashflow, and VAT return "today as pure SQL." That is **almost true**:

- The ledger is empty — every report renders zero rows until `swan_transactions` is replayed.
- `vat_rates` has zero rows — VAT return is unbuildable without a seed.
- There is no `accounting_periods` table — period-close and "is this period closed" lock are unenforceable; anyone can backdate `entry_date`.
- No `/reports/*` endpoints exist; the frontend has no Reports tab.
- The three agent pipelines mentioned (`period_close`, `vat_return`, `year_end_close`) do not exist as YAML and have no supporting tools or agents.

Everything else (`gl_poster.post` chokepoint, `write_tx`, executor, dashboard SSE, decision_traces, confidence gate) is already in place and must be **reused, not reinvented**.

## Solution Statement

Five sequential slices, each landable in a single commit:

1. **Slice A — Replay seed Swan transactions** through the existing `transaction_booked` pipeline so the ledger has ~200 entries before any report endpoint exists. (~30 min, no schema change.)
2. **Slice B — Schema additions + VAT seed** as migration `0009`. New `accounting_periods` table, optional `posted_at` on `journal_entries`, and seed `vat_rates` for French TVA. (~1h, one migration.)
3. **Slice C — SQL-only `/reports/*` endpoints** in a new `backend/api/reports.py`. Six routes: `trial_balance`, `balance_sheet`, `income_statement`, `cashflow`, `budget_vs_actuals`, `vat_return`. Pure SQL over the existing schema; no agent, no pipeline. (~3h.)
4. **Slice D — Agent reporting pipelines.** Three YAML pipelines + four new tools + one new agent + a `/pipelines/run/<name>` invocation path. Each writes report output to a new `period_reports` table and follows the existing confidence-gate / review-queue pattern. (~5h.)
5. **Slice E — Frontend Reports tab.** New tab beside Dashboard | Review | Infra; one dropdown (report type) + date picker + table; click-through to existing `TraceDrawer`. (~2h.)

The slices are deliberately ordered so each is **independently shippable and demoable**. After slice C the system already shows real reports; D and E are incremental.

## Feature Metadata

**Feature Type**: New Capability (reporting layer) + small Schema Migration + Frontend Tab
**Estimated Complexity**: Medium-High (~12h cumulative, but each slice is bite-sized)
**Primary Systems Affected**:
- Backend API: new `backend/api/reports.py`; `main.py` router mount.
- Backend orchestration: 4 new tools (`period_aggregator`, `vat_calculator`, `report_renderer`, `retained_earnings_builder`), 1 new agent (`anomaly_flag_agent`), 3 new YAML pipelines, 4 new condition functions, registry entries.
- Backend store: migration `0009`, new `accounting_periods` and `period_reports` tables.
- Backend scripts: a one-shot `replay_swan_seed.py` (Slice A).
- Frontend: new `ReportsTab.tsx` component, Tabs entry, three or four small subcomponents (`ReportTable`, `PeriodPicker`, `ReportTypeSelect`).
- Tests: 4 new test files (`test_replay_swan_seed.py`, `test_reports_api.py`, `test_period_close_pipeline.py`, `test_vat_return_pipeline.py`).

**Dependencies**:
- Backend: no new pip deps. Reuse `aiosqlite`, `pydantic`, `httpx`, `anthropic`, `pyyaml`.
- Frontend: no new deps. Reuse the existing `j<T>` helper in `src/api.ts`, `formatters.ts`, the SSE store, and `TraceDrawer`.

---

## CONTEXT REFERENCES

### Relevant Codebase Files — IMPORTANT: YOU MUST READ THESE FILES BEFORE IMPLEMENTING!

**Pipelines (mirror exactly for the three new YAMLs)**

- `backend/orchestration/pipelines/transaction_booked.yaml` (full file, 52 lines) — canonical YAML shape: `name`, `version`, `trigger.source`, `nodes[].{id, tool|agent, runner, depends_on, when, cacheable}`. Note the gate node pattern (`gate-confidence` → branched `post-entry` / `queue-review` via `when` predicates). Mirror this exactly for `period_close.yaml`.
- `backend/orchestration/pipelines/document_ingested.yaml` — second reference for shape; shows agent invocation pattern with `runner: anthropic`.
- `backend/orchestration/pipelines/log_and_continue.yaml` — minimal one-node pipeline; useful for sanity-checking your YAML loader before you wire the bigger DAGs.

**Tools — reuse and mirror**

- `backend/orchestration/tools/gl_poster.py` (full file, ~162 lines) — **the ONLY sanctioned writer to `journal_entries` outside migrations** (CLAUDE.md hard rule). The closing-period entries posted by `period_close.yaml` and the year-end retained-earnings entries posted by `year_end_close.yaml` MUST go through `gl_poster.post`. Lines 142–154: dashboard event payload `ledger.entry_posted` — frontend's reducer expects this exact shape.
- `backend/orchestration/tools/journal_entry_builder.py` — `build_cash`, `build_accrual`, `match_accrual`, `build_reversal`. The retained-earnings closing entry uses `build_accrual` (or a new `build_closing` if reversibility differs — see Task `CREATE backend/orchestration/tools/retained_earnings_builder.py`).
- `backend/orchestration/tools/confidence_gate.py` (full file, 88 lines) — multiplicative confidence. Lines 64–87 show `{"ok": bool, "needs_review": bool, "computed_confidence": float}` output shape. Reporting pipelines reuse this **as-is** — do not write a parallel gate.
- `backend/orchestration/tools/review_queue.py` (full file) — `enqueue` writes to `review_queue` and emits `review.enqueued` on the dashboard bus. Anomaly-flag fallback in `period_close.yaml` and `vat_return.yaml` calls this same tool.
- `backend/orchestration/tools/invariant_checker.py` — useful reference for the kind of read-only "post-write check" tool the reporting pipelines need (e.g., "trial balance sums to zero").

**Agents — mirror for `anomaly_flag_agent`**

- `backend/orchestration/agents/gl_account_classifier_agent.py` (full file, ~150 lines) — the canonical agent pattern: build `tool` JSONSchema, call `get_runner("anthropic").run(...)`, parse `result.output` dict, return `AgentResult` with `confidence` in the dict. Mirror this exactly. The `anomaly_flag_agent` tool-use schema closes a `proposed_action` enum like `{"flag", "auto_post", "ignore"}`, similar to how `gl_account_classifier_agent` closes `gl_account` to `chart_of_accounts.code`.
- `backend/orchestration/agents/document_extractor.py` — second reference for an agent that returns structured data (line items, totals).

**Write path / locks (NEVER bypass)**

- `backend/orchestration/store/writes.py` (full file, 35 lines) — `write_tx(conn, lock)` async context manager. `BEGIN IMMEDIATE` is mandatory. Every writer (including the report-pipeline tools and migration `0009`) MUST use this. Never call `conn.commit()` directly (CLAUDE.md hard rule, enforced by CI grep).
- `backend/orchestration/store/__init__.py` — `Store` dataclass with per-DB `aiosqlite.Connection` + `asyncio.Lock` attributes (`accounting`, `accounting_lock`, `audit`, `audit_lock`, `orchestration`, `orchestration_lock`).

**Registries — every new tool/agent/condition MUST be wired here**

- `backend/orchestration/registries.py` (full file, 100 lines) — flat-dict registry pattern. `KeyError` on miss is intentional.
- `backend/orchestration/__init__.py` lines 14–41 — **THE registration site**. New tools added in Slice D are registered here at module import. Without this, the YAML loader will fail.

**Migrations — mirror for `0009`**

- `backend/orchestration/store/migrations/accounting/0008_review_queue.py` (full file, ~25 lines) — minimal `CREATE TABLE IF NOT EXISTS` + `CREATE INDEX IF NOT EXISTS` migration. Mirror this for `accounting_periods` and `period_reports`.
- `backend/orchestration/store/migrations/accounting/0007_budget_envelopes.py` lines 40–61 — idempotent seed pattern (`INSERT … WHERE NOT EXISTS`). Mirror this for the `vat_rates` seed in Slice B.
- `backend/orchestration/store/bootstrap.py` — confirms migrations are discovered by ordered filename. Migration `0009_periods_and_vat_seed.py` will auto-apply on next `open_dbs()` call. No manual wiring needed.

**API — list endpoint pattern (mirror for `/reports/*`)**

- `backend/api/runs.py` lines 333–369 — `GET /journal_entries`. Canonical aiosqlite read pattern: `store.<db>.execute(sql, params)` → `await cur.fetchall()` → `await cur.close()` → `_rows_to_dicts(rows)`. Reads do NOT need `write_tx`. Money columns stay as integer cents.
- `backend/api/runs.py` lines 38–45 — `_row_to_dict` / `_rows_to_dicts` exported helpers; reuse, do not reimplement.
- `backend/api/runs.py` lines 86–116 — `get_run`; multi-DB read pattern (cross-DB merge in Python).
- `backend/api/main.py` lines 25–76 — router mount sequence. New `reports_router` mount goes between `runs_router` and `dashboard_router` (no prefix mount; `/reports/*` is on the router itself).
- `backend/api/dashboard.py` (full file, ~65 lines) — SSE pattern. Reporting endpoints in Slice C are non-SSE; the optional Slice D `/pipelines/run/period_close` reuses the existing `/runs/{id}/stream` for progress (no new SSE endpoint).
- `backend/api/runs.py` lines 52–84 — `POST /pipelines/run/{name}` invocation entrypoint. **Reuse this as-is** for triggering `period_close` / `vat_return` / `year_end_close`. No new POST endpoint needed.

**Test patterns**

- `backend/tests/test_list_endpoints.py` (full file, ~70 lines) — fixture pattern (`store`, `app`, `client`), `_seed_basic_entry` helper. Mirror exactly for `test_reports_api.py`.
- `backend/tests/test_runs_api.py` lines 46–124 — `_seed_entry_with_traces`; richer fixture with multiple lines + decision traces. Mirror for the period-close pipeline test (which needs ~20 entries spanning a closed and an open period).
- `backend/tests/conftest.py` — `store` and `fake_anthropic` fixtures. The `fake_anthropic` fixture is what the `anomaly_flag_agent` test uses to avoid hitting the live API.
- `backend/tests/test_dashboard_sse.py` — reference for testing the dashboard event emission from the `report_renderer` tool. **DO NOT** test SSE through `AsyncClient.stream` (httpx `ASGITransport` buffers the whole response — CLAUDE.md hard rule); invoke the route function directly.

**Frontend wiring**

- `frontend/src/App.tsx` (top, ~48 lines) — tab conditional render. Add a `'reports'` case alongside the existing `'dashboard' | 'review' | 'infra'` switch.
- `frontend/src/components/Tabs.tsx` lines 10–14 — `tabs` array literal; add `{ id: 'reports', label: 'Reports' }`.
- `frontend/src/api.ts` lines 8–38 — `j<T>(path, init?)` helper, `BASE` env fallback. Add functions: `fetchTrialBalance`, `fetchBalanceSheet`, `fetchIncomeStatement`, `fetchCashflow`, `fetchBudgetVsActuals`, `fetchVatReturn`. All return typed dicts.
- `frontend/src/types/` — add `reports.ts` with shared `ReportLine`, `ReportEnvelope`, `BalanceSheet`, `IncomeStatement`, etc. types matching the backend pydantic shapes.
- `frontend/src/components/Ledger.tsx` and `frontend/src/components/EnvelopeRings.tsx` — reference for table-rendering style; reuse Tailwind class patterns and `formatters.ts:formatCents`.
- `frontend/src/components/TraceDrawer.tsx` — opened by clicking a row; reuse for "click any report line → see provenance."
- `frontend/vite.config.ts` — already proxies every backend prefix to `:8000`. The new `/reports` prefix needs **one extra entry** in `vite.config.ts` proxies; otherwise CORS will hit you.

### New Files to Create

**Backend**

- `backend/scripts/__init__.py` — empty.
- `backend/scripts/replay_swan_seed.py` — Slice A. CLI: `python -m backend.scripts.replay_swan_seed`. Iterates `swan_transactions` ASC by `execution_date`, fabricates a synthetic Swan webhook payload per row, POSTs to `http://localhost:8000/swan/webhook`. Idempotent (relies on `gl_poster.post` deduping by `swan_transaction_id`).
- `backend/orchestration/store/migrations/accounting/0009_periods_and_vat_seed.py` — Slice B. Creates `accounting_periods` and `period_reports`, adds optional `posted_at` column to `journal_entries`, seeds `vat_rates` (TVA standard 2000bp on `706000`, deductible 2000bp on `4456`, reduced 1000bp on `624` if applicable — verify against PRD).
- `backend/api/reports.py` — Slice C. New `APIRouter()`. Six GET endpoints (specs in `STEP-BY-STEP TASKS`).
- `backend/orchestration/tools/period_aggregator.py` — Slice D. Three callables: `compute_trial_balance`, `compute_open_entries`, `summarize_period`.
- `backend/orchestration/tools/vat_calculator.py` — Slice D. `compute_vat_return(period)` joins `journal_lines` × `vat_rates` valid for the period, returns box-by-box totals.
- `backend/orchestration/tools/retained_earnings_builder.py` — Slice D. `build_closing_entry` constructs the year-end closing journal entry (zeroes revenue/expense into equity); returned shape feeds `gl_poster.post`.
- `backend/orchestration/tools/report_renderer.py` — Slice D. `render(period, report_type, payload)` writes a JSON+markdown blob to `data/blobs/reports/<period>/<report_type>.{json,md}`, registers in `period_reports`, emits `report.rendered` event on dashboard bus.
- `backend/orchestration/agents/anomaly_flag_agent.py` — Slice D. Looks at trial balance + VAT computed totals + prior-period totals; emits zero-or-more flagged anomalies with `confidence`. Mirrors `gl_account_classifier_agent.py` structure.
- `backend/orchestration/conditions/reporting.py` — Slice D. Four predicates: `period_open`, `period_closeable`, `has_anomalies`, `passes_report_confidence`.
- `backend/orchestration/pipelines/period_close.yaml` — Slice D.
- `backend/orchestration/pipelines/vat_return.yaml` — Slice D.
- `backend/orchestration/pipelines/year_end_close.yaml` — Slice D.

**Tests**

- `backend/tests/test_replay_swan_seed.py` — Slice A.
- `backend/tests/test_reports_api.py` — Slice C.
- `backend/tests/test_period_close_pipeline.py` — Slice D.
- `backend/tests/test_vat_return_pipeline.py` — Slice D.
- `backend/tests/test_anomaly_flag_agent.py` — Slice D.

**Frontend**

- `frontend/src/components/ReportsTab.tsx` — Slice E.
- `frontend/src/components/ReportTypeSelect.tsx` — Slice E.
- `frontend/src/components/PeriodPicker.tsx` — Slice E.
- `frontend/src/components/ReportTable.tsx` — Slice E.
- `frontend/src/types/reports.ts` — Slice E.

### Relevant Documentation — YOU SHOULD READ THESE BEFORE IMPLEMENTING!

- `Orchestration/PRDs/RealMetaPRD.md`
  - **§3** (lines 81–107) — "Decision trace is non-negotiable." Every report agent write goes to `audit.agent_decisions` (model, prompt_hash, alternatives, confidence) AND `accounting.decision_traces` (line_id, agent_decision_id_logical, confidence, approver_id). Two rows per decision, always.
  - **§3 line 100** — Integer cents only. VAT splits use integer rounding with documented rule: round the last box so `SUM(box_i) = total`.
  - **§6.3** (lines 479–495) — `journal_entries.basis ∈ {cash, accrual}`. Balance sheet queries `basis='accrual'`; cashflow queries `basis='cash'`. The "true P&L" view UNIONs both and dedupes paired accrual-reversal entries via `accrual_link_id`.
  - **§6.4** (lines 516–520) — Compound confidence floor 0.50 (tunable per `confidence_thresholds` row). For reports, raise the floor to 0.75 — period-close and VAT have a higher cost of error than per-transaction GL classification.
  - **§7.3–7.4** (lines 704–843) — `transaction_booked` and `document_ingested` pipeline specs; mirror their structure exactly.
  - **§7.5** (lines 847–1000) — schema spec. `chart_of_accounts.type` enum, `journal_entries.{basis, accrual_link_id, reversal_of_id}`, `decision_traces` shape, `vat_rates` shape — all already implemented; just confirm the `0009` migration does not collide.
  - Search the document for `"period"`, `"close"`, `"retained earnings"`, `"VAT"`, `"balance sheet"` to surface any prescriptive shape I may have missed.

- `Orchestration/pitch/capabilities.md` (full file, 59 lines) — the customer-facing description of what's promised. The plan must deliver **at minimum** what this document describes (trial balance, balance sheet, P&L, VAT return, cashflow, budget vs actuals + the three agent pipelines). Do NOT silently scope-cut; if something can't land, raise it.

- `Dev orchestration/_exports_for_b2b_accounting/02_YAML_WORKFLOW_DSL.md` §2 (lines 42–92) — per-node YAML keys. Required: `id`, `tool` XOR `agent`, `depends_on` (default `[]`), optional `when`, `cacheable`. Nothing beyond these keys is supported by the loader.
- `Dev orchestration/_exports_for_b2b_accounting/03_SQLITE_BACKBONE.md` §4 (lines 167–181) — index every FK and every status flag. For the new `period_reports` table, add `idx_period_reports_period`, `idx_period_reports_status`.
- `Dev orchestration/_exports_for_b2b_accounting/04_AGENT_PATTERNS.md` §3a (lines 112–147) — `ToolResult` contract. Every tool/agent in the report pipelines returns `{result, confidence, evidence_ids, refusal?}` shape. The Anomaly agent's `evidence_ids` is the list of journal_line IDs it flagged.

### Patterns to Follow

**Naming Conventions**

- Module names: `snake_case` (e.g., `period_aggregator.py`, `report_renderer.py`).
- Tool callables: lower-snake (e.g., `compute_trial_balance`, `render`, `build_closing_entry`).
- Agent module file: `<purpose>_agent.py` (mirror `gl_account_classifier_agent.py`); registered as `agents.<short_name>:run` (mirror `agents.gl_account_classifier:run`).
- YAML pipelines: lowercase, underscore-separated, `.yaml` extension (mirror `transaction_booked.yaml`).
- Pydantic response models: `PascalCase` (e.g., `BalanceSheetResponse`, `VatReturnResponse`); single-trailing-`Response` suffix.
- Frontend components: `PascalCase.tsx` (mirror `Ledger.tsx`, `EnvelopeRings.tsx`).
- Frontend types: `camelCase` field names (mirror `frontend/src/types/`).

**Money Handling**

- Integer cents everywhere. **NEVER** introduce a `float` on the money path.
- VAT splits: when a single transaction has VAT split across boxes, round the LAST box so the sum is exact: `boxes[-1] = total - sum(boxes[:-1])`. Document this rule with a one-line comment in `vat_calculator.py`.
- Currency column: schema does not yet support multi-currency. Hard-code `EUR` in response envelopes; add a `# TODO: multi-currency` comment but do NOT implement it.

**Error Handling**

- Match `gl_poster.py`: `HTTPException(status_code=400, detail=...)` for caller-fixable errors; raise plain `RuntimeError` for invariant violations (executor catches and emits `node_failed`).
- API endpoints: `HTTPException` only.
- Tool internals: bare `raise` from a `try/except` so the executor sees the original traceback.

**Logging Pattern**

- Tools use `logger = logging.getLogger(__name__)`; emit at `INFO` for happy-path, `WARNING` for confidence-below-floor, `ERROR` for invariant violation. Mirror `gl_poster.py`.
- Dashboard event payloads: emit on the `event_bus` with `event_type` ∈ `{"report.run_started", "report.rendered", "report.flagged", "period.closed"}`. Frontend reducer must be told about these (Slice E).

**SQL Patterns**

- All read queries use `store.<db>.execute(sql, params)` → `await cur.fetchall()` → `await cur.close()` → `_rows_to_dicts(rows)`. No ORMs.
- Use parameterized queries always. Never f-string SQL values; only f-string the WHERE-clause skeleton conditional on optional filters (mirror `runs.py:341`).
- For the SQL-only reports, use **CTEs** instead of subqueries when the report joins three or more tables (better SQLite query planner behavior; see `03_SQLITE_BACKBONE.md` §4).

**Confidence Rule**

- Tool outputs MUST include a `confidence` field if the value is uncertain (multiplied by upstream values in `confidence_gate`). For deterministic SQL tools (`period_aggregator`, `vat_calculator`), set `confidence: 1.0`.
- Agent outputs (`anomaly_flag_agent`) MUST emit confidence in the tool-use input schema, like `gl_account_classifier_agent.py` does on line ~110.

---

## IMPLEMENTATION PLAN

### Phase 1: Foundation — Populate Ledger + Schema Delta (Slices A & B)

**Goal**: A live ledger with ~200 entries; an `accounting_periods` table; a seeded `vat_rates`. After this phase, the SQL-only reports in Phase 2 can be validated against real data.

**Tasks**:
- Slice A: write and run `backend/scripts/replay_swan_seed.py`.
- Slice B: write migration `0009_periods_and_vat_seed.py`; run on fresh DB; verify counts.

### Phase 2: Core Implementation — SQL-Only Report Endpoints (Slice C)

**Goal**: Six `/reports/*` endpoints landing pure-SQL responses; tested with real ledger data; consumed by frontend curl/Postman before any UI is built.

**Tasks**:
- Create `backend/api/reports.py` with router + six handlers.
- Mount router in `main.py`.
- Define pydantic response models inline (no separate models file; mirror `runs.py` style).
- Add `test_reports_api.py` covering each endpoint with seeded fixtures.

### Phase 3: Agent Pipelines — Period Close, VAT Return, Year-End (Slice D)

**Goal**: Three YAML pipelines + four tools + one agent, registered and runnable via `POST /pipelines/run/<name>`. Each writes to `period_reports`, emits dashboard events, and uses `gl_poster.post` for any new journal entries.

**Tasks**:
- Create the four tools and one agent.
- Register all five in `backend/orchestration/__init__.py`.
- Create the four conditions in `conditions/reporting.py`; register them.
- Author the three YAML pipelines.
- Add per-pipeline tests with `fake_anthropic` fixture.

### Phase 4: Integration — Frontend Reports Tab (Slice E)

**Goal**: A working "Reports" tab in the dashboard that shows balance sheet / P&L / cashflow / VAT / budget; click-through opens `TraceDrawer`; the period-close button kicks off the pipeline and shows a `RunProgressOverlay`.

**Tasks**:
- Add `'reports'` to `Tabs`.
- Build `ReportsTab.tsx` with three sub-components.
- Add API methods and types.
- Wire the period-close button to `POST /pipelines/run/period_close`.

### Phase 5: Testing & Validation

**Goal**: Full suite green, CI grep audit clean, manual end-to-end validation passes.

**Tasks**:
- Run `uv run pytest` (background, with `pytest-timeout` enforced — see CLAUDE.md "How to run tests").
- Manual: replay seed → fetch each report → run period_close pipeline → see report appear in Reports tab.

---

## STEP-BY-STEP TASKS

Execute every task in order, top to bottom. Each task is atomic and independently testable.

---

### Slice A — Replay Swan Seed (~30 min)

#### CREATE `backend/scripts/__init__.py`

- **IMPLEMENT**: empty file.
- **VALIDATE**: `test -f backend/scripts/__init__.py`

#### CREATE `backend/scripts/replay_swan_seed.py`

- **IMPLEMENT**: CLI entrypoint that iterates rows in `swan_transactions` ASC by `(execution_date, id)`, builds a synthetic webhook envelope (mirror the body in `backend/api/swan_webhook.py:74` — same JSON shape Swan sends in production), POSTs each to `http://{host}:{port}/swan/webhook`. Read host/port from env (`FINGENT_HOST` default `localhost`, `FINGENT_PORT` default `8000`). Log per-row: `[ok|skipped|failed] tx_id elapsed_ms`. Emit a final summary `{posted, skipped, failed}`.
- **PATTERN**: read `backend/api/swan_webhook.py:74` for the expected body shape. Read `backend/orchestration/store/migrations/accounting/0006_demo_swan_transactions.py` for column names and seeded values.
- **IMPORTS**: `httpx`, `aiosqlite`, `asyncio`, `argparse`, `os`. Reuse `backend.orchestration.store.bootstrap.open_dbs` to read the seed.
- **GOTCHA**: idempotency depends on `gl_poster.post` deduping by `swan_transaction_id` — verify by re-running the script; the second run should report all `skipped` (or all-already-posted). If `gl_poster` does NOT yet dedupe, add a check in the script: skip rows where `swan_transactions.id` already has an entry in `journal_lines.swan_transaction_id`.
- **GOTCHA**: the executor runs uvicorn `--workers 1` (CLAUDE.md hard rule) — the script POSTs serially, not concurrently. Do not parallelize.
- **VALIDATE**: start the API server (`uv run uvicorn backend.api.main:app --workers 1`), run `python -m backend.scripts.replay_swan_seed`, assert `journal_entries` count > 100 with `python3 -c "import sqlite3; c=sqlite3.connect('data/accounting.db'); print(c.execute('SELECT COUNT(*) FROM journal_entries').fetchone())"`.

#### CREATE `backend/tests/test_replay_swan_seed.py`

- **IMPLEMENT**: spin up an in-process FastAPI app with the swan_webhook router mounted; mock the executor or run the real `transaction_booked` pipeline against a tmp store; call the script's `main()` with overridden host/port; assert `journal_entries` populated.
- **PATTERN**: `backend/tests/test_list_endpoints.py` for fixture style.
- **VALIDATE**: `uv run pytest backend/tests/test_replay_swan_seed.py -v`.

---

### Slice B — Schema Delta + VAT Seed (~1h)

#### CREATE `backend/orchestration/store/migrations/accounting/0009_periods_and_vat_seed.py`

- **IMPLEMENT** (in this exact order):
  1. `CREATE TABLE IF NOT EXISTS accounting_periods (id INTEGER PRIMARY KEY, code TEXT NOT NULL UNIQUE, start_date TEXT NOT NULL, end_date TEXT NOT NULL, status TEXT NOT NULL CHECK(status IN ('open','closing','closed')) DEFAULT 'open', closed_at TEXT, closed_by INTEGER) STRICT`. Index on `(status, end_date)`.
  2. `CREATE TABLE IF NOT EXISTS period_reports (id INTEGER PRIMARY KEY, period_code TEXT NOT NULL, report_type TEXT NOT NULL, status TEXT NOT NULL CHECK(status IN ('draft','final','flagged')) DEFAULT 'draft', confidence REAL, source_run_id INTEGER, blob_path TEXT, payload_json TEXT, created_at TEXT NOT NULL DEFAULT (datetime('now')), approved_at TEXT, approved_by INTEGER) STRICT`. Index on `(period_code, report_type)` and `(status, created_at)`.
  3. Idempotent column-add: `posted_at TEXT` on `journal_entries`. Use `PRAGMA table_info(journal_entries)` to check first (mirror migration `0007` line ~25 if it does this; otherwise wrap in a try/except).
  4. Seed `vat_rates`: idempotent `INSERT … WHERE NOT EXISTS`. Standard French TVA: `(706000, 2000bp, 2025-01-01, NULL)`, `(4456, 2000bp, 2025-01-01, NULL)`. Add reduced rates per `RealMetaPRD §6.3` if the PRD specifies them; otherwise a `# TODO: extend per PRD` comment.
  5. Seed three `accounting_periods` rows for the demo: `2026-Q1` (closed), `2026-Q2` (closing), `2026-Q3` (open). Use `WHERE NOT EXISTS` guard.
- **PATTERN**: `backend/orchestration/store/migrations/accounting/0008_review_queue.py` (table create) + `0007_budget_envelopes.py` lines 40–61 (idempotent seed).
- **IMPORTS**: `aiosqlite`, that's it.
- **GOTCHA**: the migration loader runs migrations in lex order on filename. `0009` MUST come after `0008`. Do not start the file with `09_` or similar.
- **GOTCHA**: existing `journal_entries` rows have no `posted_at`. Backfill in the same migration: `UPDATE journal_entries SET posted_at = created_at WHERE posted_at IS NULL`.
- **VALIDATE**: delete `data/accounting.db`, restart the app (or run `python -c "import asyncio; from backend.orchestration.store.bootstrap import open_dbs; asyncio.run(open_dbs())"`), then `python3 -c "import sqlite3; c=sqlite3.connect('data/accounting.db'); print([r[0] for r in c.execute(\"SELECT name FROM sqlite_master WHERE type='table'\").fetchall()])"` — assert `accounting_periods` and `period_reports` in the list.
- **VALIDATE**: `python3 -c "import sqlite3; c=sqlite3.connect('data/accounting.db'); print(c.execute('SELECT COUNT(*) FROM vat_rates').fetchone())"` — expect at least 2.

#### UPDATE `backend/orchestration/tools/gl_poster.py`

- **IMPLEMENT**: add a guard before `INSERT INTO journal_entries`: query `accounting_periods` for the period containing `entry_date`; if the row's `status='closed'`, raise `RuntimeError(f"period {code} is closed; cannot post entry_date={entry_date}")`. Set `posted_at = datetime.utcnow().isoformat()` on the new entry.
- **PATTERN**: existing `gl_poster.post` (lines 36–162) for the write_tx pattern.
- **GOTCHA**: this is the **only** sanctioned write to `journal_entries` (CLAUDE.md hard rule). Do not duplicate the period-lock check elsewhere.
- **VALIDATE**: a test in `test_period_close_pipeline.py` attempts to post into the `closed` period and asserts `RuntimeError`.

---

### Slice C — SQL-Only Report Endpoints (~3h)

#### CREATE `backend/api/reports.py`

Six GET endpoints. All return `{currency: 'EUR', as_of|period, lines: [...], totals: {...}}`. Money is integer cents.

- **IMPLEMENT** `router = APIRouter(prefix='/reports')`, then:

  **`GET /reports/trial_balance`**
  - Query params: `as_of: str (YYYY-MM-DD)`, optional `basis: Literal['cash','accrual']` default `accrual`.
  - SQL (CTE):
    ```sql
    WITH posted AS (
      SELECT jl.account_code, jl.debit_cents, jl.credit_cents
      FROM journal_lines jl
      JOIN journal_entries je ON je.id = jl.entry_id
      WHERE je.status = 'posted'
        AND je.entry_date <= ?
        AND je.basis = ?
    )
    SELECT coa.code, coa.name, coa.type,
           COALESCE(SUM(p.debit_cents),0) AS total_debit_cents,
           COALESCE(SUM(p.credit_cents),0) AS total_credit_cents,
           COALESCE(SUM(p.debit_cents),0) - COALESCE(SUM(p.credit_cents),0) AS balance_cents
    FROM chart_of_accounts coa
    LEFT JOIN posted p ON p.account_code = coa.code
    GROUP BY coa.code
    ORDER BY coa.code
    ```
  - Response: `{as_of, basis, currency: 'EUR', lines: [...], totals: {total_debit_cents, total_credit_cents}}`.

  **`GET /reports/balance_sheet`**
  - Query params: `as_of: str`, optional `basis` default `accrual`.
  - Same CTE as trial_balance, but `WHERE coa.type IN ('asset','liability','equity','contra')`. Group by `type`. Note: until Slice D's year-end-close pipeline lands, fold expense+revenue net into a synthetic `provisional_retained_earnings` line — flag this in the response with `provisional: true`.
  - Response: `{as_of, basis, currency, sections: {assets: [...], liabilities: [...], equity: [...]}, totals: {total_assets_cents, total_liabilities_equity_cents, balanced: bool}, provisional: bool}`.

  **`GET /reports/income_statement`**
  - Query params: `from: str`, `to: str`, optional `basis` default `accrual`.
  - Same CTE shape but `WHERE coa.type IN ('revenue','expense')` and `entry_date BETWEEN ? AND ?`.
  - Response: `{from, to, basis, currency, sections: {revenue: [...], expense: [...]}, totals: {total_revenue_cents, total_expense_cents, net_income_cents}}`.

  **`GET /reports/cashflow`**
  - Query params: `from: str`, `to: str`. Direct method.
  - SQL: filter `journal_lines` joined to entries where one of the lines hits a cash account (`512` Banque). Group by counterparty category (operating/investing/financing) — for the demo, derive from `chart_of_accounts.type`: revenue+expense = operating; asset (non-cash) = investing; equity+liability = financing.
  - Response: `{from, to, currency, sections: {operating, investing, financing}, totals: {net_change_cents, opening_balance_cents, closing_balance_cents}}`.

  **`GET /reports/budget_vs_actuals`**
  - Query params: `period: str (YYYY-MM)`, optional `employee_id: int|null`, `category: str|null`.
  - SQL: SELECT from `budget_envelopes` LEFT JOIN `budget_allocations`; mirror the existing `/envelopes` query in `runs.py:376`.
  - Response: `{period, currency, lines: [{envelope_id, scope_kind, scope_id, category, cap_cents, used_cents, remaining_cents, pct_used}], totals: {total_cap_cents, total_used_cents}}`.

  **`GET /reports/vat_return`**
  - Query params: `period: str (YYYY-MM)`.
  - SQL: join `journal_lines` × `vat_rates` valid for the period (`vat_rates.valid_from <= entry_date AND (vat_rates.valid_to IS NULL OR vat_rates.valid_to > entry_date)`), GROUP BY `rate_bp`.
  - Response: `{period, currency, lines: [{gl_account, rate_bp, base_cents, vat_cents}], totals: {collected_cents, deductible_cents, net_due_cents}}`.

- **PATTERN**: `backend/api/runs.py:333-369` — exact aiosqlite read pattern, `_rows_to_dicts`, `request.app.state.store`.
- **IMPORTS**: `from fastapi import APIRouter, HTTPException, Query, Request`, `from typing import Annotated, Any, Literal`, `from .runs import _rows_to_dicts` (re-export from runs to avoid duplication; if cyclic, copy `_rows_to_dicts` into a new `backend/api/_helpers.py`).
- **GOTCHA**: the empty-ledger case must not 500. If the query returns zero rows, return `{lines: [], totals: {...zero...}}`. Test for this explicitly.
- **GOTCHA**: balance-sheet `provisional: true` until year-end-close lands. The acceptance test asserts this flag flips after the year_end_close pipeline runs.
- **VALIDATE** (per endpoint): `curl http://localhost:8000/reports/trial_balance?as_of=2026-04-25 | jq` and confirm shape.

#### UPDATE `backend/api/main.py`

- **IMPLEMENT**: add `from .reports import router as reports_router` and `app.include_router(reports_router)` between `runs_router` and `dashboard_router`.
- **PATTERN**: lines 25–76 of `main.py`.
- **VALIDATE**: `curl http://localhost:8000/openapi.json | jq '.paths | keys'` should include `/reports/*` paths.

#### UPDATE `frontend/vite.config.ts`

- **IMPLEMENT**: add `/reports` to the proxy list.
- **PATTERN**: existing proxy entries in `vite.config.ts`.
- **VALIDATE**: `cd frontend && npm run dev`, browser fetch from `http://localhost:5173/reports/trial_balance?as_of=2026-04-25` returns 200.

#### CREATE `backend/tests/test_reports_api.py`

- **IMPLEMENT**: one test per endpoint. Use a `_seed_ledger` helper that fabricates ~10 entries spanning two periods, with mixed cash/accrual basis. Assert response shape, totals correctness, integer-cents enforcement.
- **PATTERN**: `backend/tests/test_list_endpoints.py` for fixture style; `backend/tests/test_runs_api.py:_seed_entry_with_traces` for seed shape.
- **GOTCHA**: balance-sheet test must check `provisional: true` (year-end-close hasn't run).
- **GOTCHA**: VAT return test must seed `vat_rates` first (the test fixture does NOT inherit migration `0009` seeds — it uses tmp DBs).
- **VALIDATE**: `uv run pytest backend/tests/test_reports_api.py -v`.

---

### Slice D — Reporting Pipelines (~5h)

#### CREATE `backend/orchestration/tools/period_aggregator.py`

- **IMPLEMENT**: three async callables with `(ctx: FingentContext) -> dict[str, Any]` signatures.
  - `compute_trial_balance(ctx)` — reads `period_code` from `ctx.trigger_payload`, runs the same trial-balance SQL as the `/reports/trial_balance` endpoint, returns `{trial_balance: [...], total_debit_cents, total_credit_cents, balanced: bool, confidence: 1.0}`.
  - `compute_open_entries(ctx)` — finds journal_entries with `accrual_link_id IS NULL AND basis='accrual'` whose `entry_date` falls inside the closing period; returns `{open_entries: [...], count: int, confidence: 1.0}`.
  - `summarize_period(ctx)` — final aggregator; returns `{period_code, trial_balance, open_entries, anomalies, confidence}` for `report_renderer` to write.
- **PATTERN**: `backend/orchestration/tools/invariant_checker.py` for read-only multi-statement structure.
- **IMPORTS**: standard. **No** `write_tx` (these are read-only).
- **GOTCHA**: do NOT duplicate the SQL from `reports.py`; either import the helper (preferred — refactor the trial-balance SQL into a shared `backend/orchestration/store/queries.py`) or accept the small duplication and mark it with a `# DRY-target` comment for a future cleanup.
- **VALIDATE**: invoked from `test_period_close_pipeline.py`.

#### CREATE `backend/orchestration/tools/vat_calculator.py`

- **IMPLEMENT**: `compute_vat_return(ctx)`. Reads `period_code` from `ctx.trigger_payload`. Joins `journal_lines × vat_rates` valid for the period; produces box-by-box totals. Apply the integer-cents rounding rule (last box absorbs the rounding remainder). Returns `{period_code, lines: [...], totals: {collected_cents, deductible_cents, net_due_cents}, confidence: 1.0}`.
- **PATTERN**: `backend/orchestration/tools/budget_envelope.py` for SQL-heavy tool structure.
- **GOTCHA**: VAT computation is deterministic, so `confidence: 1.0`. The agent layer's value-add is on **anomaly detection** (next tool), not the calculation itself.
- **VALIDATE**: invoked from `test_vat_return_pipeline.py`.

#### CREATE `backend/orchestration/tools/retained_earnings_builder.py`

- **IMPLEMENT**: `build_closing_entry(ctx)`. Reads the income-statement totals for the fiscal year from `ctx.get('compute-income-statement')`. Constructs a journal entry that DEBITs every revenue account by its credit balance, CREDITs every expense account by its debit balance, and offsets the net into a `retained_earnings` equity account (CoA code TBD — propose `120` per French PCG; if that conflicts with existing CoA, ask the user). Returns the entry shape that `gl_poster.post` expects (mirror `journal_entry_builder.build_accrual` output).
- **PATTERN**: `backend/orchestration/tools/journal_entry_builder.py:build_accrual`.
- **GOTCHA**: this writes a JOURNAL ENTRY but does NOT post it directly; the YAML pipeline downstream node (`tools.gl_poster:post`) does the post. Maintain the chokepoint rule.
- **GOTCHA**: if `chart_of_accounts` does not contain a retained-earnings code, migration `0009` MUST add one.
- **VALIDATE**: invoked from `test_period_close_pipeline.py` (year-end variant).

#### CREATE `backend/orchestration/tools/report_renderer.py`

- **IMPLEMENT**: `render(ctx)`. Reads `report_type` and `period_code` from `ctx.trigger_payload`, gathers the upstream node outputs (e.g., `ctx.get('summarize-period')`), serializes a JSON payload, writes to `data/blobs/reports/{period_code}/{report_type}.json` and a markdown sibling, INSERTs a `period_reports` row inside `write_tx`, emits `report.rendered` event on the dashboard bus. Returns `{report_id, blob_path, status: 'draft'|'flagged', confidence}`.
- **PATTERN**: `backend/orchestration/tools/gl_poster.py` for the `write_tx` + `publish_event_dashboard` sequence (lines 142–154).
- **IMPORTS**: `from backend.orchestration.store.writes import write_tx`, `from backend.orchestration.event_bus import publish_event_dashboard`, `import json, pathlib`.
- **GOTCHA**: blob path must be inside `data/blobs/`; create parent dir with `mkdir(parents=True, exist_ok=True)`.
- **GOTCHA**: status is `flagged` if upstream `confidence < 0.75` (the report-confidence floor — see `confidence_thresholds` table in PRD §6.4).
- **VALIDATE**: pipeline test asserts a `period_reports` row exists with the expected `report_type` and `payload_json`.

#### CREATE `backend/orchestration/agents/anomaly_flag_agent.py`

- **IMPLEMENT**: agent that takes `(trial_balance, vat_return, prior_period_summary)` and proposes zero-or-more anomalies with confidence per-anomaly. Tool-use schema closes a `kind` enum like `{"vat_mismatch", "balance_drift", "missing_accrual", "outlier_expense"}`. Output: `{anomalies: [{kind, line_ids, evidence, confidence, refusal?}], overall_confidence}`.
- **PATTERN**: `backend/orchestration/agents/gl_account_classifier_agent.py` (full file). Mirror the `tool` JSONSchema build, the system-prompt structure, the `runner.run(...)` invocation, the writeback pattern (here the writeback is INSERTing into `review_queue` if `kind == 'flag'` AND `confidence < 0.75`).
- **MODEL**: `claude-sonnet-4-6` (matches existing agents).
- **GOTCHA**: the agent must NOT itself write to `period_reports` or `journal_entries` — it returns a structured proposal; downstream YAML nodes handle the side effects.
- **VALIDATE**: `test_anomaly_flag_agent.py` with `fake_anthropic` fixture; assert output schema.

#### CREATE `backend/orchestration/conditions/reporting.py`

- **IMPLEMENT** four predicates `(ctx) -> bool`:
  - `period_open(ctx)`: returns True if `accounting_periods` row for `ctx.trigger_payload['period_code']` has `status='open'`.
  - `period_closeable(ctx)`: True if `status IN ('open','closing')` AND no entries with `entry_date > now()` in that period.
  - `has_anomalies(ctx)`: True if `ctx.get('flag-anomalies')['anomalies']` is non-empty.
  - `passes_report_confidence(ctx)`: True if `ctx.get('summarize-period')['confidence'] >= 0.75`.
- **PATTERN**: `backend/orchestration/conditions/gating.py`.
- **VALIDATE**: pipeline tests exercise each branch.

#### UPDATE `backend/orchestration/__init__.py`

- **IMPLEMENT**: register the four new tools, the new agent, and the four new conditions. Mirror the existing block exactly.
  ```python
  register_tool("tools.period_aggregator:compute_trial_balance", "backend.orchestration.tools.period_aggregator:compute_trial_balance")
  # ... etc for compute_open_entries, summarize_period, compute_vat_return, build_closing_entry, render
  register_agent("agents.anomaly_flag:run", "backend.orchestration.agents.anomaly_flag_agent:run")
  register_condition("conditions.reporting:period_open", "backend.orchestration.conditions.reporting:period_open")
  # ... etc
  ```
- **PATTERN**: lines 14–41 of `backend/orchestration/__init__.py`.
- **VALIDATE**: `python -c "from backend.orchestration.registries import get_tool; print(get_tool('tools.period_aggregator:compute_trial_balance'))"` returns the callable, no `KeyError`.

#### CREATE `backend/orchestration/pipelines/period_close.yaml`

- **IMPLEMENT** (mirror `transaction_booked.yaml` exactly):
  ```yaml
  name: period_close
  version: 1
  trigger:
    source: pipeline:period_close
  nodes:
    - id: validate-period
      tool: tools.period_aggregator:compute_trial_balance
      when: conditions.reporting:period_closeable
    - id: compute-trial-balance
      tool: tools.period_aggregator:compute_trial_balance
      depends_on: [validate-period]
    - id: compute-open-entries
      tool: tools.period_aggregator:compute_open_entries
      depends_on: [validate-period]
    - id: compute-vat
      tool: tools.vat_calculator:compute_vat_return
      depends_on: [validate-period]
    - id: flag-anomalies
      agent: agents.anomaly_flag:run
      runner: anthropic
      depends_on: [compute-trial-balance, compute-open-entries, compute-vat]
    - id: summarize-period
      tool: tools.period_aggregator:summarize_period
      depends_on: [flag-anomalies]
    - id: gate-confidence
      tool: tools.confidence_gate:run
      depends_on: [summarize-period]
    - id: render-report
      tool: tools.report_renderer:render
      depends_on: [summarize-period, gate-confidence]
      when: conditions.reporting:passes_report_confidence
    - id: queue-review
      tool: tools.review_queue:enqueue
      depends_on: [summarize-period, gate-confidence]
      when: conditions.gating:needs_review
  ```
- **GOTCHA**: do not introduce new YAML keys beyond `{name, version, trigger.source, nodes[].{id, tool|agent, runner, depends_on, when, cacheable}}` — the loader rejects unknown keys (or worse, silently drops them).

#### CREATE `backend/orchestration/pipelines/vat_return.yaml`

- **IMPLEMENT** smaller pipeline: validate-period → compute-vat → flag-anomalies → render-report. No journal-posting needed; VAT return is read-only (the actual TVA payment is a separate transaction posted via `transaction_booked`).
- **PATTERN**: as `period_close.yaml`, minus the journal-posting branch.

#### CREATE `backend/orchestration/pipelines/year_end_close.yaml`

- **IMPLEMENT**: extends `period_close` with two extra nodes:
  - `build-closing-entry` (`tools.retained_earnings_builder:build_closing_entry`) after `compute-trial-balance`.
  - `post-closing-entry` (`tools.gl_poster:post`) after `build-closing-entry` and `gate-confidence`, gated `when: conditions.gating:passes_confidence`.
- **GOTCHA**: this is the ONLY pipeline that posts new journal entries during a close. `period_close.yaml` is read-only.

#### UPDATE `backend/ingress/routing.yaml` (OPTIONAL — only if event-triggered)

- **IMPLEMENT**: not strictly required if you're invoking via `POST /pipelines/run/<name>`. If you want to add an event-triggered close (e.g., on the 5th of each month), add a route here. For Phase 3 demo, skip this and rely on the manual POST.

#### CREATE `backend/tests/test_period_close_pipeline.py`

- **IMPLEMENT**: end-to-end test that seeds a small ledger, calls the executor with `period_close` payload, asserts `period_reports` row exists, asserts dashboard event emitted, asserts no journal entries posted (read-only pipeline).
- **PATTERN**: `backend/tests/test_runs_api.py` — full executor invocation via `pipelines/run/<name>`.
- **GOTCHA**: pipeline tests must run with `fake_anthropic` fixture so the agent is mocked.
- **VALIDATE**: `uv run pytest backend/tests/test_period_close_pipeline.py -v`.

#### CREATE `backend/tests/test_vat_return_pipeline.py`

- **IMPLEMENT**: similar shape; assert VAT box totals match a hand-computed expected value.
- **VALIDATE**: `uv run pytest backend/tests/test_vat_return_pipeline.py -v`.

#### CREATE `backend/tests/test_anomaly_flag_agent.py`

- **IMPLEMENT**: unit test for the agent in isolation; uses `fake_anthropic` to stub the runner; asserts output schema.
- **VALIDATE**: `uv run pytest backend/tests/test_anomaly_flag_agent.py -v`.

---

### Slice E — Frontend Reports Tab (~2h)

#### UPDATE `frontend/src/components/Tabs.tsx`

- **IMPLEMENT**: add `{ id: 'reports', label: 'Reports' }` to the `tabs` array. Update the `TabId` union type.
- **PATTERN**: existing array literal lines 10–14.
- **VALIDATE**: tab renders in browser.

#### UPDATE `frontend/src/App.tsx`

- **IMPLEMENT**: add `'reports'` case in the tab-conditional switch; render `<ReportsTab />`.
- **PATTERN**: existing cases lines 34–44.
- **VALIDATE**: clicking the Reports tab renders the new component.

#### CREATE `frontend/src/types/reports.ts`

- **IMPLEMENT**: TypeScript shapes matching the six pydantic responses from Slice C. Currency is always `'EUR'`. Money fields end in `_cents` and are typed `number`.
- **PATTERN**: existing files in `frontend/src/types/`.

#### UPDATE `frontend/src/api.ts`

- **IMPLEMENT**: add six functions: `fetchTrialBalance`, `fetchBalanceSheet`, `fetchIncomeStatement`, `fetchCashflow`, `fetchBudgetVsActuals`, `fetchVatReturn`. All use `j<T>(path)` helper. Plus `runPeriodClose(period: string)` POSTing to `/pipelines/run/period_close`.
- **PATTERN**: lines 8–38 of `api.ts`.
- **VALIDATE**: TypeScript compiles (`cd frontend && npm run build`).

#### CREATE `frontend/src/components/ReportTypeSelect.tsx`

- **IMPLEMENT**: simple `<select>` over the six report types. Controlled component with `value` and `onChange` props.

#### CREATE `frontend/src/components/PeriodPicker.tsx`

- **IMPLEMENT**: depending on the selected report, render either an `as_of` date picker (trial_balance, balance_sheet) or a from/to range (income_statement, cashflow) or a YYYY-MM picker (budget_vs_actuals, vat_return). Controlled.

#### CREATE `frontend/src/components/ReportTable.tsx`

- **IMPLEMENT**: generic table that renders `lines: [{label, value_cents, type?}]`. Group by `type` if provided. Click on a row opens `TraceDrawer` (next slice — for now, just emit `onLineClick(lineId)`).
- **PATTERN**: `frontend/src/components/Ledger.tsx` for table styling, `formatCents` from `formatters.ts`.

#### CREATE `frontend/src/components/ReportsTab.tsx`

- **IMPLEMENT**: composes `ReportTypeSelect` + `PeriodPicker` + a "Run period close" button + `ReportTable`. On report-type or period change, calls the matching `fetch*` function from `api.ts`. On period-close click, calls `runPeriodClose(period)` and shows `RunProgressOverlay` keyed on the returned `run_id`.
- **PATTERN**: `frontend/src/components/EnvelopeRings.tsx` for hydration-on-mount + rerender-on-change.
- **VALIDATE**: manually click through every report type with the Vite dev server running and the backend serving real data.

---

## TESTING STRATEGY

### Unit Tests

- Each new tool: a focused unit test exercising the SQL/computation logic against a tmp store seeded with a minimal fixture.
- Each new condition: a parameterized test asserting True/False boundary cases.
- The new agent: a `fake_anthropic`-backed test asserting output-schema parsing and writeback behavior.

### Integration Tests

- `test_reports_api.py` — six tests, one per endpoint, with shared `_seed_ledger` fixture (~10 entries).
- `test_period_close_pipeline.py` — end-to-end through the executor; assert `period_reports` row, dashboard event, no journal entries posted.
- `test_vat_return_pipeline.py` — same shape, assert VAT box totals.
- `test_replay_swan_seed.py` — runs the script against a small fixture; asserts `journal_entries` count.

### Edge Cases

- Empty ledger: every report endpoint returns an empty `lines: []` and zero totals (not 500).
- Backdated entry: posting an entry into a `closed` period must fail with `RuntimeError` (period-lock enforcement).
- Unbalanced trial balance: the `compute_trial_balance` tool returns `balanced: false`, `confidence: <0.75`, the gate routes to `queue-review`.
- Empty `vat_rates` (legacy DB): VAT report returns `lines: [], net_due_cents: 0` — verify migration `0009` is applied before the endpoint is exercised.
- Period-close on an `open` period (not yet closeable): the `period_closeable` condition returns False, the `validate-period` node is skipped, the executor short-circuits, no `period_reports` row is written.
- Year-end-close re-run idempotency: running `year_end_close` twice for the same fiscal year must NOT post duplicate closing entries — guard in `retained_earnings_builder.build_closing_entry` by checking `period_reports` for an existing `final` row.
- VAT rounding: a transaction with €100 split across two boxes at uneven rates must sum to exactly 10000 cents — assert in test.
- Multi-currency transaction (out of scope): if a `journal_lines` row's account_code doesn't have a EUR mapping, the report MUST exclude it and emit a warning in the response (the `currency` field is hard-coded to `'EUR'`; document this limitation).

---

## VALIDATION COMMANDS

Execute every command to ensure zero regressions and 100% feature correctness.

### Level 1: Syntax & Style

```bash
uv run python -c "import backend.orchestration; import backend.api.reports"  # import-time crash test
uv run python -c "from backend.orchestration.registries import get_tool, get_agent, get_condition; \
  for k in ['tools.period_aggregator:compute_trial_balance', 'tools.vat_calculator:compute_vat_return', 'tools.report_renderer:render', 'tools.retained_earnings_builder:build_closing_entry']: get_tool(k); \
  get_agent('agents.anomaly_flag:run'); \
  for k in ['conditions.reporting:period_open', 'conditions.reporting:period_closeable', 'conditions.reporting:has_anomalies', 'conditions.reporting:passes_report_confidence']: get_condition(k); \
  print('all registered')"
cd frontend && npm run build  # TS strict mode
```

### Level 2: Unit Tests

```bash
uv run pytest backend/tests/test_replay_swan_seed.py -v
uv run pytest backend/tests/test_reports_api.py -v
uv run pytest backend/tests/test_anomaly_flag_agent.py -v
```

### Level 3: Integration Tests

Run in **background** per CLAUDE.md "How to run tests":

```bash
uv run pytest -v  # full suite
```

Specific pipeline tests:

```bash
uv run pytest backend/tests/test_period_close_pipeline.py backend/tests/test_vat_return_pipeline.py -v
```

### Level 4: Manual Validation

```bash
# 1. Apply migration & replay seed
rm -f data/accounting.db data/audit.db data/orchestration.db
uv run uvicorn backend.api.main:app --workers 1 &  # starts → applies all migrations
sleep 2
uv run python -m backend.scripts.replay_swan_seed

# 2. Verify ledger populated
python3 -c "import sqlite3; c=sqlite3.connect('data/accounting.db'); print('entries:', c.execute('SELECT COUNT(*) FROM journal_entries').fetchone()[0]); print('lines:', c.execute('SELECT COUNT(*) FROM journal_lines').fetchone()[0])"

# 3. Each SQL endpoint
curl -s 'http://localhost:8000/reports/trial_balance?as_of=2026-04-25' | jq
curl -s 'http://localhost:8000/reports/balance_sheet?as_of=2026-04-25' | jq
curl -s 'http://localhost:8000/reports/income_statement?from=2026-01-01&to=2026-04-25' | jq
curl -s 'http://localhost:8000/reports/cashflow?from=2026-01-01&to=2026-04-25' | jq
curl -s 'http://localhost:8000/reports/budget_vs_actuals?period=2026-04' | jq
curl -s 'http://localhost:8000/reports/vat_return?period=2026-03' | jq

# 4. Pipeline trigger
curl -s -X POST 'http://localhost:8000/pipelines/run/period_close' -H 'content-type: application/json' \
  -d '{"period_code": "2026-Q1"}' | jq
# → should return {run_id: ...}; SSE stream at /runs/{id}/stream shows progress

# 5. Period_reports populated
python3 -c "import sqlite3; c=sqlite3.connect('data/accounting.db'); \
  print(c.execute('SELECT id, period_code, report_type, status, confidence FROM period_reports').fetchall())"

# 6. Frontend
cd frontend && npm run dev
# → open http://localhost:5173, click Reports tab, exercise every dropdown
```

### Level 5: Additional Validation

- Open the dashboard SSE in a second terminal: `curl -N http://localhost:8000/dashboard/stream` while running the period_close pipeline; confirm `report.run_started` and `report.rendered` events appear.
- Click any line in the Reports tab → `TraceDrawer` opens with the underlying `decision_traces` rows.

---

## ACCEPTANCE CRITERIA

- [ ] Migration `0009` applies cleanly on a fresh DB and idempotently on an existing DB.
- [ ] `swan_transactions` replay populates `journal_entries` with > 100 rows; second run produces zero new rows (idempotency).
- [ ] All six `/reports/*` endpoints return valid JSON with the documented shape against the populated ledger.
- [ ] Empty-ledger case returns `lines: []`, never 500.
- [ ] `period_close` pipeline runs end-to-end against a closed period; writes one `period_reports` row; emits `report.rendered` on the dashboard SSE.
- [ ] `vat_return` pipeline computes VAT correctly with integer-cents rounding (last box absorbs remainder).
- [ ] `year_end_close` posts the retained-earnings entry via `gl_poster.post` and is re-run-safe.
- [ ] Backdated entry into a closed period raises `RuntimeError` (period-lock enforcement).
- [ ] Frontend Reports tab renders every report; click-through opens `TraceDrawer`.
- [ ] All four new tools, the agent, and the four conditions are registered in `backend/orchestration/__init__.py` (no `KeyError` at executor load).
- [ ] `gl_poster.post` remains the single chokepoint for `journal_entries` writes; no new bypasses introduced.
- [ ] All money paths use integer cents; CI grep audit clean.
- [ ] CLAUDE.md and README.md updated with the new pipeline list, new endpoint list, new migration count, new schema tables.
- [ ] Full pytest suite passes with 15s-timeout-per-test enforced.

---

## COMPLETION CHECKLIST

- [ ] Slice A executed: replay script lands, ledger populated.
- [ ] Slice B executed: migration `0009`, schema delta, VAT seed.
- [ ] Slice C executed: six `/reports/*` endpoints, tests passing.
- [ ] Slice D executed: three pipelines, four tools, one agent, registry entries, tests passing.
- [ ] Slice E executed: Reports tab live, manual click-through validated.
- [ ] Full pytest suite green.
- [ ] CLAUDE.md and README.md updated.
- [ ] Manual end-to-end validation walkthrough completed.

---

## NOTES

**Scope cut deliberately deferred to Phase 4 (post-hackathon):**

- Multi-currency support (`journal_lines.currency` column, FX revaluation table). Schema is single-currency today.
- Streaming reports for huge ledgers (the SQL queries above are fine for ~1000s of entries; not for ~millions).
- PDF rendering of reports — `report_renderer` only writes JSON+markdown blobs in Phase 3. PDF can be a Phase 4 add-on with `weasyprint` or similar.
- A dedicated `confidence_thresholds` row for `report_*` types — for Phase 3, hard-code the 0.75 floor in `report_renderer` with a `# TODO: move to confidence_thresholds table` comment.

**Why three slices for Slice D, not one:**

The three pipelines share tools but have distinct shapes (read-only vs writing closing entries; full-period vs VAT-only). Authoring them in parallel risks YAML drift; author `period_close.yaml` first as the reference, then peel off the smaller `vat_return.yaml` and the writing-variant `year_end_close.yaml`.

**Audit-trace continuity:**

Every report row references the underlying `journal_lines.id` set in its `evidence_ids` field; the frontend `TraceDrawer` resolves these to existing `decision_traces`. This means **the audit trail in the new reports is the same audit trail as the existing ledger** — no duplicate provenance, no new audit table needed beyond `period_reports`.

**Why no `report_events` table:**

The reference doc 03 suggested a separate `report_events` append-only event stream. For Phase 3, the existing `pipeline_events` table (orchestration.db) already captures every node's `started`/`completed`/`failed`, and the executor populates it for free. A separate `report_events` table is duplicative; defer it.

**Confidence:** **8.5/10** for one-pass success. The risks are: (1) the registries.py path being slightly wrong for one tool import (easy fix on re-run); (2) the `provisional_retained_earnings` synthesis in balance-sheet potentially conflicting with PRD wording on net-income placement (verify against §6.3 before implementing); (3) VAT rounding edge cases on uneven rates (well-covered by tests). Slice E (frontend) is mechanical given the existing patterns.
