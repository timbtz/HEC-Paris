# Backend gap plan — what the Lovable frontend needs that we haven't shipped

> Companion to `Orchestration/lovable/lovable-prompt.md`.
> Status as of 2026-04-25 against Phases 1–3 (live ledger, document
> upload, six SQL reports, three agentic reporting pipelines, dashboard
> SSE, per-run SSE, journal-entry trace endpoint).

The Lovable prompt was deliberately written so every screen *can* be
built today by mocking the missing bits with local fixtures. That
keeps the design loop fast and keeps Lovable from inventing JSON
shapes we don't want to inherit. This plan lists, per missing
capability:

- **What the frontend needs** — the smallest contract that unblocks
  the screen
- **Backend status today** — what's already in the schema or code
- **Proposed endpoint(s)** — method, path, params, response shape
- **Pipeline / migration / dependency work** — anything beyond the
  endpoint
- **Effort** — XS / S / M / L / XL, where XS is hours, XL is weeks
- **Priority** — P0 (blocks demo), P1 (Phase 4.A), P2 (Phase 4.B+),
  P3 (nice-to-have)

The order below is roughly priority-descending.

---

## 1. Pipeline catalog & run list (P0 — unblocks `/runs` and `/runs/:id`)

**Frontend needs**

- A list of recent pipeline runs to populate `/runs`.
- The pipeline topology (nodes + `depends_on`) so react-flow can
  render the DAG before the run starts emitting node events.
- The catalog of pipelines so the "Run pipeline" button on the
  Reports tab and the Today dashboard knows what's available.

**Status today**

- `pipeline_runs` and `pipeline_events` tables are populated; only
  reachable through `GET /runs/{id}` (single run) and the SSE stream.
  No list endpoint, no catalog endpoint.
- Pipeline YAMLs live in `backend/orchestration/pipelines/*.yaml` and
  are loaded into the registry at boot. The registry is in-process;
  the frontend can't introspect it.

**Proposed endpoints**

```
GET /runs?limit=50&offset=0&pipeline_name=...&status=...&from=...&to=...
→ {
    "items": [
      {
        "id": 412,
        "pipeline_name": "transaction_booked",
        "pipeline_version": 3,
        "trigger_source": "swan.webhook",
        "employee_id_logical": 3,
        "status": "success",
        "error": null,
        "started_at": "2026-04-25T08:14:31Z",
        "completed_at": "2026-04-25T08:14:34Z",
        "elapsed_ms": 3120,
        "total_cost_micro_usd": 4310,
        "review_count": 0
      }
    ],
    "total": 1842,
    "limit": 50,
    "offset": 0
  }

GET /pipelines
→ {
    "items": [
      {
        "name": "transaction_booked",
        "version": 3,
        "kind": "event",                 // event | manual
        "trigger": "swan.Transaction.Booked",
        "node_count": 12,
        "default_cost_budget_micro_usd": 5000
      }
    ]
  }

GET /pipelines/{name}
→ {
    "name": "transaction_booked",
    "version": 3,
    "nodes": [
      { "id": "swan-query", "kind": "tool", "depends_on": [] },
      { "id": "counterparty-resolver", "kind": "tool", "depends_on": ["swan-query"] },
      { "id": "ai-counterparty-fallback", "kind": "agent", "depends_on": ["counterparty-resolver"], "when": "needs_ai_counterparty" },
      ...
    ]
  }
```

**Implementation**

- `GET /runs` — single SQL query against `orchestration.pipeline_runs`
  joined to `audit.agent_costs` (sum micro-USD by `run_id_logical`) and
  `accounting.review_queue` (count where `entry_id` belongs to a JE
  with `source_run_id = run.id`). Add an index on
  `pipeline_runs.started_at` if not present.
- `GET /pipelines` — read the in-memory `pipeline_registry`, project to
  the catalog shape. Add a `kind` field by inspecting whether the
  pipeline is referenced from `routing.yaml` (event) or only by the
  manual run path (manual).
- `GET /pipelines/{name}` — already loaded YAML; project nodes +
  depends_on + `kind` + `when` clauses.

**Effort:** S. **Migration:** none.

---

## 2. Period reports list & artifact endpoints (P0 — unblocks Reports tab agentic cards)

**Frontend needs**

- A way to list past `period_close` / `vat_return` / `year_end_close`
  outputs without re-running the pipeline.
- A way to fetch the rendered markdown / PDF / CSV blob for a given
  period_report row.

**Status today**

- The `accounting.period_reports` table exists (Phase 3 migration
  0009) and is populated by the `report_renderer` tool at the end of
  every agentic reporting pipeline. No HTTP exposure.
- `blob_path` and `payload_json` are both stored on the row.

**Proposed endpoints**

```
GET /period_reports?period_code=2026-Q1&type=period_close&status=...
→ {
    "items": [
      {
        "id": 7,
        "period_code": "2026-Q1",
        "report_type": "period_close",
        "status": "final",
        "confidence": 0.91,
        "source_run_id": 412,
        "blob_path": "data/blobs/period_close_2026Q1.md",
        "payload_json": { "headline": "...", "anomalies": [...] },
        "created_at": "...",
        "approved_at": null,
        "approved_by": null
      }
    ]
  }

GET /period_reports/{id}/artifact?format=md|pdf|csv
→ raw bytes (Content-Type: text/markdown / application/pdf / text/csv)

POST /period_reports/{id}/approve
body: { approver_id: number }
→ { id, approved_at, approved_by, status: "final" }
```

**Implementation**

- Trivial reads against `accounting.period_reports`.
- `format=pdf|csv` requires the renderer to actually emit PDF/CSV
  variants. Today `report_renderer` writes `.md` only. To unblock the
  frontend, ship `format=md` first and accept a 415 for the others
  with a "coming soon" detail string. Add WeasyPrint integration in a
  follow-up.
- Approve route mirrors `/review/{entry_id}/approve`.

**Effort:** S for read/list, M for full PDF/CSV rendering.
**Migration:** none. The table already has the columns.

---

## 3. Employees list (P0 — unblocks employee chips, AI spend pivots, Budgets matrix)

**Frontend needs**

- A directory of employees with id, full_name, email, department,
  manager, swan_iban, active. Used by:
  - Employee dropdown filter on `/ledger` and `/budgets`
  - Per-employee column in the Budgets matrix
  - Avatar / chip on every ledger row
  - Pivot rows on `/ai-spend?by=employee`

**Status today**

- The `audit.employees` table exists with columns `id, email,
  full_name, swan_iban, swan_account_id, manager_employee_id,
  department, active`. Seeded with three demo rows (Tim, Marie, Paul).
  No HTTP exposure.

**Proposed endpoints**

```
GET /employees?active=true
→ {
    "items": [
      {
        "id": 1,
        "email": "tim@acme.eu",
        "full_name": "Tim Schmid",
        "swan_iban": "FR76...",
        "swan_account_id": "acc_...",
        "manager_employee_id": null,
        "department": "Engineering",
        "active": true
      }
    ]
  }

GET /employees/{id}
→ single row + envelope summary (current month) + 30-day spend total
```

**Implementation**

- Plain SQL against `audit.employees`. Extend the detail variant with
  envelope summary and an `agent_costs` aggregate per
  `employee_id`. Make the `audit.db` connection read-accessible from
  `backend/api/employees.py` (already accessible — the
  `/journal_entries/{id}/trace` endpoint joins across DBs).
- Add a thin write path later (`POST /employees` for onboarding step
  6); not needed for MVP since the seed is enough.

**Effort:** XS. **Migration:** none.

---

## 4. AI-spend drilldown (P1 — unblocks `/ai-spend`)

**Frontend needs**

Four pivot views (employee / model / pipeline / api_key) over a
selectable period, plus a 90-day timeseries per pivot value.

**Status today**

- The `audit.agent_costs` table holds the raw rows: `decision_id,
  employee_id, provider, model, input_tokens, output_tokens,
  cache_read_tokens, cache_write_tokens, reasoning_tokens,
  cost_micro_usd, created_at`.
- The schema does not currently store an API key id; it stores
  `provider` only. To pivot by API key, a new `api_key_id` column
  needs to be added (or hashed) — see migration note below.
- `agent_decisions.run_id_logical` lets us pivot by pipeline by
  joining to `pipeline_runs.pipeline_name`.

**Proposed endpoints**

```
GET /ai-spend?by=employee|model|pipeline|api_key&period=2026-04
→ {
    "period": "2026-04",
    "by": "employee",
    "rows": [
      {
        "key": "3",                       // employee_id (or model name, etc.)
        "label": "Marie Dupont",          // human label
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
    "total_cost_micro_usd": 9_840_000,
    "total_calls": 14_200
  }

GET /ai-spend/timeseries?by=employee&key=3&from=2026-01-01&to=2026-04-30
→ {
    "by": "employee", "key": "3",
    "buckets": [
      { "date": "2026-04-01", "cost_micro_usd": 142_000, "calls": 64 },
      ...
    ]
  }
```

**Implementation**

- All pivots are SQL aggregates over `audit.agent_costs` joined to
  `audit.agent_decisions` (for `node_id` / `run_id_logical`) and
  `accounting.audit.employees` (for label) and `pipeline_runs` (for
  pipeline name). `p95_latency_ms` requires `agent_decisions.latency_ms`
  — already present.
- Pivot by API key needs schema: add `api_key_hint` or `api_key_id` to
  `agent_costs` (a stable hash or the last 4 of the key, never the raw
  key). Migration `audit/0004_agent_costs_api_key.py`.
- Index `agent_costs(created_at, employee_id, model, provider)` to
  keep the monthly pivots fast.

**Effort:** M. **Migration:** one new column on `agent_costs` for the
api_key pivot only — pivots by employee/model/pipeline ship without
schema change.

---

## 5. Wiki layer (P1 — unblocks `/wiki` and the agent prompt-citation
contract)

**Frontend needs**

- CRUD on markdown pages with frontmatter
- Revision history with diff
- A way to know which agents read each page

**Status today**

- Nothing shipped. The `wiki/` directory referenced in the PRD does
  not exist on disk yet.
- `prompt_hash.py` does not incorporate any wiki revision id.
- The agents (`counterparty_classifier`, `gl_account_classifier_agent`,
  `document_extractor`, `anomaly_flag_agent`) do not yet call a
  `wiki_reader` tool.

**Proposed endpoints**

```
GET /wiki/pages
→ { "items": [ { "path": "policies/fr-bewirtung.md", "title": "...", "revision": 7, "last_audited_at": "2026-04-12", "agent_input_for": ["gl_account_classifier_agent"] } ] }

GET /wiki/pages/{path:path}
→ { "path", "frontmatter": {...}, "body": "<md>", "revision": 7, "updated_at": "..." }

PUT /wiki/pages/{path:path}
body: { "frontmatter": {...}, "body": "<md>", "author": "marie@acme.eu" }
→ { "path", "revision": 8 }

GET /wiki/pages/{path:path}/revisions
→ { "items": [ { "revision": 7, "author", "created_at", "diff_summary" } ] }

POST /wiki/lint
→ { "issues": [ { "path", "kind": "stale|orphan|contradiction|missing_frontmatter", "detail" } ] }
```

**Implementation**

This is its own mini-project — see the deferred research items in
`PRD-AutonomousCFO.md` §D. Smallest viable cut for the MVP:

- New module `backend/orchestration/wiki/{loader,writer,schema}.py`
- New migrations:
  - `orchestration/0002_wiki_pages.py` — `id, path UNIQUE,
    current_revision_id, agent_input_for_json, created_at, updated_at`
  - `orchestration/0003_wiki_revisions.py` — `id, page_id FK, revision
    INT, frontmatter_json, body, author, created_at`
- New tool `wiki_reader` registered in
  `backend/orchestration/tools/__init__.py` with input
  `{ tags: string[] }` returning a list of matched page bodies
- Patch `prompt_hash.py` to incorporate `(page_id, revision_id)` for
  every wiki page injected into the prompt
- Patch the four reasoning agents to call `wiki_reader` for the tags
  relevant to their decision
- Cache invalidation: a wiki edit must invalidate downstream
  `node_cache` rows for any node whose prompt referenced that page.
  Track this via a new `cache_dependencies(cache_key, wiki_page_id,
  wiki_revision_id)` association table.

**Effort:** XL. **Migration:** two new tables + a new association
table for cache invalidation.

---

## 6. Campaigns (P1 — unblocks `/budgets` Campaigns tab)

**Frontend needs**

- Plain-English goal → structured `EnvelopeDelta[]` proposal
- Preview of the per-employee impact of a proposed plan
- Atomic commit (one `write_tx`) of a plan that rewrites
  `budget_envelopes.cap_cents` and inserts a campaign-history row

**Status today**

- Nothing shipped.

**Proposed endpoints**

```
POST /campaigns/draft
body: { "goal": "Save €15K by Q3 — don't touch JobRad",
        "horizon_months": 5 }
→ { "run_id": 9123,
    "plan": {
      "deltas": [
        { "envelope": "benefit.usc", "scope": "all_employees",
          "current_cap_cents": 5000, "proposed_cap_cents": 4000,
          "monthly_savings_cents": 120_000 }
      ],
      "estimated_total_savings_cents": 3_350_000,
      "horizon_months": 5,
      "wiki_citations": ["policies/campaign-bounds.md@rev3"],
      "agent_decision_id": "dec_..."
    }
  }

POST /campaigns/preview
body: { "plan": {...} }
→ {
    "impacted_employees": [
      { "employee_id": 3, "category": "benefit.usc", "delta_cents": -1000 }
    ],
    "violates_bounds": [],
    "wiki_citations": [...]
  }

POST /campaigns/commit
body: { "plan": {...}, "approver_id": 1 }
→ { "campaign_id": 7, "committed_at": "...", "deltas_applied": 12 }

GET /campaigns?period=2026-Q3
→ { "items": [ { "id", "goal", "status", "deltas", "agent_decision_id", "committed_at" } ] }

POST /campaigns/{id}/revert
body: { "approver_id": 1 }
→ { "campaign_id", "reverted_at" }
```

**Implementation**

- New table `accounting.campaigns` (id, goal, horizon_months,
  proposed_plan_json, committed_plan_json, status, agent_decision_id,
  committed_at, committed_by, reverted_at, reverted_by).
- New pipeline `campaign_propose.yaml` invoking a new
  `campaign_proposer` agent. Reads `wiki/policies/campaign-bounds.md`.
- New tool `campaign_executor` — applies a `CampaignPlan` under one
  `write_tx`, updating every affected `budget_envelopes` row and
  inserting the campaign row. CI grep should require this tool to be
  the only writer to `budget_envelopes.cap_cents` outside migrations.
- Hard bounds enforced inside `campaign_executor` (statutory minimums,
  no headcount changes, monthly delta ≤ X% of last month's OpEx).
- Revert writes inverse deltas under the same tool.

**Effort:** L. **Migration:** one new table.

---

## 7. Onboarding (P1 — unblocks `/onboarding`)

**Frontend needs**

- Persist 8 steps' worth of structured answers
- Trigger the `onboarding_to_wiki` pipeline at finalize
- Seed the chart of accounts based on the user's choice
- Test a single Swan webhook end-to-end on step 5
- Replay one historical Swan transaction at the end (the "fixture
  pipeline run")

**Status today**

- Nothing shipped.

**Proposed endpoints**

```
POST /onboarding/start
→ { "tenant_id": "...", "session_id": "...", "step": 1 }

GET /onboarding/state
→ { "session_id", "step", "answers": {...}, "completed": false }

POST /onboarding/answer/{step}
body: structured per-step payload
→ { "step", "next_step", "validation": "ok" | { "errors": [...] } }

POST /onboarding/finalize
→ { "wiki_pages_created": 8, "fixture_run_id": 412 }

GET /onboarding/coa-presets
→ { "items": [ { "id": "fr_pcg", "name": "FR PCG", "account_count": 412 }, { "id": "de_skr03", ... }, { "id": "de_skr04", ... } ] }
```

**Implementation**

- New migration `audit/0004_onboarding_answers.py` — `(session_id,
  step, answers_json, completed_at)`.
- Seed CoA presets as YAML fixtures under
  `backend/orchestration/seeds/coa/{fr_pcg,de_skr03,de_skr04}.yaml`.
- New pipeline `onboarding_to_wiki.yaml` — runs after finalize, drafts
  8 markdown pages from the answers, persists them via the wiki layer
  from §5.
- `POST /onboarding/finalize` runs the pipeline, inserts the chosen
  CoA, and seeds the budget envelopes for default employees. It also
  triggers a one-shot replay of a fixture Swan transaction so the user
  sees the DAG visualizer once before any production data flows.

**Effort:** L. Couples directly to §5.

---

## 8. Review queue endpoint (P2 — improves `/review`)

**Frontend needs**

- Right now `/review` works by filtering `/journal_entries?status=review`.
- That misses entries flagged via the `review_queue` table that aren't
  bound to a JE (e.g. validation failures that prevented posting).

**Status today**

- `accounting.review_queue` table exists with `(id, entry_id, kind,
  confidence, reason, created_at, resolved_at, resolved_by)`. Not
  exposed.

**Proposed endpoint**

```
GET /review_queue?resolved=false&kind=...
→ {
    "items": [
      {
        "id": 18,
        "entry_id": null,
        "kind": "totals_mismatch",
        "confidence": 0.42,
        "reason": "items sum (€419) ≠ subtotal (€420)",
        "created_at": "...",
        "resolved_at": null,
        "resolved_by": null
      }
    ]
  }

POST /review_queue/{id}/resolve
body: { "resolved_by": 1, "resolution": "manual_post" | "discard" }
→ { ... }
```

**Effort:** XS. **Migration:** none.

---

## 9. Per-node SSE event upgrade (P1 — improves DAG viewer)

**Frontend needs**

For react-flow nodes to render with full agent context, the SSE stream
should emit, per node:

- The agent decision (model, prompt_hash, finish_reason, latency)
- The recorded cost (tokens, cost_micro_usd)
- Wiki citations consumed (page_id, revision_id) — depends on §5
- Any tool I/O preview (truncated)

**Status today**

- Per the orchestration map: events emitted today are
  `pipeline_started, node_started, node_completed (with elapsed_ms),
  node_skipped, node_failed, cache_hit, pipeline_completed,
  pipeline_failed`, plus tool-emitted custom events
  (`envelope.decremented`, `review.enqueued`,
  `ledger.entry_posted`, `report.rendered`).
- Agent decisions and costs are written to `audit.db` but **not**
  re-broadcast on the run SSE channel. Today the frontend has to
  re-fetch `GET /runs/{id}` after the run ends to populate the
  inspector pane.

**Proposed events to add**

```
agent.decision  { run_id, node_id, decision_id, runner, model, prompt_hash,
                  confidence, finish_reason, latency_ms }
cost.recorded   { run_id, node_id, decision_id, employee_id, provider,
                  model, input_tokens, output_tokens, cache_read_tokens,
                  cache_write_tokens, reasoning_tokens, cost_micro_usd }
wiki.cited      { run_id, node_id, page_id, revision }   // depends on §5
tool.io         { run_id, node_id, args_preview, result_preview }    // truncated to 1KB each
```

**Implementation**

- Patch `executor.py`'s per-node wrapper to publish these on the same
  event bus channel that already feeds the run SSE. Insert publish
  calls after the existing `audit.write_*` calls. Don't change the
  wire format — same `data: {JSON}\n\n` envelope.
- Bump `payload_version` so old consumers know to ignore unknown event
  types. The existing dashboard SSE consumer should not regress.

**Effort:** S (S+ if wiki-citation events depend on §5).

---

## 10. Document blob serving (P1 — needed for the trace drawer PDF preview)

**Frontend needs**

- The trace drawer wants to render the original PDF of an uploaded
  invoice inline.

**Status today**

- `documents.blob_path` points at `data/blobs/<sha256>` on the local
  filesystem. No HTTP route exposes it.

**Proposed endpoint**

```
GET /documents/{document_id}/blob
→ raw bytes (Content-Type from sniff or from documents.kind)

GET /documents/{document_id}
→ document row + line items
```

**Security note**

- Add a strict allow-list of MIME types served (PDF, PNG, JPEG only).
- Set `Content-Disposition: inline; filename="<original>"` so the
  browser previews the PDF.
- Do **not** allow path traversal on `blob_path`; serve only files
  under `data/blobs/` whose name matches the row's `sha256`.

**Effort:** XS. **Migration:** none.

---

## 11. Dashboard SSE event coverage gap (P0 quick fix)

**Frontend needs**

The `/dashboard/stream` is documented as long-lived, but the prompt
relies on it pushing five event families:

- `ledger.entry_posted` — emitted ✅
- `envelope.decremented` — emitted ✅
- `review.enqueued` — emitted ✅
- `report.rendered` — emitted ✅ (Phase 3)
- `pipeline_started` / `pipeline_completed` / `pipeline_failed` —
  re-broadcast from the per-run channel?

**Status today**

The dashboard agent didn't confirm that pipeline lifecycle events
re-broadcast on the dashboard channel. Verify in
`backend/orchestration/event_bus.py` that the dashboard subscriber
also receives `pipeline_*` events. If not, add a one-line fan-out so
the Today dashboard's "recent runs" card can update without polling.

**Effort:** XS — verify, then a one-line patch if needed.

---

## 12. CSV / Excel export of every report (P2)

**Frontend needs**

Each report card on `/reports` has an `[Export CSV]` action.

**Proposed endpoints**

Add a `format=csv|json` query param to every `/reports/*` endpoint.
Default `json`. CSV uses `;` separators (German Excel compatibility),
UTF-8 BOM, ISO-8601 dates, integer cents (no commas).

**Effort:** S. No new routes — single helper plus a flag on each
existing handler.

---

## 13. Authentication and "View as auditor" (P2 — needed for design partners)

**Frontend needs**

- A signed-in user identity (id, email, name, role).
- A read-only auditor mode that disables every mutating button.

**Status today**

- All endpoints are public on the local network. No auth header
  enforced beyond webhook secrets.

**Proposed**

- Phase 4.D security pass — Google / Microsoft OAuth, WebAuthn-gated
  mutations over €2,500 (campaign commits, year-end close), role
  column on `audit.employees` (`cfo | auditor | engineer | employee`).
- For now, the frontend reads `localStorage.user = { id: 1, role:
  "cfo" }` and the backend trusts an `X-User-Id` dev header.

**Effort:** M for the dev-header stub, L+ for production OAuth +
WebAuthn.

---

## Sequencing

A pragmatic order to land this work:

1. **Week 1** — §1 (run list + catalog), §2 (period_reports list +
   md artifact), §3 (employees), §11 (dashboard SSE verify), §10
   (document blob). All XS / S. Unblocks 80% of the Lovable mockable
   frontend with real data.
2. **Week 2** — §4 (AI spend pivots + timeseries), §8 (review queue),
   §9 (per-node SSE upgrades), §12 (CSV export). All S / M.
3. **Weeks 3-4** — §5 (wiki layer + agent integration). XL.
4. **Weeks 5-6** — §7 (onboarding) + §6 (campaigns), in parallel. L
   each.
5. **Week 7** — §13 (auth + auditor mode), polish, security checklist.

**Demo readiness:** after Week 1, the Lovable frontend is fully wired
for the Today / Ledger / Review / Reports / Runs / Budgets-Envelopes
routes against real backend data. AI Spend / Wiki / Campaigns /
Onboarding remain mocked for the Friday demo, with the mock chips
visible only in dev. Replace the mocks one by one as Weeks 2-6 land.

---

## What this plan deliberately avoids

- **No multi-currency.** Single-EUR until Phase 5.
- **No multi-tenancy.** One DB triple per tenant, separate uvicorn
  instance.
- **No new agent runtimes.** Anthropic only. The `pydantic_ai_runner`
  and `adk_runner` stubs stay stubbed.
- **No e-invoicing PDP / ELSTER submission.** We file-format-validate;
  submission stays with the customer's PDP partner.
- **No replay UI.** The "[Replay run]" button on the run page renders
  but is wired to a "coming soon" tooltip until the executor learns to
  re-run a node graph against an alternate wiki revision.

---

## Open questions for the implementing agent

1. The PRD says wiki revisions belong in `orchestration.db` to
   preserve the 3-DB invariant; some of us argue for a fourth `wiki.db`
   for clean separation. Decide before building §5.
2. `prompt_hash` extension — does it hash full page bodies or only
   `(page_id, revision_id)`? Determinism vs. byte size trade-off.
3. Cache invalidation granularity — per-node-cache-row dependencies
   on `(wiki_page_id, revision_id)` add an N×M association table that
   could grow fast. Investigate eviction strategy as part of §5.
4. CA3 / UStVA exact column → GL-account mapping for the CSV export
   in §2 — this is genuine accounting research, not just a coding
   task.
5. Campaign double-entry semantics (§6) — does "move €5k from dinners
   to a CNC reserve" warrant a transfer JE, or is it a pure budgeting
   metadata change? Reason from PCG / HGB principles before building.
