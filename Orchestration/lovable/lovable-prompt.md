# Lovable prompt — Fingent (the Autonomous CFO)

> Paste the section below into Lovable as the project brief. It is a single
> self-contained prompt. Everything outside the `===PROMPT BEGINS===` /
> `===PROMPT ENDS===` markers is editorial scaffolding for our team.

The prompt is grounded in the **live FastAPI backend** described in
`CLAUDE.md` (Phases 1–3 shipped: live ledger, document upload, six SQL
reports, three agentic reporting pipelines, dashboard SSE, per-run SSE,
journal-entry trace endpoint). All endpoints and JSON shapes below are
real and copy-pasted from the backend source. Screens that depend on
endpoints we **haven't shipped yet** are explicitly flagged
`status: "mocked — backend gap"` so Lovable wires the UI but reads from a
local JSON fixture; the gap plan in
`Orchestration/lovable/backend-gap-plan.md` lists every missing endpoint
that needs to land before the mock is replaced with a live wire.

---

===PROMPT BEGINS===

# Fingent — the Autonomous CFO

Build the customer-facing web app for **Fingent**, a finance-software
product for the founder-CFO of a 100-person Franco-German B2B scale-up.
The brand promise is one sentence: *the last piece of finance software
this CFO will ever need.* Every euro on every report is one click away
from the agent that booked it, the rule it cited, the document it read,
and the API token that paid for it.

You are building a polished, opinionated, single-tenant web app — not a
landing page, not a marketing site, not a demo. The product surface has
nine routes. Treat this brief as a contract: hit every endpoint, every
empty state, every loading state, every event type listed below. Where
data is missing, render a tasteful empty-state with a one-sentence
explainer — never a stack trace, never a JSON dump.

## 0. Stack & non-negotiables

- **Vite + React 18 + TypeScript (strict)**
- **Tailwind v4** (no UI libraries — no shadcn, no Radix; build the
  primitives yourself, kept minimal)
- **Zustand 5** for client state
- **Motion** (ex-Framer-Motion) for transitions — used sparingly
- **react-flow** for the DAG visualization on the Run page
- **CodeMirror 6** for the markdown wiki editor
- **`recharts`** is allowed for charts; do not pull in any other chart lib
- Native `EventSource` for SSE (browser auto-reconnects with backoff)
- `fetch` only — no axios, no react-query, no swr (rolling our own thin
  hooks keeps the surface small)

Performance bar: every screen interactive in <1s on a cached load. No
client-side bundle over 250KB gzipped without a deliberate reason.

## 1. Brand & visual language

- **Mood:** *quiet authority*. Think Linear, Mercury, Stripe Dashboard,
  Pennylane — never Notion, never Airtable, never Plaid-the-toy.
- **Palette:**
  - Surface: `#FAFAF9` (light) / `#0B0B0C` (dark)
  - Card: `#FFFFFF` / `#141416`
  - Hairline: `#E7E5E4` / `#27272A`
  - Ink: `#0C0A09` / `#FAFAF9`
  - Muted ink: `#52525B`
  - Accent (single): `#1B5E20` — a deep, confident green used only for
    money-positive figures, primary buttons, and the live-pulse dot.
    Never red+green together for accounting deltas — use ink + accent.
  - Warning amber: `#B45309` — used for review-queue badges and
    soft-threshold warnings only.
  - Failure: `#B91C1C` — only on actual error states.
- **Typography:**
  - UI: `Inter`, weights 400/500/600.
  - Tabular figures everywhere money is displayed
    (`font-feature-settings: "tnum"`).
  - Code/IDs: `JetBrains Mono` 13px.
- **Density:** compact but breathing. 12px base spacing rhythm, 14px
  base font, 13px secondary, 11px metadata. Tables use 36px row height.
- **Light & dark mode** both first-class. Default to system. Toggle in
  the user menu.
- **Radii:** 8px on cards, 6px on inputs/buttons, 4px on badges. Never
  fully rounded buttons — this is finance software.
- **Shadows:** flat. One soft shadow on modals only.
  `shadow: 0 1px 2px rgb(0 0 0 / 0.04)` on hovered rows; that is it.
- **Motion:** ≤120ms ease-out for hover/focus, ≤220ms for route
  transitions. Reduce-motion respected. No bouncy springs anywhere.

## 2. Information architecture

Top-level layout: **left sidebar** (persistent), **top breadcrumb +
period selector + user menu**, **main canvas**.

Sidebar groups & routes (in order):

1. **Today** — `/` — the live operating dashboard
2. **Ledger** — `/ledger` — every journal entry, drillable
3. **Review** — `/review` — entries that the agents flagged for human
4. **Runs** — `/runs` — every pipeline execution, with the live DAG
5. **Reports** — `/reports` — six SQL reports + three agentic reports
6. **Budgets** — `/budgets` — per-employee envelopes & campaigns
7. **AI spend** — `/ai-spend` — Anthropic / OpenAI cost drilldown
8. **Wiki** — `/wiki` — the living rule corpus the agents read
9. **Onboarding** — `/onboarding` — 8-step CFO setup wizard (only
   visible until completed; afterwards demoted to a Settings entry)

Plus a global **trace drawer** (right-hand slide-out, 480px) reachable
from any number on any report or any row in the ledger.

The user menu (top-right) holds: tenant identity, light/dark, "View as
auditor" mode (read-only badge), sign out.

## 3. Backend contract (READ FIRST — this is the source of truth)

### Base URL

Read from `import.meta.env.VITE_API_BASE_URL`. In dev, default to
`http://localhost:8000`. In production it is the same origin, no proxy.

### Conventions, every response

- **All field names are `snake_case`** (we never reformat — the backend
  returns SQLite column names directly). Do not camelCase anything.
- **Money is integer cents** (`amount_cents`, `cap_cents`, etc.). Single
  currency: EUR. Never use floats. Render as `€1,234.56` with thin
  spaces and tabular figures.
- **Cost tokens** are `cost_micro_usd` — integer micro-USD. Render as
  `$0.0034` in tables; `$3.21k` style only on aggregate cards.
- **Timestamps** are ISO-8601 UTC strings. Render with the user's
  locale and an explicit timezone abbreviation in tooltips.
- **Errors** are `4xx` with `{ "detail": "..." }`. Surface the `detail`
  string in a small inline banner; never raw-dump.

### Endpoints (the ones that are LIVE today)

| Method | Path | Purpose |
|---|---|---|
| GET | `/healthz` | `{ status: "ok" }` — used by the connection dot |
| POST | `/swan/webhook` | `x-swan-secret` header; not called from UI |
| POST | `/external/webhook/{provider}` | not called from UI |
| POST | `/documents/upload` | multipart `file` (PDF) + optional `employee_id` → `{ document_id, sha256, run_id, stream_url }` |
| POST | `/pipelines/run/{name}` | body `{ trigger_payload?: object, employee_id?: number }` → `{ run_id, stream_url }` |
| GET | `/runs/{run_id}` | → `{ run, events, agent_decisions }` (full reconstruction; see schema below) |
| GET | `/runs/{run_id}/stream` | SSE; closes on terminal event |
| GET | `/journal_entries/{entry_id}/trace` | → entry + lines + traces + agent_decisions + agent_costs + source_run + swan_transactions + documents |
| POST | `/review/{entry_id}/approve` | body `{ approver_id: number }` → `{ entry_id, approver_id, status: "approved" }` |
| GET | `/journal_entries?limit&offset&status` | paginated; default `limit=50` (max 200), reverse chronological. Each item carries `total_cents` and `line_count`. |
| GET | `/envelopes?employee_id&period&scope_kind` | per-envelope `{ id, scope_kind, scope_id, category, period (YYYY-MM), cap_cents, soft_threshold_pct, used_cents, allocation_count }` |
| GET | `/dashboard/stream` | long-lived SSE — open it once on app boot |
| GET | `/reports/trial_balance?as_of=YYYY-MM-DD&basis=accrual\|cash` | see §5 |
| GET | `/reports/balance_sheet?as_of=YYYY-MM-DD&basis=...` | see §5 |
| GET | `/reports/income_statement?from&to&basis=...` | see §5 |
| GET | `/reports/cashflow?from&to` | direct method; sections operating/investing/financing |
| GET | `/reports/budget_vs_actuals?period=YYYY-MM&employee_id?&category?` | see §5 |
| GET | `/reports/vat_return?period=YYYY-MM` | output 445 vs input 4456 |

### Endpoints we still need (mock for now)

The following routes are **not yet wired** in the backend. Build the UI
exactly as if they exist, but read from a local `mocks/*.json` fixture
and surface a small `mocked` chip in dev. Do not invent JSON shapes
that don't exist; mirror the shapes I give you in §6, §7, §8.

- `GET /runs?limit&offset&pipeline_name&status` — list runs
- `GET /pipelines` — catalog of available pipelines (id, version, node count)
- `GET /pipelines/{name}` — DAG topology (nodes, depends_on, kind)
- `GET /employees` — employee directory
- `GET /period_reports?period_code&type` — already-rendered agentic reports
- `GET /period_reports/{id}/artifact` — markdown / PDF / CSV blob
- `GET /ai-spend?by=employee\|model\|pipeline\|key&period=YYYY-MM` — spend pivots
- `GET /ai-spend/timeseries?by=...&from&to`
- `GET /wiki/pages` and `GET|PUT /wiki/pages/{path}` and `/revisions`
- `POST /campaigns/draft|preview|commit` and `GET /campaigns`
- `POST /onboarding/start|answer/{step}|finalize` and `GET /onboarding/state`

### SSE event format

Every SSE message is `data: {...JSON...}\n\n`. Heartbeats are
`: heartbeat\n\n` with no `data:` prefix — silently ignore them.

Every event payload carries an `event_type` field. The events you must
handle:

- `pipeline_started` — `{ event_type, run_id, pipeline_name, version }`
- `node_started` — `{ event_type, run_id, node_id }`
- `node_completed` — `{ event_type, run_id, node_id, elapsed_ms, output? }`
- `node_skipped` — `{ event_type, run_id, node_id, reason }`
- `node_failed` — `{ event_type, run_id, node_id, error }`
- `cache_hit` — `{ event_type, run_id, node_id, cache_key }`
- `pipeline_completed` — `{ event_type, run_id }`
- `pipeline_failed` — `{ event_type, run_id, error, traceback? }`
- `ledger.entry_posted` — `{ event_type, entry_id, approver_id?, approved_at? }`
- `envelope.decremented` — `{ event_type, envelope_id, amount_cents, line_id }`
- `review.enqueued` — `{ event_type, entry_id, kind, confidence, reason }`
- `report.rendered` — `{ event_type, run_id, period_code, report_type, blob_path }`

Two streams to subscribe to:

1. **Dashboard stream** — `GET /dashboard/stream` — opened once on app
   mount; reducer routes `ledger.*`, `envelope.*`, `review.*`,
   `report.rendered` events into the global Zustand store. Pulse the
   sidebar live-dot green while connected; amber while reconnecting.
2. **Run stream** — `GET /runs/{id}/stream` — opened when a Run page is
   open or a run-progress overlay is mounted. Closes itself on
   `pipeline_completed` or `pipeline_failed` (the backend ends the
   stream — your code should also abort the EventSource on receipt).

## 4. Global components (build these once, reuse everywhere)

- `<Money cents={...} signed?={true} />` — euro formatter, tabular
  figures, optional sign, optional muted-zero.
- `<Cents cents={...} />` — same but for thousands ledgers
  (`12 345,67` style with thin spaces).
- `<MicroUsd value={...} />` — `$0.0034` if <1c, `$0.42` if <$1, `$1.23`
  otherwise. Tabular.
- `<RelTime iso={...} />` — `4s ago`, `12 min`, `2h 14min`, then full
  date.
- `<TraceLink entryId={...} />` — wrapper that opens the right-rail
  trace drawer for a given journal entry.
- `<EmptyState icon title hint cta? />` — used everywhere.
- `<LiveDot connected={...} />` — 6px circle, accent when connected,
  amber when reconnecting, ink/30 when offline.
- `<NodeBadge kind="tool"|"agent"|"condition" />` — colored chip used
  in the DAG viewer and run history.
- `<ConfidenceBar value={...} floor={0.75} />` — tiny inline bar with
  the floor as a tick mark; agent-confidence is the most-shown metric
  in this app.
- `<KeyValue rows={...} />` — for the trace drawer.

## 5. Routes — detailed specs

### 5.1 Today — `/`

A single canvas that answers *"what changed in my company since I last
looked?"*

Top half — three cards across:

1. **Live ledger pulse** — last 12 entries from
   `GET /journal_entries?limit=12`, prepended by SSE
   `ledger.entry_posted` events. Each row: time, description,
   `total_cents`, status badge, employee chip if a line carries one.
   Click → trace drawer.
2. **Envelopes burning** — top 6 envelopes by `pct_used` from
   `GET /envelopes?period=<current YYYY-MM>`, rendered as small radial
   rings with the employee's first name underneath. Use accent green if
   `pct_used < soft_threshold_pct`, amber otherwise, failure-red at
   `≥100%`.
3. **Review queue** — count + the 5 most recent items from
   `GET /journal_entries?status=review&limit=5`, with confidence bars.
   Click row → trace drawer with an Approve button.

Bottom half — two cards:

1. **AI spend today** — sum of `cost_micro_usd` for today, plus a
   sparkline of the last 14 days; tap → `/ai-spend`. (Mocked endpoint —
   fixture `mocks/ai-spend-today.json`.)
2. **Recent runs** — last 8 from `GET /runs?limit=8` (mocked) — pipeline
   name, started_at, status, elapsed_ms, total agent cost. Click →
   `/runs/{id}`.

Top of the page: a single primary action — `[+ Upload document]` — that
opens the upload modal. Submission posts `POST /documents/upload`;
on success, opens the run-progress overlay subscribed to the returned
`stream_url`.

### 5.2 Ledger — `/ledger`

A single dense table. Sticky header. Virtualized rows (no library — use
`@tanstack/react-virtual` is fine). Filters in a bar above:

- Status: `posted | review | draft | reversed | all`
- Date range
- Counterparty search (client-side filter on the loaded page)
- Employee dropdown (sourced from `/employees` mock)

Columns: `date · description · counterparty · employee · debit · credit
· status · run` — last column is a small "open run" arrow. Row click →
trace drawer.

The trace drawer shows, top-down:
- **Header** — entry id, date, basis, status, source pipeline (linkable
  to the run page).
- **Lines** — table of (account_code, account_name from CoA join,
  debit, credit, counterparty, document chip if any). Each line is
  clickable to expand its decision trace inline.
- **Decision** — for each line, the trace row: `source` chip
  (rule/agent/cache/human), `rule_id` if any, confidence bar,
  `agent_decision_id` link.
- **Agent reasoning** — when source is agent: model, prompt_hash (mono,
  first 8 chars), `finish_reason`, latency_ms, `temperature`,
  `alternatives_json` (rendered as a small accordion).
- **Cost** — token breakdown (input / output / cache_read / cache_write
  / reasoning) and the micro-USD figure. The employee_id link routes
  to `/ai-spend?employee=...`.
- **Source artefacts** — Swan transaction (with its raw payload toggle)
  and/or document blob preview (PDF iframe; fall back to a download
  link).

### 5.3 Review — `/review`

Same table as `/ledger` filtered to `status=review`, but each row has an
inline `[Approve]` button that POSTs `/review/{id}/approve` with the
current user's `employee_id`. On success, optimistically remove the
row and pulse a confirmation toast that says "posted · cited rule X" if
the trace cites a wiki page, otherwise "posted." Idempotent on the
backend, so retries are safe.

Show a per-row "why" callout next to confidence — concatenate the
trace's `reason` (from the `review_queue` row) plus the agent's
`alternatives_json` if present.

### 5.4 Runs — `/runs` and `/runs/:id`

**Index `/runs`** — paginated list from `GET /runs` (mocked). Filters:
pipeline_name, status, date range. Columns: `pipeline · started ·
duration · status · agent_cost · review_count`. The pipeline name is a
chip with the kind icon.

**Detail `/runs/:id`** — three-pane layout, splitter draggable.

- **Left (240px):** node list (from
  `GET /runs/{id}` events). Vertical list, color-coded dots
  (pending=zinc, running=blue, completed=accent, skipped=zinc/40,
  failed=failure, cached=accent/60). Click selects a node.
- **Center (fluid):** **react-flow** DAG visualization. Layout is
  Kahn-layered top-to-bottom: each layer is a horizontal row of nodes;
  edges are drawn from `depends_on`. Each node shows id, kind icon,
  status, elapsed_ms, cost (if agent). Live updates from
  `/runs/{id}/stream` — incoming `node_started` paints blue, etc.
- **Right (440px):** node inspector. Tabs: `Input · Output · Trace ·
  Cost`. For agent nodes: full prompt (collapsed), tool-use args, model
  output, decision row from `agent_decisions`. For tool nodes:
  args/result JSON pretty-printed. For condition nodes: predicate name
  + bool result.

A toolbar above the canvas: `[Replay run]` (mocked — opens a "coming
soon" tooltip), `[Open trace of resulting JEs]`, total cost in
micro-USD, total elapsed wall-clock.

### 5.5 Reports — `/reports`

Top: report-type selector (chip group, 9 chips):

> Trial balance · Balance sheet · Income statement · Cashflow ·
> Budget vs actuals · VAT return · **Period close** · **Cash forecast**
> · **Audit pack**

The first six are the **live SQL endpoints** described in §3; render
them today.
The last three are **agentic reports** — they are *mocked* until the
backend lands them (see `backend-gap-plan.md`). Render the UI for them
with the same visual language; the data comes from
`mocks/period-close.json`, etc.

Below the selector: a context-aware filter row. Each report has its
own filter shape — render only the relevant inputs:
- as_of (date) for trial_balance / balance_sheet
- from + to (date range) for income_statement / cashflow
- period (YYYY-MM) for budget_vs_actuals / vat_return / period_close
- basis toggle (cash / accrual) for trial_balance / balance_sheet /
  income_statement
- employee_id select for budget_vs_actuals
- category select for budget_vs_actuals

A `[Run pipeline]` button appears for the three agentic reports — POSTs
to `/pipelines/run/{period_close|vat_return|year_end_close}`, opens
the run-progress overlay. The result is pinned at the top of the
report list ("just generated, see it now") with a link to the run page
for full DAG view.

Render each report as a structured card with:

- **Header** — title, period/date, basis chip, `[Export CSV]` /
  `[Copy link]` actions, total at the right (`balanced ✓` badge if
  applicable).
- **Body** — section-grouped tables:
  - Trial balance: one table, totals at the bottom, `balanced` callout
    in green or failure-red.
  - Balance sheet: 2-column layout (Assets | Liab + Equity), each
    with subtotals, plus a "provisional" warning if `provisional=true`.
  - Income statement: revenue / expense sections, net-income card.
  - Cashflow: operating/investing/financing cards, opening + closing
    balance.
  - Budget vs actuals: per-envelope rows with cap, used,
    `<ConfidenceBar>`-styled bar (cap-relative), pct_used.
  - VAT return: 445/4456 split, net_due tile.
- **Every figure is a `<TraceLink>`**. Click any number → drawer opens
  filtered to the journal lines that produced that aggregate. (For the
  six SQL reports, this is computable on the client by re-querying
  `/journal_entries` filtered by date+account; render a "see entries"
  CTA in the drawer if not feasible inline.)

For the three agentic reports, additionally show:
- The run id that produced the report, with elapsed time and total
  agent cost.
- The list of anomalies surfaced by `anomaly_flag_agent`, each with
  kind chip (`vat_mismatch | balance_drift | missing_accrual |
  outlier_expense | duplicate_entry`), description, evidence,
  per-anomaly confidence bar, and a `[Open in ledger]` action that
  filters the ledger by `line_ids`.
- Wiki citations as small mono-spaced badges
  (`policies/fr-bewirtung.md@rev7`).

### 5.6 Budgets — `/budgets`

Two tabs: **Envelopes** | **Campaigns**.

**Envelopes** — a matrix: rows = employees, columns = categories,
cells = small radial rings sized to `pct_used`. Hover → mini popover
with cap / used / remaining / allocation_count. Click → drawer with
the underlying `budget_allocations` (list of journal lines that
charged the envelope; reuse trace-drawer mechanics).

Data: `GET /envelopes?period=...` for every active employee from
`/employees` (mocked). Period selector at the top (default: current
month, with prev/next chevrons).

**Campaigns** — entirely *mocked* until the backend lands.

Layout: a "what's your goal?" textarea at the top with a `[Draft]`
button that POSTs `/campaigns/draft` (mocked). Below: when a draft
exists, show the proposed envelope deltas as a side-by-side
"current → proposed" table grouped by category, with monthly savings
on the right. Two big buttons at the bottom — `[Refine]` (re-prompts
the agent, opens the goal box again) and `[Commit]` (POSTs
`/campaigns/commit` — *mocked*; on success show the committed plan
inline). History list of past campaigns underneath.

### 5.7 AI spend — `/ai-spend`

Four pivots in a tab bar: **Employee · Model · Pipeline · API key**.
Each pivot shows: a single big card with the period's total at the
top, then a horizontal stacked bar (categories), then a table.

Period selector: monthly. Range selector: last 12 months.

The data shape (mocked, see `backend-gap-plan.md`):
```json
{
  "period": "2026-04",
  "by": "employee",
  "rows": [
    {
      "employee_id": 2,
      "full_name": "Marie Dupont",
      "department": "Strategy",
      "cost_micro_usd": 4_210_000,
      "calls": 1820,
      "input_tokens": 11_200_000,
      "output_tokens": 320_000,
      "cache_read_tokens": 2_100_000,
      "p95_latency_ms": 3120,
      "top_pipeline": "transaction_booked",
      "top_model": "claude-sonnet-4-6"
    }
  ],
  "total_cost_micro_usd": 9_840_000
}
```

Below the table, when a row is selected, a sparkline timeseries (`/ai-spend/timeseries`, mocked) of the
last 90 days for that pivot value, and a "top 5 prompt-hashes" list
each linking back to one representative `agent_decisions` row (so the
user can land in the trace drawer).

### 5.8 Wiki — `/wiki`

Three-pane Obsidian-style:
- **Left (240px):** file tree. Folders `policies/`, `employees/`,
  `counterparties/`, `raw/`. Search at the top.
- **Center:** CodeMirror 6 markdown editor with frontmatter
  highlighting. The frontmatter spec has fixed keys: `applies_to[]`,
  `threshold_eur`, `jurisdictions[]`, `last_audited_by`,
  `last_audited_at`, `revision`, `agent_input_for[]`. Validate on
  blur; surface errors inline.
- **Right (320px):** revisions panel — list of revisions with author
  and timestamp. Diff view on click. Below it: "agents that read this
  page" (computed from `agent_input_for`). Click an agent → routes to
  `/runs?agent=...`.

This whole route is *mocked* until the wiki backend lands. Use
`mocks/wiki/*.md` files as the corpus and an in-memory revision log.
Lovable should render the route as if production-ready — it is the
single most novel surface in the product.

### 5.9 Onboarding — `/onboarding`

8-step wizard, each step is a single card with a clear primary action
and a quiet `Skip — I'll come back` link. Progress bar across the top
(8 dots; current step solid, completed steps with checkmark, future
steps muted).

Steps (one screen each):
1. **Identity & jurisdiction** — legal name, SIREN/HRB, primary
   country (FR/DE radio), other operating countries.
2. **Chart of accounts** — choose: FR PCG | DE SKR03 | DE SKR04 (radio
   cards). CSV import optional.
3. **Fiscal posture** — fiscal year end (date), basis (accrual / cash
   radio).
4. **VAT regime** — FR options (réel normal monthly / réel simplifié
   quarterly / franchise) | DE options (monthly / quarterly / annual).
   Show only the relevant set based on step 1.
5. **Banking & e-invoicing** — Swan OAuth connect button (mocked: pops
   a "Connect to Swan" modal with a fake redirect that returns
   success); French PDP partner select; XRechnung readiness toggle.
6. **Per-employee budget defaults** — six benefit caps (USC, Wellpass,
   JobRad bike-list, Finn category, dinners per-meal, dinners
   monthly), each a numeric input with a small policy explainer.
7. **Expense policy thresholds** — receipt-required (default €25),
   manager-approval (€500), CFO-approval (€2,500), auto-post
   confidence floor (slider, 0.50-0.99, default 0.85).
8. **Statutory partners** — expert-comptable / Steuerberater contact,
   audit firm (visible only if "≥2 of 3" thresholds met based on
   answers), payroll provider (PayFit / DATEV / Personio / Silae).

Final screen: a "ratify your wiki" page that displays the eight
auto-drafted markdown pages in a stacked list, each editable inline,
and a single `[Approve & go live]` button that POSTs
`/onboarding/finalize` (mocked).

This route is **entirely mocked** until the backend lands. Persist
state to `localStorage` between steps so the user can refresh without
losing progress. Show a one-time "fixture pipeline run" at the very
end — a fake `transaction_booked` run plays out in the run-progress
overlay so the CFO sees the DAG visualizer for the first time.

## 6. Sample JSON shapes (these are real — copy literally)

### 6.1 `GET /journal_entries`

```json
{
  "items": [
    {
      "id": 178,
      "basis": "accrual",
      "entry_date": "2026-04-25",
      "description": "JobRad lease — Paul Müller — April",
      "status": "posted",
      "source_pipeline": "transaction_booked",
      "source_run_id": 412,
      "accrual_link_id": null,
      "reversal_of_id": null,
      "created_at": "2026-04-25T08:14:33Z",
      "total_cents": 9900,
      "line_count": 2
    }
  ],
  "total": 1842,
  "limit": 50,
  "offset": 0
}
```

### 6.2 `GET /envelopes`

```json
{
  "items": [
    {
      "id": 14,
      "scope_kind": "employee",
      "scope_id": 3,
      "category": "benefit.usc",
      "period": "2026-04",
      "cap_cents": 5000,
      "soft_threshold_pct": 80,
      "used_cents": 4800,
      "allocation_count": 1
    }
  ]
}
```

### 6.3 `GET /journal_entries/{id}/trace` (abbreviated)

```json
{
  "entry": { "id": 178, "basis": "accrual", "entry_date": "2026-04-25", ... },
  "lines": [
    { "id": 412, "entry_id": 178, "account_code": "613500", "debit_cents": 9900, "credit_cents": 0, "counterparty_id": 14, "swan_transaction_id": null, "document_id": 81, "description": "JobRad April lease" },
    { "id": 413, "entry_id": 178, "account_code": "401", "debit_cents": 0, "credit_cents": 9900, ... }
  ],
  "traces": [
    { "id": 305, "line_id": 412, "source": "agent", "rule_id": null, "confidence": 0.92, "agent_decision_id_logical": "dec_2026...", "approver_id": null, "approved_at": null }
  ],
  "agent_decisions": [
    { "id": 1, "run_id_logical": 412, "node_id": "ai-account-fallback", "source": "agent", "runner": "anthropic", "model": "claude-sonnet-4-6", "response_id": "msg_01...", "prompt_hash": "a3f8c2…", "alternatives_json": "[{\"gl\":\"626700\",\"conf\":0.07}]", "confidence": 0.92, "line_id_logical": 412, "latency_ms": 1810, "finish_reason": "tool_use" }
  ],
  "agent_costs": [
    { "decision_id": 1, "employee_id": 3, "provider": "anthropic", "model": "claude-sonnet-4-6", "input_tokens": 4210, "output_tokens": 142, "cache_read_tokens": 1820, "cache_write_tokens": 0, "reasoning_tokens": 0, "cost_micro_usd": 4310 }
  ],
  "source_run": { "id": 412, "pipeline_name": "transaction_booked", "status": "success", ... },
  "swan_transactions": [],
  "documents": [ { "id": 81, "sha256": "…", "kind": "invoice_in", "amount_cents": 9900, "blob_path": "data/blobs/…" } ]
}
```

### 6.4 `GET /reports/trial_balance?as_of=2026-04-30&basis=accrual`

```json
{
  "as_of": "2026-04-30",
  "basis": "accrual",
  "currency": "EUR",
  "lines": [
    { "code": "512", "name": "Bank — Swan", "type": "asset", "total_debit_cents": 142500000, "total_credit_cents": 38400000, "balance_cents": 104100000 }
  ],
  "totals": { "total_debit_cents": 312000000, "total_credit_cents": 312000000, "balanced": true }
}
```

### 6.5 `GET /reports/budget_vs_actuals?period=2026-04`

```json
{
  "period": "2026-04",
  "currency": "EUR",
  "lines": [
    { "envelope_id": 14, "scope_kind": "employee", "scope_id": 3, "category": "benefit.usc", "cap_cents": 5000, "used_cents": 4800, "remaining_cents": 200, "pct_used": 96.0, "allocation_count": 1 }
  ],
  "totals": { "total_cap_cents": 1200000, "total_used_cents": 880000, "total_remaining_cents": 320000 }
}
```

## 7. State management

One Zustand store per concern, all in `src/store/`:

- `useDashboard()` — ledger feed (capped at 200), envelopes by key,
  reviewIds set, dashboard SSE connected boolean.
- `useRunProgress()` — active run id, nodes record, pipeline status.
  Auto-resets 4s after terminal event.
- `useReports()` — last filter set per report-type, last result cache
  (5 min).
- `useWiki()` — open page, dirty state, revision tree (mocked).
- `useOnboarding()` — step index, answers map, persisted to
  localStorage.

Hooks: `useSSE<T>(url, onEvent, onStatus?)` — small, robust, uses
EventSource, exposes `{ connected }`. Already documented; build it once,
re-use.

## 8. Empty / loading / error rules

- **Loading:** skeleton rows in tables (3 rows minimum). Skeleton
  cards for the dashboard. No spinners — finance software does not
  spin.
- **Empty (no data yet):** a tasteful card with one icon, one
  sentence, and one action. Example for `/ledger` empty: *"No journal
  entries yet. Connect a Swan account or upload an invoice to get
  started."* — `[+ Upload]` button below.
- **Error:** an inline banner above the affected component:
  > Couldn't load this. *(Error: 500)* `[Retry]`

  Never replace the whole page with an error. Never show stack
  traces. Never log the user out on a 4xx that isn't 401.

## 9. Acceptance bar

Done means:

1. Every route renders, on a fresh boot, in <1 second on a cached load,
   against an empty backend (so: no errors, only empty states).
2. Uploading a real PDF to `/documents/upload` produces:
   - the upload modal close
   - the run-progress overlay opens, reads `/runs/{id}/stream`, paints
     each node as it transitions, and closes itself 4s after
     `pipeline_completed`
   - a new row appears in `/ledger` (via SSE — no refresh)
3. Clicking that row opens the trace drawer with all six sections
   populated: lines, traces, agent reasoning, costs, source run,
   artefacts.
4. The Reports tab renders all six SQL reports against the seed
   dataset, every figure tabular-aligned, the balanced badge correctly
   reflecting the SQL response.
5. Light/dark mode parity — every screen shipped in both, no missing
   colors.
6. Reduce-motion respected: no Motion animations play if
   `prefers-reduced-motion: reduce`.
7. The mocked routes (Onboarding, Wiki, Campaigns, AI spend, Run list,
   Period reports artifact viewer) all render with the same visual
   polish as the wired routes; the only difference is a small
   `mocked` chip in the dev environment.
8. Keyboard: `cmd+k` opens a global search palette (mocked search over
   journal entries + wiki pages); `?` opens a shortcut cheat-sheet;
   `g` then `l` jumps to ledger, `g` then `r` to runs, etc.
9. No console errors, no React StrictMode warnings, no Tailwind unused
   class warnings in the build.
10. `npm run build` produces a deployable static bundle that talks to
    `VITE_API_BASE_URL` with no further config.

## 10. What we are NOT building

- ❌ Tenancy / sign-up / billing — single-tenant by definition
- ❌ Multi-currency UI — EUR only
- ❌ A mobile app — web is responsive but desktop-first
- ❌ A standalone auditor seat — the View-as-auditor mode is enough
- ❌ Browser notifications, sound effects, gamification

Build it sober, build it dense, build it in a way that an auditor
flipping through it on Monday would not roll their eyes.

===PROMPT ENDS===
