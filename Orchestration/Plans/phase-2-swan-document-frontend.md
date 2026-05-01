# Feature: Phase 2 — Swan Webhook Path, PDF Document Path, and Demo Frontend

The following plan should be complete, but it is important that you validate documentation and codebase patterns and task sanity before you start implementing.

Pay special attention to naming of existing utils, types, and models. Import from the right files etc. The Phase 1 metalayer (Phases A–C of `RealMetaPRD §12`) is already in place — this plan **plugs into** it. Do not refactor the executor, registries, cache, audit spine, or PRAGMA block; mirror the conventions they established.

## Feature Description

This is the back half of the hackathon MVP defined by `Orchestration/PRDs/RealMetaPRD.md`. Phase 1 (this project's name for `RealMetaPRD §12` Phases A+B+C) shipped the metalayer foundation: three SQLite databases, the YAML DSL parser, the layer-by-layer DAG executor, the four registries, the cross-run cache, the audit spine (`propose → checkpoint → commit`), the cost helper, the prompt-hash function, and one trivial `noop_demo.yaml` pipeline. **0 production tools, 0 production agents, 0 production conditions, 0 production pipelines, no Swan integration, no document path, no API surface beyond `/healthz`, no frontend.**

Phase 2 builds the two demo paths plus the dashboard:

1. **Swan webhook → live ledger** (`RealMetaPRD §12 Phase D`, ~6h). A Swan `Transaction.Booked` webhook arrives, signature-verified, idempotently inserted, routed to `transaction_booked.yaml`. The pipeline re-queries Swan via GraphQL, resolves counterparty (cascade with cache writeback), classifies GL account (cascade with cache writeback), builds a deterministic journal entry, gates on confidence, posts under `accounting.journal_entries`, asserts invariants, decrements the right per-employee budget envelope, and emits SSE events. A companion `transaction_released.yaml` reverses on `Released` / `Canceled`.
2. **PDF invoice → accrual entry** (`RealMetaPRD §12 Phase E`, ~3h). A drag-drop or `POST /documents/upload` arrives, SHA256-keyed for idempotency. The pipeline runs Claude vision through a strict invoice JSON tool, validates `SUM(line_items) == total`, resolves counterparty, classifies GL, builds a `basis='accrual'` entry, posts, and writes the audit trace.
3. **Frontend + dashboard SSE** (`RealMetaPRD §12 Phase F`, ~5h). Vite + React + TypeScript scaffold with: live ledger view (SSE-animated row inserts), per-employee envelope rings, drag-drop upload zone, trace drawer (line → trace → agent decision → cost), review queue, and a one-page "infrastructure" tab.
4. **Generic external webhook ingress** (`RealMetaPRD §7.2`). `POST /external/webhook/{provider}` with a verifier registry, idempotent insert, and a stub `external_event.yaml` route. Proves the pluggability claim without wiring a live third-party provider.

Cross-cutting: every `pipeline_runs` row carries `employee_id_logical` populated **at trigger time** (resolved from the IBAN/account in the Swan handler, or accepted as a form field on document upload). Counterparty → envelope category mapping is set when the resolver runs, not after. The compensation pipeline ships from day one. The §7.9 retry/timeout/idempotency policy is applied at every Anthropic call site. The §7.6 hard invariants run after every post.

> **Lesson baked in from `Orchestration/Plans/phase-1-critical-gap-remediation.md`:** Phase A–F implementers tend to build the *boxes* (schemas, tools, agents, pipelines) and leave the *arrows between boxes* loose — employee attribution at trigger time, envelope decrement gated on `posted`, counterparty→category mapping, compensation routing, §7.9 timeouts, dashboard SSE event re-emission. This Phase 2 plan wires those arrows from the start so a remediation pass is not needed.

## User Story

**As a hackathon judge** watching the demo on stage,
**I want** to see a Swan `Transaction.Booked` fire and the corresponding journal entry appear in a live React dashboard within 5 seconds, with a click-through trace down to the model name, prompt hash, alternatives, confidence, and cost; then drag an Anthropic invoice PDF onto the dashboard and watch an accrual entry post with the same audit-trail depth; then drop a `Transaction.Released` and watch the entry reverse cleanly,
**so that** I am convinced this is an autonomous-CFO product, not a chat wrapper.

**As a backend engineer** extending the system,
**I want** to add a new event type or a new provider by writing one YAML pipeline, registering one Python tool, and adding one line to `routing.yaml`,
**so that** the metalayer's "pipelines are data, not code" claim holds in practice.

## Problem Statement

After Phase 1, the project has a working pipeline runtime but cannot demo. There is no way to ingest a Swan event, no way to ingest a PDF, no way to post a journal entry, no UI. The wedge query (`RealMetaPRD §7.11`) returns zero rows because nothing produces decisions. The 5-second SLA (`RealMetaPRD §11`) is unmeasurable because no end-to-end path exists.

Furthermore, the gaps that historically broke the demo — employee not attributed to the run, envelope not decremented, counterparty→category not mapped, compensation pipeline missing, §7.9 timeouts not applied, dashboard SSE not emitting envelope deltas — are not problems to discover and remediate later; they are problems to design out from the start of Phase 2.

## Solution Statement

A linear, dependency-ordered execution of `RealMetaPRD §12 Phases D + E + F` with the wiring lessons from `phase-1-critical-gap-remediation.md` baked in. Each task uses the `IMPLEMENT / PATTERN / IMPORTS / GOTCHA / VALIDATE` shape from `phase1-metalayer-foundation.md`. The plan is dependency-ordered: the Swan auth + GraphQL plumbing comes before any pipeline that uses it; the counterparty resolver comes before any builder that consumes its output; the budget envelope tool comes before any pipeline node that decrements; the compensation pipeline ships in the same phase as the booking pipeline, not as a follow-up.

The frontend is treated as a parallel work track that begins as soon as the SSE endpoints (`/runs/{id}/stream` and `/dashboard/stream`) exist; it does not depend on every pipeline node being implemented, only on the SSE event shapes being stable.

## Feature Metadata

**Feature Type**: New Capability (production tools, agents, pipelines, API surface, frontend)
**Estimated Complexity**: High (~14h backend + ~5h frontend; spans Swan integration, vision extraction, real-time SSE, and cross-DB writes)
**Primary Systems Affected**:
- `backend/api/` — three new routers (Swan webhook, external webhook, document upload), runs / SSE / trace / review endpoints
- `backend/orchestration/swan/` (new) — OAuth, GraphQL client, mutation-error helper
- `backend/orchestration/tools/` — eleven new tools (swan_query, counterparty_resolver, gl_account_classifier, journal_entry_builder, gl_poster, invariant_checker, budget_envelope, confidence_gate, review_queue, document_extractor validator, external_payload_parser)
- `backend/orchestration/agents/` — three production agents (counterparty_classifier, gl_account_classifier_agent, document_extractor)
- `backend/orchestration/conditions/` — production gates (`gating.passes_confidence`, `gating.needs_review`, `gating.posted`, `counterparty.unresolved`, `gl.unclassified`, `documents.totals_ok`, `documents.totals_mismatch`)
- `backend/orchestration/pipelines/` — four production YAMLs (`transaction_booked`, `transaction_released`, `document_ingested`, `external_event`)
- `backend/ingress/routing.yaml` (new file) — event_type → pipeline mapping
- `backend/orchestration/store/migrations/` — incremental migrations adding `envelope_category` on counterparties, seeding chart of accounts and account rules, seeding 12 months of synthetic transactions
- `frontend/` (new top-level) — Vite + React + TypeScript SPA

**Dependencies (already in `pyproject.toml`):** `httpx`, `aiosqlite`, `pyyaml`, `pydantic`, `anthropic`, `pytest-asyncio`. **New:** `rapidfuzz` for the counterparty fuzzy stage, `python-multipart` for `POST /documents/upload`, optional `sse-starlette` for cleaner SSE framing. Frontend: Vite, React 18, TypeScript, Tailwind, Zustand. Confirm `pyproject.toml` before adding; do not introduce duplicates.

---

## CONTEXT REFERENCES

### Relevant Codebase Files — IMPORTANT: YOU MUST READ THESE BEFORE IMPLEMENTING!

> **Note:** The Phase 1 implementation is the source of truth for *how* this project does things. Every signature listed below has been verified against the actual code as of 2026-04-25; if you find a divergence, trust the code over this plan and update the plan, not the code.

**Master PRD (the contract for this plan):**

- `Orchestration/PRDs/RealMetaPRD.md` (full file, 1849 lines)
  — **Why:** This plan implements `§12 Phase D + E + F`. Pay special attention to:
  - `§4` "MVP Scope" — what is in scope vs. out of scope (do not build the campaigns engine, payment-control hook, or per-API-key allocation)
  - `§5` "User Stories" — US-1 through US-12 are the acceptance frame
  - `§6.4` "Design patterns" — append-only, named conditions, registries-as-dicts, `propose → checkpoint → commit`
  - `§6.5` "Directory structure" — the canonical layout
  - `§7.1` "Webhook ingress (Swan)" — signature verify, idempotent insert, sub-50ms target
  - `§7.2` "Webhook ingress (external / CRM)" — generic provider pattern
  - `§7.3` "PDF document ingress" — full pipeline YAML for document_ingested
  - `§7.4` "Swan transaction booking pipeline" — full pipeline YAML for transaction_booked
  - `§7.5` "SQLite schemas (the seam)" — every column you write to (`employee_id_logical`, `accrual_link_id`, `reversal_of_id`, `category`, `envelope_id`)
  - `§7.6` "Hard invariants" — the five asserts run by `invariant_checker`
  - `§7.8` "Prompt-hash canonicalization" — already implemented in Phase 1; do not re-implement
  - `§7.9` "Retry / timeout / idempotency policy" — `timeout=4.5, max_retries=2` default; 15s for vision; no retry on `APITimeoutError`
  - `§7.10` "AgentResult — cross-runtime normalization" — your tools and agents must populate every field
  - `§7.11` "The wedge query" — the SQL that must return sensible rows after this phase lands
  - `§9.2` "Webhook signature" — `hmac.compare_digest`, `x-swan-secret` header, IP allowlist
  - `§10` "API Specification" — the inbound and internal endpoint contracts (use these route shapes verbatim)
  - `§11` "Success Criteria" — the on-stage 10-step demo script and the latency / invariant assertions
  - `§14` "Risks & Mitigations" — read these so you know which knives are sharp
  - `§15.2` "Demo seed dataset" — the employees, counterparties, and PDFs you will seed

**Phase 1 plan and remediation (the structural pattern this plan extends):**

- `Orchestration/Plans/phase1-metalayer-foundation.md` (1908 lines)
  — **Why:** the task format (`IMPLEMENT / PATTERN / IMPORTS / GOTCHA / VALIDATE`), section ordering, and citation style are mirrored here. Skim once to confirm conventions before you start.
- `Orchestration/Plans/phase-1-critical-gap-remediation.md` (525 lines)
  — **Why:** every wiring lesson from this remediation plan is baked into Phase 2 from the start. Do not duplicate its work; do read its `Phase 4` (envelope decrement) and `Phase 5` (compensation pipeline) sections — those describe the exact node shapes used here.
- `Orchestration/Plans/phase-1-gap-audit.md` (288 lines)
  — **Why:** snapshot of the post-Phase-1 state at the moment Phase 2 begins. Confirm the listed gaps still match the code before starting; if any gap has been closed (e.g. `passes_confidence` now reads from a real input), update accordingly.

**Phase 1 implementation (every file you will plug into):**

- `backend/api/main.py` (47 lines) — FastAPI lifespan, `/healthz`. Mount your new routers here.
- `backend/orchestration/context.py` (29 lines) — `FingentContext` dataclass (`run_id`, `pipeline_name`, `trigger_source`, `trigger_payload`, `node_outputs`, `store`, `employee_id`, `metadata`). Use `ctx.get(node_id)` for upstream reads.
- `backend/orchestration/registries.py` (99 lines) — `register_tool/agent/runner/condition(key, dotted)` and `get_tool/agent/runner/condition(key)`. Tools and agents are referenced from YAML by their registry key; runners by name (`anthropic`).
- `backend/orchestration/executor.py` (386 lines) — `execute_pipeline(pipeline_name, *, trigger_source, trigger_payload, store, employee_id=None, background=True, pipelines_dir=None) -> int`. Returns the new `pipeline_runs.id`. Use this from every webhook / upload handler. **Do not re-implement.**
- `backend/orchestration/runners/base.py` (56 lines) — `AgentRunner` Protocol + `AgentResult` (frozen dataclass with `output`, `model`, `response_id`, `prompt_hash`, `alternatives`, `confidence`, `usage`, `latency_ms`, `finish_reason`, `temperature`, `seed`, `raw`).
- `backend/orchestration/runners/anthropic_runner.py` (188 lines) — already wired to `AsyncAnthropic(timeout=4.5, max_retries=2)`; agents call this through the runner registry, not directly. The runner forces `tool_choice` for any tool named `submit_*`.
- `backend/orchestration/cache.py` (128 lines) — `cache_key(node_id, canonical_input) -> str`, `lookup`, `store`, `record_hit`. Tools opt into the cache by setting `cacheable: true` in YAML; the executor handles read-before-dispatch and write-after-success transparently.
- `backend/orchestration/cost.py` (41 lines) — `micro_usd(usage, provider, model)`, `COST_TABLE_MICRO_USD` keyed on `(provider, model)`. Already wired into `audit.propose_checkpoint_commit`.
- `backend/orchestration/audit.py` (76 lines) — `propose_checkpoint_commit(*, audit_db, audit_lock, run_id, node_id, result, runner, employee_id, provider, source='agent') -> int`. Returns the new `agent_decisions.id`. Already called from the executor when an agent dispatches.
- `backend/orchestration/event_bus.py` (97 lines) — in-process pub/sub keyed on `run_id`. `subscribe(run_id) -> asyncio.Queue`, `publish_event(run_id, event)`, `bus_reaper_task()`. SSE routes consume from this. **Add a top-level "dashboard" bus alongside the per-run buses.**
- `backend/orchestration/store/bootstrap.py` (109 lines) — `open_dbs(data_dir, run_migrations=True) -> StoreHandles`. `StoreHandles` exposes `accounting`, `orchestration`, `audit` connections plus matched `*_lock` `asyncio.Lock`s. Use `store.conn_for(name)` and `store.lock_for(name)`.
- `backend/orchestration/store/writes.py` (34 lines) — `write_tx(conn, lock)` async context manager. **All writes** go through this; never call `conn.commit()` directly. Already wraps `BEGIN IMMEDIATE` + commit/rollback.
- `backend/orchestration/store/schema/{accounting,orchestration,audit}.sql` — bootstrap source; mirror in `migrations/` if you add columns.
- `backend/orchestration/store/migrations/__init__.py` (163 lines) — `migrate_all(store)` runs at boot; per-DB `0001_init.py` already exists. Add new migrations as `0002_*.py`, `0003_*.py` etc., importable as Python modules. The pattern is `def up(conn): conn.execute(...)`.
- `backend/orchestration/yaml_loader.py` (158 lines) — `parse(raw, source)` and `load(path)`. `Pipeline` has `name`, `version`, `trigger`, `nodes`. `PipelineNode` has `id`, `tool`, `agent`, `runner`, `depends_on`, `when`, `cacheable`, properties `is_agent` / `is_tool`. Strict-key validation rejects unknown YAML fields.
- `backend/orchestration/dag.py` (56 lines) — `topological_layers(nodes) -> list[list[PipelineNode]]`; raises `PipelineLoadError` on cycle.
- `backend/orchestration/pipelines/noop_demo.yaml` — the only existing pipeline. Use as a *shape* reference; do not modify.
- `backend/orchestration/conditions/gating.py` (24 lines) — currently three stub conditions (`passes_confidence`, `needs_review`, `posted`) that read from `ctx.get("gate-confidence")` and `ctx.get("post-entry")`. **You will fill these in.** Do not add new files for the same domain; extend this file.
- `backend/tests/conftest.py` (93 lines) — fixture scaffolding for `aiosqlite` connections, `StoreHandles`, and pipeline runs. Mirror this style for new tests; do not invent a new fixture pattern.

**Reference guides — READ THESE BEFORE IMPLEMENTING:**

- `Dev orchestration/_exports_for_b2b_accounting/01_ORCHESTRATION_REFERENCE.md` (executor, Kahn scheduler, event sequence). Lines 114–124 (event semantics); lines 226 (SSE pattern).
- `Dev orchestration/_exports_for_b2b_accounting/02_YAML_WORKFLOW_DSL.md` (DSL grammar, named conditions, `when:` shape). Confirm before adding any new YAML feature.
- `Dev orchestration/_exports_for_b2b_accounting/03_SQLITE_BACKBONE.md` (WAL, single-writer, migrations). Lines 51–72 (counterparty schema), 97–140 (decision-trace schema), 193–200 (confidence floor), 209–215 (PRAGMAs already applied), 295–316 (BEGIN IMMEDIATE), 578 (`payload_version`).
- `Dev orchestration/_exports_for_b2b_accounting/04_AGENT_PATTERNS.md` (multiplicative confidence, ToolResult, refusal log). Lines 24–29 (why floor=0.50), 52 (strict tool schema), 90–107 (Claude vision pattern), 132–146 (compound_confidence with None=0.5), 176–191 (append-only ledger), 204–210 (CONFIDENCE_FLOOR), 214–305 (cache-warmer cascade).
- `Dev orchestration/_exports_for_b2b_accounting/05_swan_integration.md` (Swan API integration: OAuth, webhook, GraphQL, booking patterns). Lines 79–87 (counterparty cascade), 95–101 (virtual IBAN), 163 (token refresh), 176–177 (webhook secret + lifecycle), 201 (decision-trace + confidence routing), 206–207 (re-query pattern).
- `Dev orchestration/swan/SWAN_API_REFERENCE.md` (Swan-specific event types, GraphQL schema, OAuth endpoints). Lines 27–46 (OAuth `client_credentials` flow), 421–458 (`transaction(id)` query shape), 584–601 (mutation-error union helper), 632–640 (`account(id)` query), 688–692 (Swan IP allowlist), 694–713 (`x-swan-secret` header verification), 717–735 (webhook envelope shape), 737–762 (event types we care about — `Transaction.Booked`/`Released`/`Canceled`/`Enriched`, `Card.Created`, `Account.Updated`).
- `Orchestration/research/ANTHROPIC_SDK_STACK_REFERENCE.md` (Anthropic SDK details, vision API, tool use). Lines 556 (model pricing — already in `cost.py`), 571–619 (timeout/retry/idempotency policy — already in `anthropic_runner.py`), 901–914 (prompt-hash shape — already in `prompt_hash.py`), 1087–1107 (`Usage` object → `TokenUsage` mapping — already in runner).
- `Dev orchestration/tech framework/REF-FASTAPI.md`, `REF-SQLITE.md`, `REF-SSE.md`, `REF-ADK.md` — secondary references; consult only if a primary reference is silent on a detail.

### New Files to Create

**Backend — Swan plumbing:**
- `backend/orchestration/swan/__init__.py`
- `backend/orchestration/swan/oauth.py` — `SwanOAuthClient` (`get_token()`, `_refresh()`, in-process token cache, refresh-on-401, refresh-60s-before-expiry)
- `backend/orchestration/swan/graphql.py` — `SwanGraphQLClient` (httpx-based; `query(query, variables, operation_name)`; `fetch_transaction(id)`; `fetch_account(id)`; mutation-error union helper)

**Backend — API surface:**
- `backend/api/swan_webhook.py` — `POST /swan/webhook`
- `backend/api/external_webhook.py` — `POST /external/webhook/{provider}` plus `_VERIFIER_REGISTRY`
- `backend/api/documents.py` — `POST /documents/upload`
- `backend/api/runs.py` — `POST /pipelines/run/{name}`, `GET /runs/{id}`, `GET /runs/{id}/stream`, `GET /journal_entries/{id}/trace`, `POST /review/{entry_id}/approve`
- `backend/api/dashboard.py` — `GET /dashboard/stream`

**Backend — production tools (under `backend/orchestration/tools/`):**
- `swan_query.py` — `fetch_transaction(ctx)`, `fetch_account(ctx)`
- `counterparty_resolver.py` — `run(ctx)` (4-stage cascade with cache writeback)
- `gl_account_classifier.py` — `run(ctx)` (rule lookup; AI handoff if unmatched)
- `journal_entry_builder.py` — `build_cash(ctx)`, `build_accrual(ctx)`, `match_accrual(ctx)`, `build_reversal(ctx)`
- `gl_poster.py` — `post(ctx)` (writes `journal_entries`+`journal_lines`+`decision_traces`; integer cents only)
- `invariant_checker.py` — `run(ctx)` (the five §7.6 asserts)
- `budget_envelope.py` — `decrement(ctx)` (envelope lookup with employee→company fallback; `budget_allocations` insert; `envelope.decremented` event; `decision_traces` row)
- `confidence_gate.py` — `run(ctx)` (multiplicative confidence; sets `gate.passes_confidence` flag)
- `review_queue.py` — `enqueue(ctx)`
- `document_extractor.py` — `validate_totals(ctx)` (companion to the agent; `SUM(line_items) == total_cents` check)
- `external_payload_parser.py` — `run(ctx)` (generic CRM event → `expected_payment` row, used by `external_event.yaml`)

**Backend — production agents (under `backend/orchestration/agents/`):**
- `counterparty_classifier.py` — `run(ctx)` (Claude classification with closed counterparty list; emits `confidence` and `alternatives`; writes back to `counterparty_identifiers`)
- `gl_account_classifier_agent.py` — `run(ctx)` (Claude classification constrained to existing chart of accounts; writes back to `account_rules`)
- `document_extractor.py` — `run(ctx)` (Claude vision + strict invoice JSON tool; `timeout=15.0` override per `RealMetaPRD §7.9`)

**Backend — production conditions (extend existing files):**
- `backend/orchestration/conditions/gating.py` — fill in `passes_confidence(ctx)`, `needs_review(ctx)`, `posted(ctx)` (currently stubs)
- `backend/orchestration/conditions/counterparty.py` (new) — `unresolved(ctx)`
- `backend/orchestration/conditions/gl.py` (new) — `unclassified(ctx)`
- `backend/orchestration/conditions/documents.py` (new) — `totals_ok(ctx)`, `totals_mismatch(ctx)`

**Backend — pipelines (under `backend/orchestration/pipelines/`):**
- `transaction_booked.yaml` (mirror of `RealMetaPRD §7.4`)
- `transaction_released.yaml` (compensation pipeline)
- `document_ingested.yaml` (mirror of `RealMetaPRD §7.3`)
- `external_event.yaml` (stub for the generic provider claim)

**Backend — routing:**
- `backend/ingress/routing.yaml` (mirror of `RealMetaPRD §7.1` table)
- `backend/ingress/__init__.py`

**Backend — migrations (under `backend/orchestration/store/migrations/accounting/`):**
- `0002_chart_of_accounts.py` — seed PCG subset (`RealMetaPRD §15.3`)
- `0003_account_rules.py` — seed deterministic rules for the demo counterparties
- `0004_envelope_category_on_counterparties.py` — `ALTER TABLE counterparties ADD COLUMN envelope_category TEXT` (or new mapping table; see `Phase 2.D Task 8`)
- `0005_demo_counterparties.py` — seed Anthropic, Notion, OFI, Boulangerie Paul, SNCF, an Airbnb-style vendor, Linear, Fin/leasing, ~5 customers with virtual IBANs (`RealMetaPRD §15.2`)
- `0006_demo_swan_transactions.py` — seed ~200 rows of synthetic 12-month activity
- `0007_budget_envelopes.py` — seed envelopes for the three demo employees + company

**Backend — migrations (under `backend/orchestration/store/migrations/audit/`):**
- `0003_seed_swan_links.py` — populate `employees.swan_iban` and `swan_account_id` for the three demo employees

**Backend — data:**
- `data/blobs/` (directory; `.gitkeep`)
- `tests/fixtures/swan_*.json` — fake webhook payloads (see Phase 2.A and 2.E)
- `tests/fixtures/anthropic_invoice_2026_03.pdf` — demo PDF (~50€, billed to Tim)
- `tests/fixtures/notion_invoice_2026_03.pdf` — second demo PDF (~$45, billed to Marie)
- `tests/fixtures/supplier_invoice_paired.pdf` — third demo PDF (~€1,200; pairs with a SEPA-out in seed)

**Backend — tests:**
- `backend/tests/test_swan_oauth.py`, `test_swan_graphql.py`, `test_swan_webhook.py`, `test_external_webhook.py`, `test_document_upload.py`
- `backend/tests/test_counterparty_resolver.py`, `test_gl_classifier.py`, `test_journal_entry_builder.py`, `test_gl_poster.py`, `test_invariants.py`, `test_budget_envelope.py`, `test_confidence_gate.py`, `test_review_queue.py`
- `backend/tests/test_pipeline_transaction_booked.py`, `test_pipeline_transaction_released.py`, `test_pipeline_document_ingested.py`, `test_pipeline_external_event.py`
- `backend/tests/test_runs_api.py`, `test_dashboard_sse.py`, `test_trace_api.py`
- `backend/tests/test_wedge_query.py`
- `backend/tests/test_employee_attribution.py`, `test_envelope_routing.py`

**Frontend:**
- `frontend/package.json`, `frontend/vite.config.ts`, `frontend/tsconfig.json`, `frontend/index.html`
- `frontend/src/main.tsx`, `frontend/src/App.tsx`
- `frontend/src/components/Ledger.tsx`, `EnvelopeRings.tsx`, `UploadZone.tsx`, `TraceDrawer.tsx`, `ReviewQueue.tsx`, `InfraTab.tsx`
- `frontend/src/lib/sse.ts`, `frontend/src/lib/api.ts`, `frontend/src/lib/format.ts`
- `frontend/src/store.ts` (Zustand)

### Relevant Documentation — YOU SHOULD READ THESE BEFORE IMPLEMENTING!

- [Anthropic Python SDK — `messages.create` with vision (PDF / document blocks)](https://docs.anthropic.com/en/docs/build-with-claude/vision)
  — **Why:** the document_extractor agent passes the PDF as a `document` content block; confirm the exact shape (`{"type": "document", "source": {"type": "base64", "media_type": "application/pdf", "data": "..."}}`) against the SDK version pinned in `pyproject.toml`.
- [Anthropic Python SDK — Tool use (function calling)](https://docs.anthropic.com/en/docs/build-with-claude/tool-use)
  — **Why:** the document_extractor and the two classifier agents force structured output via a single `submit_*` tool. The `anthropic_runner.py` already prefers `tool_choice={"type": "tool", "name": "submit_*"}` when such a tool is registered; confirm and use that convention.
- [FastAPI `StreamingResponse` and SSE](https://fastapi.tiangolo.com/advanced/custom-response/#streamingresponse)
  — **Why:** the runs and dashboard SSE routes use `text/event-stream` with `data: {...}\n\n` framing. The `event_bus.py` already provides the queue; the route consumes it and formats.
- [`hmac.compare_digest`](https://docs.python.org/3/library/hmac.html#hmac.compare_digest)
  — **Why:** Swan webhook signature verification is a constant-time string compare on `x-swan-secret`; do not use `==`. (`RealMetaPRD §9.2`)
- [`rapidfuzz` token_set_ratio](https://maxbachmann.github.io/RapidFuzz/Usage/fuzz.html#token-set-ratio)
  — **Why:** the counterparty fuzzy stage. Threshold ≥ 85 per `04_AGENT_PATTERNS.md:208`.
- [Vite + React + TypeScript starter](https://vitejs.dev/guide/) and [Zustand](https://docs.pmnd.rs/zustand/getting-started/introduction)
  — **Why:** the frontend stack per `RealMetaPRD §8`.
- [SSE in the browser — `EventSource`](https://developer.mozilla.org/en-US/docs/Web/API/EventSource)
  — **Why:** the frontend subscribes to `/runs/{id}/stream` and `/dashboard/stream` via `EventSource`. No polyfill needed for Vite + modern browsers.

### Patterns to Follow

These are the patterns Phase 1 established. Mirror them; do not invent variants.

**Single-writer lock pattern** (`RealMetaPRD §6.6`, `backend/orchestration/store/writes.py`):
Every write to `accounting.db`, `orchestration.db`, or `audit.db` happens inside `async with write_tx(store.conn_for("..."), store.lock_for("...")) as conn:`. Tools and agents receive `ctx`; they reach `conn` and `lock` through `ctx.store`. Compensation reversals and budget allocations hold the lock the same way the forward path does.

```python
# Canonical write pattern, copied from how Phase 1 audit + executor use it:
async with write_tx(ctx.store.accounting, ctx.store.accounting_lock) as conn:
    await conn.execute("INSERT INTO journal_entries (...) VALUES (...)", values)
    entry_id = (await conn.execute("SELECT last_insert_rowid()")).fetchone()[0]
    # ...subsequent writes inside the same txn
```

**Named-condition pattern** (`RealMetaPRD §6.4`, `backend/orchestration/conditions/`):
`when:` references a registered key like `gating.passes_confidence`. The function takes `ctx` and returns `bool`. No expression strings, no DSL. New conditions go in domain-named files (`gating.py`, `counterparty.py`, `gl.py`, `documents.py`); register them with `register_condition("conditions.<file>:<name>", "backend.orchestration.conditions.<file>:<name>")` in `registries.py` (or — preferably — in a per-domain `_register()` function called from `backend/orchestration/__init__.py`).

**`propose → checkpoint → commit` for agent writes** (`RealMetaPRD §6.4`, `backend/orchestration/audit.py`):
The executor already calls `propose_checkpoint_commit` after every agent dispatch when the agent returns an `AgentResult`. Your agents return an `AgentResult`; you do not call the audit helper yourself. **Tools** that mutate `accounting.db` (counterparty resolver, journal entry builder, GL poster, budget envelope) are not agents and do not write `agent_decisions` rows — but they **do** write `decision_traces` rows with `source='rule'`.

**Append-only discipline** (`RealMetaPRD §6.4`):
The compensation pipeline does **not** UPDATE the original `journal_entries` to delete its existence. It INSERTs a new entry with `reversal_of_id` set, then UPDATEs only the original `status` from `'posted'` to `'reversed'`. This is the **single legitimate UPDATE outside `pipeline_runs.status`** in this entire phase; document it inline. Same for budget allocations: insert a negative-amount allocation; do not delete.

**Integer-cents discipline** (`RealMetaPRD §7.7`):
Money is always `INTEGER` in the schema and `int` in Python. There is no float on a money path. The CI grep audit (Phase 1 ships it; see `Validation Commands → Level 1`) catches violations. VAT splits use integer arithmetic with `divmod(amount * rate_bp, 10_000)` — never `amount * rate_pct / 100`.

**Logical FK convention** (`RealMetaPRD §6.2`):
Cross-DB references use the suffix `_logical` and are not enforced by SQL FOREIGN KEY because SQLite cannot. The columns `journal_entries.source_run_id`, `decision_traces.agent_decision_id_logical`, `pipeline_runs.employee_id_logical`, and `agent_decisions.run_id_logical` / `line_id_logical` are all of this kind. A CI test (Phase 2.J) joins across DBs and asserts no orphans.

**Idempotency boundary** (`RealMetaPRD §7.1`, `§7.3`):
External events are uniquely keyed on `(provider, event_id)` in `orchestration.external_events`. Documents are uniquely keyed on `sha256` in `accounting.documents`. The handler always does `INSERT OR IGNORE`; on conflict, look up the existing row, return its id, and do NOT re-trigger a run. Webhook redelivery, replay, and PDF re-uploads are all no-ops.

**Cache-writeback cascade** (`04_AGENT_PATTERNS.md:214-305`):
Every AI resolution writes back to a deterministic cache so the next occurrence skips the model. Counterparty AI hits write to `counterparty_identifiers` with `source='ai'`; GL classifier AI hits write to `account_rules` with `source='ai'`. The next request for the same input hits the rule, not the LLM. This is **the** mechanism by which the demo's "second occurrence is free" claim works.

**Multiplicative confidence with None=0.5** (`04_AGENT_PATTERNS.md:132-146`):
`confidence_gate.run` collects per-stage confidences from `ctx.node_outputs` and multiplies. `None` (missing signal) is treated as `0.5`. The floor is `0.50` from `confidence_thresholds` (scope `'global'`). Below floor → `needs_review` path; at-or-above → `passes_confidence` path. The `posted` condition is downstream of `gl_poster.post`, not the gate.

**SSE event sequence per run** (`01_ORCHESTRATION_REFERENCE.md:114-124`, `RealMetaPRD §11`):
A clean N-node run emits exactly `2N + 2` events: `pipeline_started` + per-node (`node_started`, `node_completed` | `node_skipped` | `node_failed` | `cache_hit`) + `pipeline_completed` | `pipeline_failed`. Adding new pipeline-level events (e.g. `envelope.decremented`, `ledger.entry_posted`, `review.enqueued`) does **not** break the count; those are emitted on the dashboard bus, not the per-run bus.

---

## IMPLEMENTATION PLAN

Phase 2 breaks into eleven sub-phases (`2.A` → `2.K`). Backend phases are dependency-ordered: each later phase plugs into output produced by earlier phases. The Frontend phase (`2.K`) can begin in parallel as soon as `2.J` (Internal API surface) is past the route-skeleton stage.

### Phase 2.A — Swan Auth & GraphQL Plumbing (`RealMetaPRD §12 Phase D`; ~1.5h)

**Goal:** `swan/oauth.py` and `swan/graphql.py` exist and are unit-tested. A live integration test (skippable without `SWAN_*` env vars) hits Swan sandbox.

- ✅ `SwanOAuthClient` with `client_credentials` grant, in-process token cache, refresh-60s-before-expiry, refresh-on-401-and-retry-once
- ✅ `SwanGraphQLClient` with httpx (NOT `gql`); `fetch_transaction(id)` and `fetch_account(id)` returning typed dicts; `query()` raises on `errors` field; mutation-error union helper for future write paths
- ✅ Env-var contract: `SWAN_CLIENT_ID`, `SWAN_CLIENT_SECRET`, `SWAN_OAUTH_URL`, `SWAN_GRAPHQL_URL`, `SWAN_PROJECT_ID`, `SWAN_WEBHOOK_SECRET` — all listed in `.env.example`
- ✅ Tests: token-refresh-on-401 with mock httpx transport; envelope-shape parsing; mutation-error union helper

### Phase 2.B — Webhook Ingress (Swan + External) and Employee Resolution (`§7.1`, `§7.2`, `§9.2`; ~2h)

**Goal:** `POST /swan/webhook` and `POST /external/webhook/{provider}` exist; both verify signature in constant time, idempotent-insert on `(provider, event_id)`, employee-resolve at trigger time, enqueue the right pipeline via `routing.yaml`, return 200 within ~50ms target.

- ✅ `routing.yaml` mirrors `RealMetaPRD §7.1` table; loaded once at boot
- ✅ Swan webhook handler: `hmac.compare_digest` on `x-swan-secret`; `INSERT OR IGNORE` into `orchestration.external_events`; on insert → resolve `employee_id` from `audit.employees` via the GraphQL re-query of `account(id)` (Phase 2.A) → call `execute_pipeline(name, trigger_source='swan.<eventType>', trigger_payload=<envelope>, store=store, employee_id=<resolved>)`
- ✅ External webhook handler: per-provider verifier registry (`_VERIFIER_REGISTRY: dict[str, Verifier]`); ship Stripe HMAC verifier as the canonical one; `INSERT OR IGNORE`; route via `routing.yaml`; default `log_and_continue` for unknown event types
- ✅ Webhook handlers do NOT call GraphQL inline if the path is hot — defer the re-query to the first node in the pipeline (`swan_query.fetch_transaction`). The handler only resolves employee from a cached `swan_accounts` view (or trusts the routing payload)
- ✅ Tests: signature-mismatch → 401; duplicate eventId → 200 + no-op; valid → 200 + `pipeline_runs` row with `employee_id_logical` set
- ✅ The handler returns 200 **before** the pipeline finishes (background dispatch via `execute_pipeline(..., background=True)`)

### Phase 2.C — Counterparty Resolution Cascade + Cache Writeback (`§7.4`, `04_AGENT_PATTERNS.md:214-305`; ~1.5h)

**Goal:** `counterparty_resolver.run` resolves a Swan transaction or a parsed PDF invoice to a `counterparty_id` via the four-stage cascade, writing back AI hits to `counterparty_identifiers`. The companion `counterparty_classifier` agent runs only when stages 1–3 miss.

- ✅ Stage 1 — IBAN exact (confidence 1.0); for inbound `Credit`, look up `debtor.iban`; for outbound `Debit`, look up `creditor.iban`
- ✅ Stage 2 — virtual IBAN match (confidence 1.0; inbound only)
- ✅ Stage 3a — exact `merchantId` match for card transactions (confidence 0.95)
- ✅ Stage 3b — MCC + fuzzy merchant name (`rapidfuzz.fuzz.token_set_ratio`, threshold 85, confidence ≤ 0.85)
- ✅ Stage 4 — pure fuzzy name across all `counterparties` (threshold 85; confidence capped at 0.85)
- ✅ Cache writeback: every successful resolution INSERTs into `counterparty_identifiers` with `source` set to the stage name (`'iban'`, `'merchant_id'`, `'mcc_fuzzy'`, `'fuzzy_name'`, or `'ai'`)
- ✅ AI handoff path (`counterparty_classifier.run`) — Claude Sonnet 4.6; closed list of existing `counterparties` rows; submits via a `submit_counterparty` tool that yields `{counterparty_id, confidence, alternatives}`; writes back to `counterparty_identifiers` with `source='ai'`
- ✅ Tests: each stage independently; cache hit on second call (`record_hit` bumps); AI fallback only fires when all deterministic stages miss

### Phase 2.D — GL Classification Cascade + Counterparty → Envelope Category Mapping (`§7.4`, `phase-1-critical-gap-remediation.md Phase 2`; ~1h)

**Goal:** `gl_account_classifier.run` resolves to a chart-of-accounts code via `account_rules`. `counterparties.envelope_category` is populated for every resolver hit, so the budget-envelope tool has a clean lookup.

- ✅ Migration `0002_chart_of_accounts.py` seeds the PCG subset from `RealMetaPRD §15.3` (411, 401, 421, 445, 4456, 512, 606100, 613, 624, 6257, 626100, 626200, 706000)
- ✅ Migration `0003_account_rules.py` seeds: `(counterparty='Anthropic', mcc=NULL) → 626100`, `(counterparty='Notion') → 626200`, `(counterparty='OFI') → 626200`, `(counterparty='Boulangerie Paul') → 6257`, `(counterparty='SNCF') → 624`, etc. Source `RealMetaPRD §15.2` for the seed list
- ✅ Migration `0004_envelope_category_on_counterparties.py` adds a `envelope_category TEXT` column on `counterparties` and seeds the demo entries (Anthropic, OpenAI → `'ai_tokens'`; Notion, Linear → `'saas'`; Boulangerie Paul → `'food'`; SNCF, Airbnb → `'travel'`; Fin → `'leasing'`; OFI → `'saas'`). The closed enum is the union of `budget_envelopes.category` values seen in `0007`
- ✅ `gl_account_classifier.run` — rule lookup ordered by `precedence`; first match wins; no AI handoff if matched
- ✅ `gl_account_classifier_agent.run` — Claude Sonnet 4.6; constrained to existing `chart_of_accounts.code` values via a `submit_gl_account` tool; writes back to `account_rules` with `source='ai'` so the next request hits the rule
- ✅ Counterparty resolver writes `envelope_category` whenever it creates or updates a `counterparties` row
- ✅ Tests: rule hit returns deterministically; AI fallback path writes a rule + caches the result; envelope category lookup returns the seeded value for known counterparties; unknown counterparty → `'uncategorized'`

### Phase 2.E — Journal Entry Builder, GL Poster, Invariants (`§7.4`, `§7.6`; ~2h)

**Goal:** four booking patterns + reversal pattern produce balanced entries; `gl_poster.post` writes them with their `decision_traces`; `invariant_checker.run` enforces the five hard invariants.

- ✅ `journal_entry_builder.build_cash(ctx)` — given a Swan transaction + counterparty + GL account: emit Debit/Credit lines for the four booking patterns
  - **Card spend (Debit, side='Debit')**: `Dr Expense (account from classifier), Cr Bank (512)`. VAT split if rule provides one.
  - **SEPA-in (Debit, side='Credit')**: `Dr Bank (512), Cr AR (411) | Revenue (706000)` based on counterparty kind (`customer` vs unknown)
  - **SEPA-out (Debit, side='Debit')**: `Dr AP (401) | Expense (varies), Cr Bank (512)`. If `match_accrual` returned an `accrual_link_id`, this is the AP-reversal pair (Dr AP, Cr Bank).
  - **Fee (any 'Fees' product)**: `Dr Bank fees (627), Cr Bank (512)` — chart code is added as 627; the seed includes it
  - **Internal transfer**: out of demo scope; emit a placeholder that lands in review
- ✅ `journal_entry_builder.build_accrual(ctx)` — given a parsed PDF + counterparty + GL: `Dr Expense + Dr VAT-deductible (4456); Cr AP (401)`. VAT-deductible is the `vat_cents` from the extractor; rate from `vat_rates` lookup.
- ✅ `journal_entry_builder.match_accrual(ctx)` — for SEPA-out + supplier counterparty: query `journal_entries` for an unpaired accrual entry against the same `counterparty_id` whose AP balance equals the SEPA-out amount; return `{accrual_link_id: <id>}` or `{}`
- ✅ `journal_entry_builder.build_reversal(ctx)` — for `Released` / `Canceled` Swan events: load the original entry, flip debit and credit lines on each row, set `reversal_of_id = original.id`, basis = original.basis, status='draft'
- ✅ `gl_poster.post(ctx)` — single chokepoint: validates `SUM(debit_cents) == SUM(credit_cents)`, INSERTs `journal_entries` (status='posted'), INSERTs all `journal_lines`, INSERTs one `decision_traces` row per line (`source='rule'` for deterministic-only paths, `source='agent'` with `agent_decision_id_logical` for AI-touched paths). Holds `accounting_lock` across the entire txn.
- ✅ `invariant_checker.run(ctx)` — after every post, runs the five `RealMetaPRD §7.6` invariants. Failure → emit `pipeline_failed` and route to a `system_review` table (or just `review_queue` with `kind='invariant_failure'`)
- ✅ Tests: each booking pattern returns balanced lines; reversal pattern produces a perfect mirror; invariant failure on a tampered entry raises and rolls back; multi-line accrual with VAT sums to total

### Phase 2.F — Confidence Gate, Review Queue, Budget Envelopes (`§7.4`, `phase-1-critical-gap-remediation.md Phase 4`; ~1.5h)

**Goal:** the confidence gate routes weak runs to review; the review queue persists them; the budget envelope tool decrements after every `posted` entry, gated on the right category.

- ✅ `confidence_gate.run(ctx)` — collects upstream confidences from `ctx.node_outputs` (resolver, classifier, builder), multiplies (None=0.5), reads floor from `confidence_thresholds` (default 0.50), returns `{ok: bool, computed_confidence: float, contributing_factors: [...]}`
- ✅ `gating.passes_confidence(ctx)` — reads `ctx.get("gate-confidence").ok`
- ✅ `gating.needs_review(ctx)` — reads `not ctx.get("gate-confidence").ok`
- ✅ `gating.posted(ctx)` — reads `ctx.get("post-entry").status == "posted"`
- ✅ `counterparty.unresolved(ctx)` — reads `ctx.get("resolve-counterparty").counterparty_id is None`
- ✅ `gl.unclassified(ctx)` — reads `ctx.get("classify-gl-account").gl_account is None`
- ✅ `documents.totals_ok(ctx)` / `totals_mismatch(ctx)` — reads `ctx.get("validate").ok`
- ✅ `review_queue.enqueue(ctx)` — INSERTs into a `review_queue` table (add to `accounting.sql` migrations) with `entry_id`, `kind`, `confidence`, `created_at`
- ✅ `budget_envelope.decrement(ctx)` — reads `ctx.employee_id`, `entry.entry_date[:7]` for period, `counterparty.envelope_category`; looks up `budget_envelopes` (employee → company fallback); INSERTs `budget_allocations` for each expense line (account code starting with `6`); writes `decision_traces` with `source='rule'`; emits `envelope.decremented` event on the dashboard bus
- ✅ For `category='uncategorized'`: emit `envelope.skipped` and skip the allocation; do NOT crash
- ✅ Tests: gate passes/fails on multiplicative confidence; uncategorized counterparty skips decrement; multi-line entry sums correctly; employee→company fallback works for company-account transactions; `envelope.decremented` event arrives on the dashboard bus

### Phase 2.G — Pipelines (booking, compensation, document, external) (`§7.3`, `§7.4`; ~1.5h)

**Goal:** the four production pipelines exist as YAML, parse cleanly via `yaml_loader`, and are reachable from `routing.yaml`.

- ✅ `transaction_booked.yaml` — verbatim from `RealMetaPRD §7.4`; nodes in order: `fetch-transaction`, `resolve-counterparty`, `ai-counterparty-fallback`, `classify-gl-account`, `ai-account-fallback`, `match-accrual`, `build-cash-entry`, `gate-confidence`, `post-entry`, `queue-review`, `assert-balance`, `decrement-envelope`. The decrement node depends on `[post-entry]`, gated `gating.posted`.
- ✅ `transaction_released.yaml` — compensation pipeline. Nodes: `fetch-transaction` (same tool), `find-original-entry` (new tool: query `journal_entries` joined to `journal_lines.swan_transaction_id`), `build-reversal`, `gate-confidence` (always passes — reversal of a posted entry), `post-entry`, `mark-original-reversed` (the legitimate UPDATE), `decrement-envelope-reversal` (negative `amount_cents`), `assert-balance`. Idempotent: short-circuits if original is already `'reversed'`.
- ✅ `document_ingested.yaml` — verbatim from `RealMetaPRD §7.3`; nodes in order: `extract` (agent), `validate`, `needs-review-on-bad-totals`, `resolve-counterparty`, `ai-counterparty-fallback`, `classify-gl-account`, `ai-account-fallback`, `build-accrual-entry`, `gate-confidence`, `post-entry`, `queue-review`, `assert-balance`, `decrement-envelope`.
- ✅ `external_event.yaml` — stub: `parse-payload` (calls `external_payload_parser.run`), `log-and-complete`. Proves the routing path; does not commit downstream behavior.
- ✅ Each pipeline has a `version: 1`. Bumping the version is the future-cache-invalidation pattern; the cache key already includes `pipeline_version` indirectly via `code_version`.
- ✅ Tests per pipeline: full happy-path run + count `pipeline_events` rows = `2N + 2`; one cache hit on second run of the same input; `needs_review` short-circuits `post-entry`

### Phase 2.H — PDF Document Ingestion (`§7.3`; ~1.5h)

**Goal:** `POST /documents/upload` with SHA256 idempotency; `document_extractor` agent runs Claude vision through a strict invoice JSON tool; `validate_totals` enforces the math; the rest of `document_ingested.yaml` flows through the same builders as the Swan path.

- ✅ `POST /documents/upload` — multipart form; reads bytes; computes `sha256`; saves blob to `data/blobs/<sha256>` (idempotent); `INSERT OR IGNORE` into `accounting.documents` keyed on `sha256`; if existing, return existing `document_id` and most-recent `run_id`; if new, trigger `document_ingested.yaml` with `trigger_source='document.uploaded'` and `trigger_payload={'document_id': ..., 'sha256': ..., 'employee_id': ...}`. Returns `{document_id, sha256, run_id, stream_url}`.
- ✅ `agents/document_extractor.py` — Claude vision; `timeout=15.0` override per `§7.9`; PDF passed as `{"type": "document", "source": {"type": "base64", "media_type": "application/pdf", "data": ...}}`; forces output via a `submit_invoice` tool with strict schema (`supplier_name`, `invoice_number`, `date`, `due_date`, `items[]`, `subtotal_cents`, `vat_percent`, `vat_cents`, `total_cents`, `currency='EUR'`); confidence comes from a `confidence` field in the tool input
- ✅ `tools/document_extractor.py:validate_totals(ctx)` — reads extraction output; asserts `SUM(line_items.amount_cents) == subtotal_cents`; asserts `subtotal_cents + vat_cents == total_cents`; returns `{ok: bool, errors: [...]}`. On failure, `documents.totals_mismatch` routes to review queue.
- ✅ Tests: drop a fake PDF (or fixture extraction JSON) → accrual entry posts; drop again → no duplicate; trace points to the document and the Claude vision call; bad-totals fixture lands in review

### Phase 2.I — Internal API Surface (`§10`; ~1.5h)

**Goal:** every internal route from `RealMetaPRD §10` exists; SSE streams work; trace drilldown returns the joined view; review-approve writes the approver and triggers a re-post.

- ✅ `POST /pipelines/run/{name}` — manual trigger; body `{"trigger_payload": {...}, "employee_id": <int|null>}`; returns `{run_id, stream_url}`
- ✅ `GET /runs/{run_id}` — fetch the run; join `pipeline_runs` + `pipeline_events`; for AI-touched runs, also join `audit.agent_decisions` (cross-DB join via ATTACH or in-Python merge)
- ✅ `GET /runs/{run_id}/stream` — SSE; subscribes to the per-run bus from `event_bus.py`; emits `pipeline_started`, `node_started`, `node_completed`, `node_skipped`, `node_failed`, `cache_hit`, `pipeline_completed`, `pipeline_failed`. Heartbeat every 15s. Closes on `pipeline_completed`/`pipeline_failed`.
- ✅ `GET /dashboard/stream` — SSE; subscribes to the top-level dashboard bus; emits `ledger.entry_posted`, `envelope.decremented`, `envelope.skipped`, `review.enqueued`. Long-lived; never auto-closes.
- ✅ `GET /journal_entries/{id}/trace` — drills from a `journal_lines` row to its `decision_traces` row; for each trace with non-null `agent_decision_id_logical`, joins to `audit.agent_decisions` and `audit.agent_costs`; for each entry, also joins to source `swan_event` (via `journal_lines.swan_transaction_id`) or `documents` (via `journal_lines.document_id`).
- ✅ `POST /review/{entry_id}/approve` — body `{"approver_id": <int>}`; UPDATEs `decision_traces.approver_id` + `approved_at`; UPDATEs `journal_entries.status` from `'review'` to `'posted'`; emits `ledger.entry_posted` + the deferred `envelope.decremented`
- ✅ Mount all routers in `backend/api/main.py` lifespan
- ✅ Tests: each route end-to-end (happy path + 404 + idempotent re-trigger); SSE delivers events in order; trace drilldown returns the joined shape

### Phase 2.J — Demo Seed Dataset + Wedge Query Test (`§15.2`, `§7.11`; ~1h)

**Goal:** the migrations seed three employees, ten counterparties, ~200 synthetic Swan transactions, three demo PDFs in `tests/fixtures/`, and the wedge query returns sensible per-employee numbers.

- ✅ Migration `0005_demo_counterparties.py` — see Phase 2.D
- ✅ Migration `0006_demo_swan_transactions.py` — generate ~200 rows of `swan_transactions` across 12 months, with realistic distribution: ~30% card spend, ~20% SEPA-in, ~30% SEPA-out, ~10% fees, ~10% internal. Each row links to a counterparty and an employee account. Use `random.seed(42)` for determinism.
- ✅ Migration `0007_budget_envelopes.py` — for each of the three demo employees: `food`, `travel`, `saas`, `ai_tokens`, `leasing` envelopes per month for the last three months. Caps from a sensible distribution.
- ✅ Audit migration `0003_seed_swan_links.py` — populate `employees.swan_iban` and `employees.swan_account_id` for Tim, Marie, Paul matching the `0005` and `0006` data so resolution works
- ✅ Test `test_wedge_query.py` — runs the `RealMetaPRD §7.11` SQL against the seeded DB; asserts the result is non-empty, has one row per employee, sums match the seed
- ✅ Test `test_cross_db_orphan_check.py` — joins `pipeline_runs.id` with `agent_decisions.run_id_logical`, `journal_lines.id` with `decision_traces.line_id` and `agent_decisions.line_id_logical`; asserts no orphans
- ✅ The on-stage demo (Phase 2.K manual validation) walks through this seed

### Phase 2.K — Frontend (`§12 Phase F`; ~5h, parallel work track)

**Goal:** the demo lands visually. A judge sees a live ledger, three envelope rings per employee, can drag a PDF onto the page, and can click any line to see the trace.

- ✅ `frontend/` scaffolded with Vite + React 18 + TypeScript + Tailwind; one HTML entry; `frontend/src/lib/api.ts` wraps `fetch` for all backend routes; `frontend/src/lib/sse.ts` wraps `EventSource` with a simple subscription model
- ✅ `Ledger.tsx` — subscribes to `/dashboard/stream` for `ledger.entry_posted`; pulls initial rows from `GET /journal_entries?limit=50`; animates new rows with Framer Motion or simple CSS transitions
- ✅ `EnvelopeRings.tsx` — subscribes to `envelope.decremented` and `envelope.skipped`; reconciles state from `GET /envelopes?employee_id=...`; renders three or five rings per employee with `used / cap`
- ✅ `UploadZone.tsx` — drag-drop a single PDF; POSTs to `/documents/upload`; opens an SSE subscription to `/runs/{run_id}/stream` to show node-by-node progress
- ✅ `TraceDrawer.tsx` — opens on click of any ledger row; calls `GET /journal_entries/{id}/trace`; renders the chain (line → trace → agent_decision → agent_costs → source webhook/document)
- ✅ `ReviewQueue.tsx` — list of `status='review'` entries; each row has an "Approve" button calling `POST /review/{id}/approve`
- ✅ `InfraTab.tsx` — one-page credibility surface: list of recent runs, recent events, three DB sizes; for the judge who wants to see the bones
- ✅ `App.tsx` mounts a tabbed layout: Dashboard | Review | Infra
- ✅ The whole app fits a 1080p screen at the demo resolution; envelope rings are large enough to read from 3m
- ✅ Manual rehearsal: drop a Swan webhook fixture (via `curl`), watch the ledger row appear within 5s, click → trace; drop the Anthropic PDF → accrual within 10s; drop a `Released` webhook → reversal animates in

### Phase 2.L — Validation & Rehearsal

**Goal:** the on-stage 10-step demo from `RealMetaPRD §11` works end-to-end, twice in a row (idempotency).

- ✅ Run all four validation levels (see below)
- ✅ Walk the §11 success-criteria script on a fresh DB
- ✅ Re-run the same script on the same DB; confirm no duplicate entries, `cache_hit` events on counterparty resolver
- ✅ Wedge SQL on stage; one row per employee; sums match the seed
- ✅ Backup video recording of the full demo (per `§14 risk #1` mitigation)

---

## STEP-BY-STEP TASKS

Execute every task in order, top to bottom. Each task is atomic and independently testable.

### Task Format Guidelines

Use information-dense keywords:
- **CREATE**: New files
- **UPDATE**: Modify existing files
- **ADD**: Insert into existing files
- **REMOVE**: Delete deprecated code (rare in this plan)
- **REFACTOR**: Restructure (rare in this plan; do not refactor Phase 1 code)
- **MIRROR**: Copy a pattern from elsewhere

### Task 1 — UPDATE `pyproject.toml` to add Phase 2 dependencies

- **IMPLEMENT**: Add `rapidfuzz>=3`, `python-multipart>=0.0.9` to `[project] dependencies`. Add no other deps for the backend; do NOT add `gql` (use httpx), `tenacity` (use SDK retries + asyncio.wait_for), or `pydantic-settings` (use os.environ).
- **PATTERN**: Existing `[project] dependencies` block in `pyproject.toml`.
- **IMPORTS**: N/A (TOML edit).
- **GOTCHA**: `python-multipart` is required by FastAPI's `Form` and `UploadFile`. Without it, `POST /documents/upload` 500s with a cryptic message. Confirm the lock file is regenerated (`uv lock`).
- **VALIDATE**:
  ```bash
  cd "/home/developer/Projects/HEC Paris" && uv sync && python -c "import rapidfuzz; import multipart; print('OK')"
  ```

### Task 2 — UPDATE `.env.example` with Phase 2 envs

- **IMPLEMENT**: Add `SWAN_CLIENT_ID`, `SWAN_CLIENT_SECRET`, `SWAN_OAUTH_URL=https://oauth.swan.io/oauth2/token`, `SWAN_GRAPHQL_URL=https://api.swan.io/sandbox-partner/graphql`, `SWAN_PROJECT_ID`, `SWAN_WEBHOOK_SECRET`, `STRIPE_WEBHOOK_SECRET` (placeholder for the verifier registry), `ANTHROPIC_API_KEY` (already there if Phase 1 set it; confirm), `FINGENT_DATA_DIR=./data` (already there).
- **PATTERN**: Existing `.env.example` shape.
- **IMPORTS**: N/A.
- **GOTCHA**: Comments only — never commit real secrets. Add a top-of-file warning.
- **VALIDATE**: `grep -E '^SWAN_(CLIENT_ID|CLIENT_SECRET|OAUTH_URL|GRAPHQL_URL|PROJECT_ID|WEBHOOK_SECRET)=' .env.example | wc -l` should be `6`.

### Task 3 — CREATE `backend/orchestration/swan/__init__.py`

- **IMPLEMENT**: Empty file (package marker).
- **PATTERN**: Existing empty `__init__.py` files under `orchestration/`.
- **IMPORTS**: N/A.
- **GOTCHA**: None.
- **VALIDATE**: `test -f backend/orchestration/swan/__init__.py`.

### Task 4 — CREATE `backend/orchestration/swan/oauth.py`

- **IMPLEMENT**: `class SwanOAuthClient` with constructor `(client_id, client_secret, oauth_url, *, http_client: httpx.AsyncClient | None = None)`. Methods:
  - `async get_token() -> str` — returns cached token if `now < expires_at - 60s`; else calls `_refresh_token()`
  - `async _refresh_token() -> None` — POSTs `{grant_type: client_credentials, client_id, client_secret}` to `oauth_url`; on success, updates `self._token` and `self._expires_at`
  - `async invalidate()` — sets `self._token = None`, forces refresh on next `get_token()`. Called by `SwanGraphQLClient` on 401
- **PATTERN**: Reference document `Phase 2 Implementation Guidance §A` (the persisted reference output from the planning agent); `SWAN_API_REFERENCE.md:27-46`.
- **IMPORTS**: `httpx`, `time` (or `datetime`), `dataclasses` for the cached-token struct.
- **GOTCHA**: Do **not** send a `scope` parameter. Project tokens authorize on membership; sending `scope` returns 400. The full `SANDBOX_<uuid>` or `LIVE_<uuid>` prefix is part of the `client_id`; send literally.
- **VALIDATE**:
  ```bash
  python -m pytest backend/tests/test_swan_oauth.py -q
  ```

### Task 5 — CREATE `backend/tests/test_swan_oauth.py`

- **IMPLEMENT**: Test `get_token()` calls refresh on first call; second call within window returns cached value; refresh after expiry triggers a new POST; `invalidate()` forces refresh. Use `httpx.MockTransport` for the underlying HTTP calls.
- **PATTERN**: Existing tests under `backend/tests/`; mock-transport pattern.
- **IMPORTS**: `pytest`, `pytest_asyncio`, `httpx`.
- **GOTCHA**: `MockTransport` matches by callable; pass an `async def handler(request)` that returns `httpx.Response(200, json={...})`.
- **VALIDATE**: `python -m pytest backend/tests/test_swan_oauth.py -v`.

### Task 6 — CREATE `backend/orchestration/swan/graphql.py`

- **IMPLEMENT**: `class SwanGraphQLClient` with constructor `(graphql_url, oauth: SwanOAuthClient, *, http_client: httpx.AsyncClient | None = None)`. Methods:
  - `async query(query_str, variables=None, operation_name=None) -> dict` — POSTs `{query, variables, operationName}` with `Authorization: Bearer <token>`; on 401, calls `oauth.invalidate()` and retries once; on `errors` field in response, raises `SwanGraphQLError(errors)`; returns `body["data"]`
  - `async fetch_transaction(tx_id) -> dict` — runs the canonical `transaction(id)` query (Phase 2 reference §D); returns `result["transaction"]` or raises if null
  - `async fetch_account(account_id) -> dict` — runs the canonical `account(id)` query; returns `result["account"]`
  - `def handle_mutation_result(payload, expected_success_type)` — module-level helper; pattern-matches on `__typename`; returns success payload or raises `SwanRejectionError(message, fields)` for the various rejection types
- **PATTERN**: Phase 2 reference §D; `SWAN_API_REFERENCE.md:421-458`, `:584-601`, `:632-640`.
- **IMPORTS**: `httpx`, `json`, `typing`.
- **GOTCHA**: Mutation errors come back as **union members in the response data**, NOT in the GraphQL `errors` field. The `errors` field is for system faults (auth, rate limit, malformed query). The `handle_mutation_result` helper is for the data layer. Do not conflate.
- **GOTCHA**: GraphQL queries are multi-line strings. Embed them as Python string literals; do not try to load from `.graphql` files in MVP.
- **VALIDATE**: `python -m pytest backend/tests/test_swan_graphql.py -v`.

### Task 7 — CREATE `backend/tests/test_swan_graphql.py`

- **IMPLEMENT**: Tests for: 200 response with `data` returns dict; 200 with `errors` raises `SwanGraphQLError`; 401 → invalidate + retry once → success on retry; mutation-error union helper handles `ValidationRejection`, generic `Rejection`, success types; raises on unknown `__typename`.
- **PATTERN**: Same `httpx.MockTransport` shape as Task 5.
- **IMPORTS**: As Task 5; the new exception classes from `swan/graphql.py`.
- **GOTCHA**: Test the retry-on-401 path explicitly. The first response should be 401, the second 200; assert the test's fake transport saw two requests.
- **VALIDATE**: `python -m pytest backend/tests/test_swan_graphql.py -v`.

### Task 8 — CREATE `backend/ingress/__init__.py` and `backend/ingress/routing.yaml`

- **IMPLEMENT**: `routing.yaml` mirrors `RealMetaPRD §7.1`:
  ```yaml
  routes:
    swan.Transaction.Booked:    [transaction_booked]
    swan.Transaction.Released:  [transaction_released]
    swan.Transaction.Canceled:  [transaction_released]
    swan.Transaction.Enriched:  [transaction_reclassify]   # placeholder; pipeline not built in MVP
    swan.Card.Created:          [card_lifecycle]            # placeholder
    swan.Account.Updated:       [reconcile_balance]         # placeholder
    document.uploaded:          [document_ingested]
    external.crm.invoice_paid:  [external_event]
    external.shop.order_paid:   [external_event]
  defaults:
    unknown_event: [log_and_continue]                       # placeholder pipeline; emits a single event
  ```
  Plus a tiny loader: `backend/ingress/__init__.py` exports `load_routing(path) -> dict[str, list[str]]` and `defaults(path) -> list[str]`.
- **PATTERN**: `RealMetaPRD §7.1`; `yaml_loader.parse` style for strict-key validation.
- **IMPORTS**: `yaml`, `pathlib.Path`.
- **GOTCHA**: The placeholder pipelines (`transaction_reclassify`, `card_lifecycle`, `reconcile_balance`, `log_and_continue`) are referenced but not built in MVP. The router's `dispatch` should look up `routing.yaml`; if the pipeline file does not exist, log a warning and treat as `defaults.unknown_event`. **Do not 500.**
- **VALIDATE**:
  ```bash
  python -c "from backend.ingress import load_routing; print(len(load_routing('backend/ingress/routing.yaml')['routes']))"
  ```

### Task 9 — CREATE `backend/api/swan_webhook.py`

- **IMPLEMENT**: `router = APIRouter(prefix="/swan")`; `@router.post("/webhook")` async handler:
  1. Read `x-swan-secret` header; constant-time compare (`hmac.compare_digest`) against `os.environ["SWAN_WEBHOOK_SECRET"]`. Mismatch → 401.
  2. Read body as JSON. Validate envelope shape (`eventType`, `eventId`, `eventDate`, `projectId`, `resourceId`).
  3. `INSERT OR IGNORE` into `orchestration.external_events` with `provider='swan'`, `event_id=eventId`, `event_type=eventType`, `resource_id=resourceId`, `payload=<envelope JSON>`. Use `write_tx`.
  4. If the row was a duplicate (`changes() == 0`), return `{"status": "duplicate"}` with 200. Do not trigger.
  5. Resolve `employee_id` via Swan account lookup: `swan_query.fetch_account(resourceId)` is the lazy way; faster path is to maintain a `swan_accounts` view in `accounting.db` (Phase 2.J seeds it) and read `employees.swan_account_id`. Pick the latter for MVP performance; fall back to GraphQL only on miss.
  6. Look up `eventType` in `routing.yaml`. For each pipeline name, call `execute_pipeline(name, trigger_source=f"swan.{eventType}", trigger_payload=envelope, store=store, employee_id=employee_id)`.
  7. Return `{"status": "ok", "run_ids": [...]}` with 200.
- **PATTERN**: `RealMetaPRD §7.1`; `executor.execute_pipeline` signature.
- **IMPORTS**: `fastapi.APIRouter`, `fastapi.Request`, `fastapi.HTTPException`, `os`, `hmac`, `json`, `aiosqlite`, `backend.orchestration.executor.execute_pipeline`, `backend.orchestration.store.writes.write_tx`, `backend.ingress`.
- **GOTCHA**: Verify signature **before** parsing body to JSON, lest a malformed body (rejected by Pydantic) bypasses signature check. Use `await request.body()` for the raw bytes.
- **GOTCHA**: 5s SLA. The handler must return within ~50ms target / 10s ceiling. The pipeline runs in the background (`execute_pipeline(..., background=True)` is the default). Do not `await` on the run.
- **GOTCHA**: Employee resolution must not crash on a company-account transaction. NULL is a legitimate value. The wedge query handles NULL via `WHERE employee_id IS NOT NULL`.
- **VALIDATE**: `python -m pytest backend/tests/test_swan_webhook.py -v`.

### Task 10 — CREATE `backend/tests/test_swan_webhook.py`

- **IMPLEMENT**: Cases: valid signature + new event_id → 200, `pipeline_runs` row created with `employee_id_logical` set; valid + duplicate event_id → 200 + `{status: 'duplicate'}` + no new run; bad signature → 401; missing envelope field → 400; unknown event type → 200 + `defaults.unknown_event` route fired.
- **PATTERN**: `backend/tests/test_executor.py` for fixture style; FastAPI's `TestClient` for HTTP.
- **IMPORTS**: `pytest`, `pytest_asyncio`, `httpx.AsyncClient` or `fastapi.testclient.TestClient`, fixture `store_handles` from `conftest.py`.
- **GOTCHA**: Tests must seed `audit.employees` with a known `swan_account_id` so resolution works. Add to `conftest.py` if not already there.
- **VALIDATE**: `python -m pytest backend/tests/test_swan_webhook.py -v`.

### Task 11 — CREATE `backend/api/external_webhook.py`

- **IMPLEMENT**: `router = APIRouter(prefix="/external")`; `_VERIFIER_REGISTRY: dict[str, Callable[[bytes, dict[str, str], str], tuple[bool, str | None]]]` — each verifier takes raw body bytes, headers, and the provider's secret; returns `(is_valid, normalized_event_id)`. Implement Stripe HMAC-SHA256 (signature in `Stripe-Signature` header; `t=<timestamp>,v1=<signature>` format; HMAC over `t.body`). `@router.post("/webhook/{provider}")` handler:
  1. Look up verifier by `provider`. Unknown provider → 404.
  2. Read raw body. Look up secret from env (`STRIPE_WEBHOOK_SECRET` etc.).
  3. Verify; if invalid, 401.
  4. Insert into `external_events` with `(provider, event_id)`. On duplicate, 200 + `{status: 'duplicate'}`.
  5. Look up `external.{provider}.{event_type}` in `routing.yaml` → fall back to `defaults.unknown_event`. Trigger pipeline.
  6. 200.
- **PATTERN**: `RealMetaPRD §7.2`; mirror the Swan handler shape but parameterize the verifier and secret.
- **IMPORTS**: `hmac`, `hashlib`, plus the same imports as Task 9.
- **GOTCHA**: Verify-before-parse, same as Swan. The `event_id` field name varies by provider; the verifier returns it alongside the boolean.
- **GOTCHA**: Stripe's signature header has **multiple** `v1=` entries (key rotation grace). Compare against each.
- **GOTCHA**: This is the routing-claim path; **do NOT wire a live Stripe account**. The pitch frame is "any provider lands here." Tests use a fake provider.
- **VALIDATE**: `python -m pytest backend/tests/test_external_webhook.py -v`.

### Task 12 — CREATE `backend/tests/test_external_webhook.py`

- **IMPLEMENT**: Cases: valid Stripe HMAC → 200 + run; tampered body → 401; unknown provider → 404; duplicate event_id → 200 + duplicate; valid + unknown event_type → 200 + default route fired; constant-time compare path is the only one used.
- **PATTERN**: As Task 10.
- **IMPORTS**: As Task 10.
- **GOTCHA**: Build the Stripe signature in the test fixture so you have a known-valid example. Use `hmac.new(secret, f"{ts}.{body}".encode(), hashlib.sha256).hexdigest()`.
- **VALIDATE**: `python -m pytest backend/tests/test_external_webhook.py -v`.

### Task 13 — CREATE migration `0002_chart_of_accounts.py` (accounting)

- **IMPLEMENT**: `def up(conn)`: INSERT 13 rows into `chart_of_accounts` per `RealMetaPRD §15.3`. Use `INSERT OR IGNORE` (idempotent re-runs).
- **PATTERN**: `backend/orchestration/store/migrations/audit/0002_seed_employees.py`.
- **IMPORTS**: `aiosqlite` is already wired by the migration runner; the migration body is plain SQL via `conn.execute`.
- **GOTCHA**: PCG codes are TEXT, not INTEGER. `'706000'`, not `706000`.
- **GOTCHA**: Chart hierarchy: `'4456'` parent is `'445'`; `'626100'` parent is `'626'` (which you must seed first as a category-only row, or set `parent=NULL` for MVP).
- **VALIDATE**:
  ```bash
  python -c "import asyncio; from pathlib import Path; from backend.orchestration.store.bootstrap import open_dbs; \
  asyncio.run((lambda: (s := open_dbs(Path('./data'), run_migrations=True)) and None)())" && \
  sqlite3 data/accounting.db "SELECT COUNT(*) FROM chart_of_accounts;"   # expect ≥13
  ```

### Task 14 — CREATE migration `0003_account_rules.py` (accounting)

- **IMPLEMENT**: Seed deterministic rules per `RealMetaPRD §15.2` counterparties: `(pattern_kind='counterparty', pattern_value='Anthropic', gl_account='626100', precedence=10, source='config')`, `Notion → 626200`, `OFI → 626200`, `Boulangerie Paul → 6257`, `SNCF → 624`, `Linear → 626200`, `Fin → 613`, `OpenAI → 626100` (added even if not in seed counterparties). Plus MCC fallbacks: `(pattern_kind='mcc', pattern_value='5814', gl_account='6257')` (food), `'5811' → '6257'`, `'4112' → '624'` (rail), `'7011' → '624'` (lodging).
- **PATTERN**: As Task 13.
- **IMPORTS**: As Task 13.
- **GOTCHA**: `precedence` ordering: counterparty rules > MCC rules > generic. Lower precedence number wins; choose 10 for counterparty, 50 for MCC.
- **VALIDATE**: `sqlite3 data/accounting.db "SELECT COUNT(*) FROM account_rules;"` expect ≥10.

### Task 15 — CREATE migration `0004_envelope_category_on_counterparties.py` (accounting)

- **IMPLEMENT**: `ALTER TABLE counterparties ADD COLUMN envelope_category TEXT`. Seed envelope_category for the demo set (Anthropic/OpenAI → `'ai_tokens'`, Notion/Linear → `'saas'`, Boulangerie Paul → `'food'`, SNCF/Airbnb-style → `'travel'`, Fin → `'leasing'`, OFI → `'saas'`).
- **PATTERN**: As Task 13.
- **IMPORTS**: As Task 13.
- **GOTCHA**: `STRICT` tables in SQLite require the column type. Use `TEXT`. Default NULL is fine; the resolver fills it in.
- **GOTCHA**: SQLite `ALTER TABLE ADD COLUMN` is fine on STRICT tables but cannot add `NOT NULL` without a default. Leave it nullable; treat NULL as `'uncategorized'` in the lookup.
- **VALIDATE**: `sqlite3 data/accounting.db "PRAGMA table_info(counterparties);"` should list `envelope_category`.

### Task 16 — CREATE migration `0005_demo_counterparties.py` (accounting)

- **IMPLEMENT**: INSERT 10 counterparties matching `RealMetaPRD §15.2` plus the rules in Task 14: Anthropic, Notion, OFI, Boulangerie Paul, SNCF, an Airbnb-style travel vendor (call it "Hostelo"), Linear, Fin (leasing), plus 5 customer rows with virtual IBANs (`Acme SAS`, `Beta GmbH`, `Gamma Ltd`, etc.). Also INSERT into `counterparty_identifiers`: each supplier gets one IBAN row (`source='config'`, `confidence=1.0`); each customer gets one virtual-IBAN row.
- **PATTERN**: As Task 13.
- **IMPORTS**: As Task 13.
- **GOTCHA**: IBAN format must validate IBAN-checksum-correctly if you want to test downstream IBAN-extraction. Use `FR76 1027 8060 6100 0102 0480 110` style (but generated; don't use a real one). For demo speed, keep them syntactically plausible.
- **VALIDATE**: `sqlite3 data/accounting.db "SELECT COUNT(*) FROM counterparties;"` expect ≥10.

### Task 17 — CREATE migration `0006_demo_swan_transactions.py` (accounting)

- **IMPLEMENT**: Generate ~200 `swan_transactions` rows, deterministic via `random.seed(42)`. Distribution: 30% `CardOutDebit` (counterparty: a supplier; mcc populated), 20% `SepaCreditTransferIn` (counterparty: a customer), 30% `SepaCreditTransferOut` (counterparty: Anthropic/Notion/OFI/Fin), 10% `Fees`, 10% `InternalCreditTransfer`. Across 12 months from 2025-04 to 2026-04. Ensure each transaction has `swan_event_id` (UUID), `amount_cents`, `execution_date`, `booked_balance_after`, `raw` (JSON envelope).
- **PATTERN**: As Task 13.
- **IMPORTS**: `random`, `uuid`, `json`, `datetime`.
- **GOTCHA**: `booked_balance_after` must be monotonically derivable from a starting balance and the sequence of debits/credits, otherwise the §7.6 invariant 2 will fail when the seed data is queried. Track running balance per Swan account.
- **GOTCHA**: Currency is `'EUR'` enforced by CHECK constraint.
- **VALIDATE**: `sqlite3 data/accounting.db "SELECT COUNT(*) FROM swan_transactions;"` expect ~200.

### Task 18 — CREATE migration `0007_budget_envelopes.py` (accounting)

- **IMPLEMENT**: For each of the three demo employees (Tim id=1, Marie id=2, Paul id=3) and the company (scope_id=NULL), create envelopes for the last three months (2026-02, 2026-03, 2026-04) across categories `food`, `travel`, `saas`, `ai_tokens`, `leasing`. Caps: `ai_tokens=20000_00` (€200), `food=15000_00`, `travel=50000_00`, `saas=30000_00`, `leasing=80000_00`. Soft threshold 80%.
- **PATTERN**: As Task 13.
- **IMPORTS**: As Task 13.
- **GOTCHA**: Period format is `'YYYY-MM'` (ISO month). Always 7 chars.
- **VALIDATE**: `sqlite3 data/accounting.db "SELECT COUNT(*) FROM budget_envelopes;"` expect 60 (4 scopes × 5 categories × 3 months).

### Task 19 — CREATE migration `0003_seed_swan_links.py` (audit)

- **IMPLEMENT**: UPDATE `audit.employees` for the three seeded employees: set `swan_iban` and `swan_account_id` to values matching the data seeded in `accounting.0006_demo_swan_transactions.py`. Do NOT introduce a fourth employee; the existing `audit.0002_seed_employees.py` already creates Tim, Marie, Paul.
- **PATTERN**: `audit.0002_seed_employees.py`.
- **IMPORTS**: As Task 13.
- **GOTCHA**: This migration must run after the accounting seed creates the synthetic Swan accounts, but the migration runner runs each DB independently. Use deterministic ID generation (e.g., the IBAN values are hard-coded constants shared between the two migrations).
- **VALIDATE**: `sqlite3 data/audit.db "SELECT email, swan_iban, swan_account_id FROM employees;"` shows three rows with non-NULL `swan_*`.

### Task 20 — CREATE `backend/orchestration/tools/swan_query.py`

- **IMPLEMENT**: Two tool functions:
  - `async def fetch_transaction(ctx)` — reads `tx_id = ctx.trigger_payload['resourceId']`; calls `SwanGraphQLClient.fetch_transaction(tx_id)`; persists the result into `accounting.swan_transactions` (`INSERT OR REPLACE` keyed on `id`); returns the dict
  - `async def fetch_account(ctx)` — reads `account_id = ctx.get('fetch-transaction').account.id`; calls `fetch_account`; returns the dict
- **PATTERN**: Phase 1 `tools/noop.py`; the tool reads from `ctx`, returns a dict, the executor handles caching/events.
- **IMPORTS**: `backend.orchestration.swan.graphql.SwanGraphQLClient`, `backend.orchestration.swan.oauth.SwanOAuthClient`, `os` for env. Build the client lazily; cache as a module-level singleton.
- **GOTCHA**: This tool's `cacheable: false` in YAML — always re-fetch the canonical state from Swan. Stale cache on a transaction state change kills the demo.
- **GOTCHA**: Persist into `swan_transactions` inside `write_tx(ctx.store.accounting, ctx.store.accounting_lock)` — this is a **write** even though the tool feels like a read.
- **VALIDATE**: `python -m pytest backend/tests/test_swan_query_tool.py -v` (a small test that mocks `SwanGraphQLClient` and asserts the row is inserted).

### Task 21 — REGISTER swan_query tools in `registries.py`

- **IMPLEMENT**: Call `register_tool("tools.swan_query:fetch_transaction", "backend.orchestration.tools.swan_query:fetch_transaction")` (and `fetch_account`) at module bootstrap. The cleanest place is a `_register_production_tools()` function called from `backend/orchestration/__init__.py` or from `executor.py` once at boot.
- **PATTERN**: How `noop` is registered — confirm in `registries.py` and replicate.
- **IMPORTS**: `from .registries import register_tool`.
- **GOTCHA**: Registration is idempotent; Phase 1's registry should not error on re-register, but confirm.
- **VALIDATE**: `python -c "from backend.orchestration.registries import get_tool; print(get_tool('tools.swan_query:fetch_transaction'))"`.

### Task 22 — CREATE `backend/orchestration/tools/counterparty_resolver.py`

- **IMPLEMENT**: `async def run(ctx)`. Reads `tx = ctx.get('fetch-transaction')` (Swan path) or `extraction = ctx.get('extract')` (document path). Runs the four-stage cascade:
  1. **IBAN exact** — look up `counterparty_identifiers` where `identifier_type='iban' AND identifier=<extracted IBAN>`. Confidence 1.0.
  2. **Virtual IBAN** — for inbound only; same lookup against the credited IBAN. Confidence 1.0.
  3. **Merchant** — for card transactions: exact `merchantId` match; if miss, MCC + fuzzy on `counterparties.legal_name` (`rapidfuzz.fuzz.token_set_ratio ≥ 85`).
  4. **Fuzzy name** — `rapidfuzz.fuzz.token_set_ratio` on the counterparty label across all `counterparties` (limit 1000 by `updated_at DESC`). Threshold 85, confidence capped at `min(0.85, score/100)`.
  Cache writeback: on stages 1–4 success, INSERT into `counterparty_identifiers` with the identifier and the stage-named `source`. Returns `{counterparty_id, confidence, method}` or `{counterparty_id: None}` on miss.
- **PATTERN**: Phase 2 reference §E (the 4-stage cascade); `04_AGENT_PATTERNS.md:214-305`.
- **IMPORTS**: `rapidfuzz.fuzz`, `aiosqlite`, `backend.orchestration.store.writes.write_tx`.
- **GOTCHA**: For Swan transactions, the IBAN is on `creditor.iban` (debit side) or `debtor.iban` (credit side). For card transactions, no IBAN — skip stage 1 fast.
- **GOTCHA**: Cache writeback: do NOT insert if the identifier already exists (unique constraint). Use `INSERT OR IGNORE`.
- **GOTCHA**: Fuzzy stage limit 1000 by `updated_at DESC` keeps it fast; full-table scan on a hot path is a cost trap once the table grows past 10k rows.
- **VALIDATE**: `python -m pytest backend/tests/test_counterparty_resolver.py -v`.

### Task 23 — CREATE `backend/orchestration/agents/counterparty_classifier.py`

- **IMPLEMENT**: `async def run(ctx)` — Claude Sonnet 4.6; system prompt: "You classify a transaction's counterparty by selecting from a closed list of known counterparties." User content: serialized transaction or extraction summary + the closed list (top 20 candidates by recency). Tool: `submit_counterparty(counterparty_id: int, confidence: float, alternatives: list[{id, score}])`. The `anthropic_runner` forces this tool. Returns `AgentResult` with `output={counterparty_id, confidence, alternatives}`. The executor's `propose_checkpoint_commit` handles the audit row. Tool also writes back to `counterparty_identifiers` with `source='ai'`.
- **PATTERN**: Phase 1 `agents/noop_agent.py`; `04_AGENT_PATTERNS.md:90-107` (closed-list classifier shape); `RealMetaPRD §7.10` (`AgentResult` populated fields).
- **IMPORTS**: `from backend.orchestration.registries import get_runner`, `from backend.orchestration.runners.base import AgentResult`.
- **GOTCHA**: The closed list must include an explicit "none of the above" option (`counterparty_id=null`) so the model can refuse, lowering confidence. The PRD's §6.4 cache-warmer explicitly avoids forcing matches the model isn't sure about.
- **GOTCHA**: Cache writeback for AI hits: pin to the exact identifier the model saw (the merchant name, the IBAN). Future deterministic stages will then hit; that's the whole point.
- **VALIDATE**: `python -m pytest backend/tests/test_counterparty_classifier_agent.py -v`.

### Task 24 — CREATE `backend/orchestration/conditions/counterparty.py`

- **IMPLEMENT**: `def unresolved(ctx) -> bool`: `out = ctx.get("resolve-counterparty"); return out is None or out.get("counterparty_id") is None`.
- **PATTERN**: `backend/orchestration/conditions/gating.py`.
- **IMPORTS**: None beyond stdlib.
- **GOTCHA**: Conditions must be pure and unit-testable. No DB access.
- **VALIDATE**: `python -m pytest backend/tests/test_conditions.py::test_counterparty_unresolved -v`.

### Task 25 — CREATE `backend/orchestration/tools/gl_account_classifier.py`

- **IMPLEMENT**: `async def run(ctx)`. Reads counterparty_id (from `resolve-counterparty` or `ai-counterparty-fallback`). Looks up `account_rules` ordered by precedence ASC. Match logic: `pattern_kind='counterparty' AND pattern_value=<legal_name>` first; then `pattern_kind='mcc' AND pattern_value=<mcc>`; then `pattern_kind='counterparty_kind' AND pattern_value=<kind>`. First match wins; returns `{gl_account, confidence: 1.0, rule_id}`. Miss → returns `{gl_account: None}`.
- **PATTERN**: As Task 22 cascade structure.
- **IMPORTS**: `aiosqlite`.
- **GOTCHA**: Read-only; no `write_tx` needed.
- **VALIDATE**: `python -m pytest backend/tests/test_gl_classifier.py -v`.

### Task 26 — CREATE `backend/orchestration/agents/gl_account_classifier_agent.py`

- **IMPLEMENT**: `async def run(ctx)` — Claude Sonnet 4.6; system: "Classify a transaction or invoice line into a GL account from the closed chart of accounts."; tool: `submit_gl_account(gl_account: str, confidence: float, alternatives, vat_rate_bp: int | null)` constrained to existing `chart_of_accounts.code` enum (loaded at boot or query time). The constraint is via the tool's `enum` field on the input schema. Cache writeback: INSERT into `account_rules` with `pattern_kind='counterparty'`, `pattern_value=<counterparty.legal_name>`, `gl_account=<chosen>`, `precedence=20` (higher than seed=10 so it doesn't override config), `source='ai'`.
- **PATTERN**: As Task 23.
- **IMPORTS**: As Task 23.
- **GOTCHA**: The closed enum must come from the **current state** of `chart_of_accounts` at request time — not hardcoded — so adding a new GL code (e.g., `627` for bank fees) without restarting the app works. Read once per process and cache.
- **VALIDATE**: `python -m pytest backend/tests/test_gl_classifier_agent.py -v`.

### Task 27 — CREATE `backend/orchestration/conditions/gl.py`

- **IMPLEMENT**: `def unclassified(ctx) -> bool`: `out = ctx.get("classify-gl-account"); return out is None or out.get("gl_account") is None`.
- **PATTERN**: As Task 24.
- **IMPORTS**: None.
- **GOTCHA**: None.
- **VALIDATE**: `python -m pytest backend/tests/test_conditions.py::test_gl_unclassified -v`.

### Task 28 — CREATE `backend/orchestration/tools/journal_entry_builder.py`

- **IMPLEMENT**: Four functions:
  - `async def build_cash(ctx)` — reads `tx`, `counterparty`, `gl_account`, `accrual_link_id`. Dispatches on Swan transaction `type` to one of four patterns (card spend, SEPA-in, SEPA-out, fee). Returns `{lines: [{account_code, debit_cents, credit_cents, ...}], basis: 'cash', accrual_link_id, entry_date}`.
  - `async def build_accrual(ctx)` — reads extraction; emits `Dr Expense (gl_account) + Dr VAT-deductible (4456); Cr AP (401)`. Returns same shape with `basis='accrual'`.
  - `async def match_accrual(ctx)` — for SEPA-out + supplier: SELECT `journal_entries.id` FROM `journal_entries je JOIN journal_lines jl ...` WHERE `basis='accrual'`, `status='posted'`, counterparty matches, AP balance equals SEPA amount, and entry has no existing `accrual_link_id`. Returns `{accrual_link_id: <id>}` or `{}`.
  - `async def build_reversal(ctx)` — reads `original_entry_id` from `find-original-entry`; loads original `journal_lines`; flips Dr/Cr per line; returns `{lines, basis: <original.basis>, reversal_of_id: <original.id>, entry_date: <today>}`.
- **PATTERN**: Phase 2 reference §G.
- **IMPORTS**: `aiosqlite`, `decimal.Decimal` for VAT computation (then converted to int), `datetime`.
- **GOTCHA**: Integer cents only. `vat_cents = (subtotal_cents * vat_rate_bp + 5000) // 10000` (rounded half-up). Never `subtotal_cents * vat_rate_pct / 100`.
- **GOTCHA**: `build_reversal` must NOT just negate amounts. It must produce a new entry with debit and credit amounts SWAPPED on each line — so an Dr Expense / Cr Bank becomes Dr Bank / Cr Expense. The `debit_cents >= 0` and `credit_cents >= 0` check constraints would reject negatives.
- **GOTCHA**: For card spend, the bank account is `'512'`. For SEPA, also `'512'` for now (the demo has one bank account). When multiple Swan accounts ship, this becomes a per-account lookup.
- **VALIDATE**: `python -m pytest backend/tests/test_journal_entry_builder.py -v`.

### Task 29 — CREATE `backend/orchestration/tools/gl_poster.py`

- **IMPLEMENT**: `async def post(ctx)`. Reads the built entry from `ctx.get("build-cash-entry")` or `ctx.get("build-accrual-entry")` or `ctx.get("build-reversal")`. In one `write_tx`:
  1. Validate `SUM(debit_cents) == SUM(credit_cents)`. Mismatch → raise.
  2. INSERT `journal_entries` (basis, entry_date, description, source_pipeline=ctx.pipeline_name, source_run_id=ctx.run_id, status='posted', accrual_link_id, reversal_of_id).
  3. INSERT each line into `journal_lines`.
  4. INSERT one `decision_traces` row per line: `source` is `'agent'` if any agent contributed (look at `ctx.metadata.agent_decision_ids`), else `'rule'`; `confidence` from `gate-confidence`; `agent_decision_id_logical` from the contributing agent decision; `parent_event_id` from the trigger payload.
  Returns `{entry_id, status: 'posted'}`. Emits `ledger.entry_posted` event on the dashboard bus.
- **PATTERN**: `RealMetaPRD §6.4` (the chokepoint), `§7.5` (table shapes), `§7.6` invariant 1.
- **IMPORTS**: As Task 28; `event_bus.publish_event`.
- **GOTCHA**: This is the **single chokepoint** for journal writes. Other tools must not INSERT into `journal_entries` directly. The CI grep can enforce this: `grep "INSERT INTO journal_entries" backend/orchestration/tools/` should match exactly one file.
- **GOTCHA**: `decision_traces.source` needs careful attribution. Heuristic: walk `ctx.node_outputs` upstream of the post; if any node was an agent (look at the YAML's `is_agent` flag), the trace is `'agent'`. Else `'rule'`.
- **VALIDATE**: `python -m pytest backend/tests/test_gl_poster.py -v`.

### Task 30 — CREATE `backend/orchestration/tools/invariant_checker.py`

- **IMPLEMENT**: `async def run(ctx)`. Reads `entry_id` from `ctx.get("post-entry")`. Runs the five `RealMetaPRD §7.6` asserts:
  1. `SUM(debit_cents) == SUM(credit_cents)` for the entry.
  2. After cash-basis posts: `swan_transactions.booked_balance_after` for the same Swan account equals the GL bank-account balance computed from `journal_lines` (account_code='512', filtered by execution_date ≤ now).
  3. The entry has at least one `decision_traces` row.
  4. For accrual entries from a PDF: `documents.sha256` reachable through `journal_lines.document_id`.
  5. For paired entries: AP balance across the pair returns to zero.
  Returns `{ok: bool, failures: [...]}`. On failure, raise — the pipeline fail-fast triggers `pipeline_failed`.
- **PATTERN**: `RealMetaPRD §7.6`.
- **IMPORTS**: `aiosqlite`.
- **GOTCHA**: Read-only; no lock.
- **GOTCHA**: Invariant 2 is the most likely to surface a bug. If it fails on demo seed data, the seed generator (Task 17) has a balance-tracking bug — fix the seed, not the assert.
- **VALIDATE**: `python -m pytest backend/tests/test_invariants.py -v`.

### Task 31 — CREATE `backend/orchestration/tools/budget_envelope.py`

- **IMPLEMENT**: `async def decrement(ctx)`. Reads `entry_id`, `employee_id = ctx.employee_id`, `entry_date` from `post-entry`; reads `counterparty.envelope_category` from `resolve-counterparty`. Logic:
  1. `period = entry_date[:7]` (YYYY-MM).
  2. If `category == 'uncategorized'` or NULL: emit `envelope.skipped`, return `{skipped: True}`.
  3. Look up `budget_envelopes WHERE scope_kind='employee' AND scope_id=employee_id AND category=? AND period=?`. Miss → fall back to `scope_kind='company', scope_id IS NULL`. Miss again → emit `envelope.no_envelope`, return `{skipped: True}`.
  4. For each expense line of the entry (`account_code` starts with `6`): INSERT `budget_allocations(envelope_id, line_id, amount_cents=line.debit_cents)`. Use `write_tx`.
  5. INSERT one `decision_traces` row per allocation: `source='rule'`, `confidence=1.0`, `line_id=allocation.line_id`.
  6. Compute new used/cap: `SELECT SUM(amount_cents) FROM budget_allocations WHERE envelope_id=?` minus reversal allocations.
  7. Emit `envelope.decremented` on the dashboard bus: `{employee_id, category, period, used_cents, cap_cents, soft_threshold_pct, ledger_entry_id: entry_id}`.
  Returns `{envelope_id, allocations: [...], used_cents, cap_cents}`.
- **PATTERN**: `phase-1-critical-gap-remediation.md Phase 4`; `RealMetaPRD §7.5`.
- **IMPORTS**: `aiosqlite`, `event_bus.publish_event_dashboard` (new helper; see Task 51).
- **GOTCHA**: Period comes from `entry_date[:7]`, NOT `datetime.now()`. Backfilled or historical entries land in their historical month.
- **GOTCHA**: Reversal pipelines call this with **negative** amount_cents. Enforce by: `if entry.reversal_of_id is not None: amount_cents = -line.debit_cents`. The `budget_allocations.amount_cents` column is INTEGER NOT NULL; SQLite allows negatives. Confirm there's no CHECK preventing it (there isn't in the canonical schema).
- **GOTCHA**: Multi-line entries (e.g. accrual with VAT): allocate the *expense* line, not the VAT line. VAT is a balance-sheet item, not budget-relevant.
- **VALIDATE**: `python -m pytest backend/tests/test_budget_envelope.py -v`.

### Task 32 — CREATE `backend/orchestration/tools/confidence_gate.py`

- **IMPLEMENT**: `async def run(ctx)`. Walks `ctx.node_outputs` for nodes named in `["resolve-counterparty", "ai-counterparty-fallback", "classify-gl-account", "ai-account-fallback", "build-cash-entry", "build-accrual-entry", "extract", "validate"]` (use the actual node IDs of the running pipeline; pull from `ctx.pipeline_name` lookup or just walk all outputs). For each, extract `confidence` if present. Apply `compound_confidence([...])` (None → 0.5). Read floor from `confidence_thresholds WHERE scope='global'` (or scope=`pipeline:<name>`). Returns `{ok: bool, computed_confidence: float, contributing_factors: [(node_id, confidence), ...], floor: float}`.
- **PATTERN**: `04_AGENT_PATTERNS.md:132-146` (compound_confidence shape).
- **IMPORTS**: `aiosqlite` for the threshold read.
- **GOTCHA**: Multiplicative — even one 0.0 collapses the chain. If a stage explicitly fails (counterparty unresolved → `confidence=0.0`), that's correct: the entry should land in review.
- **GOTCHA**: Missing `confidence` (e.g., a deterministic builder that doesn't emit one) is `None` → `0.5`. **Do not skip** missing factors; they are signals.
- **VALIDATE**: `python -m pytest backend/tests/test_confidence_gate.py -v`.

### Task 33 — CREATE `backend/orchestration/tools/review_queue.py` and the `review_queue` table

- **IMPLEMENT**:
  - Migration `0008_review_queue.py` (accounting): `CREATE TABLE review_queue (id INTEGER PRIMARY KEY, entry_id INTEGER REFERENCES journal_entries(id), kind TEXT NOT NULL, confidence REAL, reason TEXT, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, resolved_at TEXT, resolved_by INTEGER) STRICT;`
  - Tool `review_queue.py:enqueue(ctx)`: reads `entry_id` (might be NULL if the entry was never built), `kind` (`'low_confidence'`, `'invariant_failure'`, `'totals_mismatch'`), `reason`. INSERT. Emits `review.enqueued` on dashboard bus. Returns `{review_id}`.
- **PATTERN**: As Task 31.
- **IMPORTS**: As Task 31.
- **GOTCHA**: The review queue can hold entries where `entry_id IS NULL` (the build node never ran). Allow nullable.
- **VALIDATE**: `python -m pytest backend/tests/test_review_queue.py -v`.

### Task 34 — UPDATE `backend/orchestration/conditions/gating.py` — fill in stubs

- **IMPLEMENT**: Replace the three stubs:
  - `def passes_confidence(ctx)`: `out = ctx.get("gate-confidence"); return bool(out and out.get("ok"))`
  - `def needs_review(ctx)`: `out = ctx.get("gate-confidence"); return bool(out) and not out.get("ok")`
  - `def posted(ctx)`: `out = ctx.get("post-entry"); return bool(out) and out.get("status") == "posted"`
- **PATTERN**: The existing function shapes; do not rename, do not add async.
- **IMPORTS**: None.
- **GOTCHA**: `posted` reads from `post-entry`; if the pipeline names this node differently (e.g. `post`), the condition won't fire. Standardize node naming across pipelines: `post-entry` everywhere.
- **VALIDATE**: `python -m pytest backend/tests/test_conditions.py -v`.

### Task 35 — CREATE `backend/orchestration/conditions/documents.py`

- **IMPLEMENT**: `def totals_ok(ctx)`: `out = ctx.get("validate"); return bool(out) and out.get("ok")`. `def totals_mismatch(ctx)`: `not totals_ok(ctx)`.
- **PATTERN**: As Task 34.
- **IMPORTS**: None.
- **GOTCHA**: None.
- **VALIDATE**: `python -m pytest backend/tests/test_conditions.py -v`.

### Task 36 — REGISTER all production tools / agents / conditions in `registries.py`

- **IMPLEMENT**: Extend the bootstrap-time registration to include every new tool, agent, and condition created in Tasks 20–35. Suggest a `_register_production()` function in `backend/orchestration/__init__.py` called once from `api/main.py`'s lifespan or from `executor` first import.
- **PATTERN**: Existing registration pattern.
- **IMPORTS**: As required.
- **GOTCHA**: If you forget to register, the YAML loader's strict-key validation passes (`tool: tools.foo:run` is just a string) but `executor._dispatch_tool` raises `KeyError` at runtime. Test by parsing each pipeline at boot.
- **VALIDATE**:
  ```bash
  python -c "
  import yaml
  from pathlib import Path
  from backend.orchestration import _register_production
  from backend.orchestration.registries import get_tool, get_agent, get_condition
  _register_production()
  for f in Path('backend/orchestration/pipelines').glob('*.yaml'):
      doc = yaml.safe_load(f.read_text())
      for n in doc['nodes']:
          if 'tool' in n: get_tool(n['tool'])
          if 'agent' in n: get_agent(n['agent'])
          if 'when' in n: get_condition(n['when'])
  print('OK: every pipeline node resolves')
  "
  ```

### Task 37 — CREATE `backend/orchestration/pipelines/transaction_booked.yaml`

- **IMPLEMENT**: Verbatim from `RealMetaPRD §7.4` (including the `decrement-envelope` node from `phase-1-critical-gap-remediation.md Phase 4`). Strict structure:
  ```yaml
  name: transaction_booked
  version: 1
  trigger: { source: external_event:swan.Transaction.Booked }
  nodes:
    - { id: fetch-transaction,        tool: tools.swan_query:fetch_transaction,             cacheable: false }
    - { id: resolve-counterparty,     tool: tools.counterparty_resolver:run,                depends_on: [fetch-transaction], cacheable: true }
    - { id: ai-counterparty-fallback, agent: agents.counterparty_classifier:run, runner: anthropic, depends_on: [resolve-counterparty], when: conditions.counterparty:unresolved }
    - { id: classify-gl-account,      tool: tools.gl_account_classifier:run,                depends_on: [resolve-counterparty, ai-counterparty-fallback], cacheable: true }
    - { id: ai-account-fallback,      agent: agents.gl_account_classifier_agent:run, runner: anthropic, depends_on: [classify-gl-account], when: conditions.gl:unclassified }
    - { id: match-accrual,            tool: tools.journal_entry_builder:match_accrual,      depends_on: [classify-gl-account, ai-account-fallback] }
    - { id: build-cash-entry,         tool: tools.journal_entry_builder:build_cash,         depends_on: [match-accrual] }
    - { id: gate-confidence,          tool: tools.confidence_gate:run,                      depends_on: [build-cash-entry] }
    - { id: post-entry,               tool: tools.gl_poster:post,                           depends_on: [build-cash-entry, gate-confidence], when: conditions.gating:passes_confidence }
    - { id: queue-review,             tool: tools.review_queue:enqueue,                     depends_on: [build-cash-entry, gate-confidence], when: conditions.gating:needs_review }
    - { id: assert-balance,           tool: tools.invariant_checker:run,                    depends_on: [post-entry], when: conditions.gating:posted }
    - { id: decrement-envelope,       tool: tools.budget_envelope:decrement,                depends_on: [post-entry], when: conditions.gating:posted }
  ```
- **PATTERN**: `RealMetaPRD §7.4`.
- **IMPORTS**: N/A.
- **GOTCHA**: Strict-key validation in `yaml_loader` rejects unknown fields. Stick to the schema.
- **VALIDATE**: `python -c "from backend.orchestration.yaml_loader import load; p = load('backend/orchestration/pipelines/transaction_booked.yaml'); print(len(p.nodes))"` expect 12.

### Task 38 — CREATE `backend/orchestration/pipelines/transaction_released.yaml`

- **IMPLEMENT**: Compensation pipeline:
  ```yaml
  name: transaction_released
  version: 1
  trigger: { source: external_event:swan.Transaction.Released }
  nodes:
    - { id: fetch-transaction,           tool: tools.swan_query:fetch_transaction,           cacheable: false }
    - { id: find-original-entry,         tool: tools.journal_entry_builder:find_original,    depends_on: [fetch-transaction] }
    - { id: build-reversal,              tool: tools.journal_entry_builder:build_reversal,   depends_on: [find-original-entry] }
    - { id: gate-confidence,             tool: tools.confidence_gate:run,                    depends_on: [build-reversal] }
    - { id: post-entry,                  tool: tools.gl_poster:post,                         depends_on: [build-reversal, gate-confidence], when: conditions.gating:passes_confidence }
    - { id: mark-original-reversed,      tool: tools.journal_entry_builder:mark_reversed,    depends_on: [post-entry], when: conditions.gating:posted }
    - { id: assert-balance,              tool: tools.invariant_checker:run,                  depends_on: [mark-original-reversed], when: conditions.gating:posted }
    - { id: decrement-envelope-reversal, tool: tools.budget_envelope:decrement,              depends_on: [post-entry], when: conditions.gating:posted }
  ```
  Add `find_original` and `mark_reversed` as small helpers in `journal_entry_builder.py`. `find_original` short-circuits returning `{skip: True, reason: 'already_reversed'}` if the original is already `'reversed'`; `gl_poster.post` honors that.
- **PATTERN**: `phase-1-critical-gap-remediation.md Phase 5`.
- **IMPORTS**: N/A.
- **GOTCHA**: Idempotency. `Released` arriving twice must not double-reverse. The `(provider, event_id)` constraint catches webhook redelivery; the `find_original` short-circuit catches manual replays.
- **VALIDATE**: As Task 37.

### Task 39 — CREATE `backend/orchestration/pipelines/document_ingested.yaml`

- **IMPLEMENT**: Verbatim from `RealMetaPRD §7.3` (already specified there). Add `decrement-envelope` after `assert-balance`. 13 nodes total.
- **PATTERN**: `RealMetaPRD §7.3`.
- **IMPORTS**: N/A.
- **GOTCHA**: The `extract` node uses `agent`, not `tool`. The runner is `anthropic`. `cacheable: false` because PDF extraction is expensive and rare; the SHA256 idempotency at the upload layer prevents re-extraction.
- **VALIDATE**: As Task 37.

### Task 40 — CREATE `backend/orchestration/pipelines/external_event.yaml`

- **IMPLEMENT**: Stub:
  ```yaml
  name: external_event
  version: 1
  trigger: { source: external_event:external.* }
  nodes:
    - { id: parse-payload, tool: tools.external_payload_parser:run }
    - { id: log-and-complete, tool: tools.review_queue:enqueue, depends_on: [parse-payload] }
  ```
  And a `log_and_continue.yaml` (one-node pipeline that just emits an event) for the unknown_event default.
- **PATTERN**: As Task 37.
- **IMPORTS**: N/A.
- **GOTCHA**: This is a stub. Don't over-engineer; the routing-claim only needs end-to-end "webhook → run → completed."
- **VALIDATE**: As Task 37.

### Task 41 — CREATE `backend/orchestration/tools/external_payload_parser.py`

- **IMPLEMENT**: `async def run(ctx)`. Reads `ctx.trigger_payload`. Provider-aware shim: switch on `provider`; for Stripe, extract `event_type`, `payment_intent_id`, `amount`. Returns the normalized dict. Optionally INSERTs an `expected_payments` row with `direction='inbound'`, `status='open'`. Returns `{normalized: {...}, expected_payment_id?: int}`.
- **PATTERN**: As Task 22.
- **IMPORTS**: `aiosqlite`, `write_tx`.
- **GOTCHA**: The Stripe payload shape is well-known; consult Stripe docs once. The Shopify shape is different. Handling both well is a Phase 3 task; for MVP, parse Stripe and log everything else.
- **VALIDATE**: `python -m pytest backend/tests/test_external_payload_parser.py -v`.

### Task 42 — CREATE `backend/api/documents.py`

- **IMPLEMENT**: `router = APIRouter(prefix="/documents")`; `@router.post("/upload")` with `UploadFile` and optional `employee_id: int = Form(default=None)`:
  1. Read bytes; compute SHA256.
  2. Save to `data/blobs/<sha256>` (idempotent: skip write if exists).
  3. `INSERT OR IGNORE` into `accounting.documents` keyed on `sha256` (`kind='invoice_in'`, `direction='inbound'`, `employee_id`, `blob_path=data/blobs/<sha256>`). On duplicate, `SELECT id` and return existing.
  4. If new, `execute_pipeline('document_ingested', trigger_source='document.uploaded', trigger_payload={'document_id': ..., 'sha256': ...}, store=store, employee_id=employee_id)`.
  5. Return `{document_id, sha256, run_id, stream_url: f"/runs/{run_id}/stream"}`.
- **PATTERN**: `RealMetaPRD §7.3`, §10.
- **IMPORTS**: `fastapi.UploadFile`, `fastapi.File`, `fastapi.Form`, `hashlib`, `pathlib.Path`.
- **GOTCHA**: `data/blobs/` must exist on boot. Create on lifespan startup.
- **GOTCHA**: Large PDF (>25MB) — set FastAPI's max upload to 50MB explicitly. `await file.read()` reads everything; for MVP that's fine.
- **GOTCHA**: Idempotent uploads need to return the **most recent** `run_id`, not a new one. `SELECT MAX(run_id) FROM pipeline_runs WHERE pipeline_name='document_ingested' AND json_extract(trigger_payload, '$.document_id') = ?`.
- **VALIDATE**: `python -m pytest backend/tests/test_document_upload.py -v`.

### Task 43 — CREATE `backend/orchestration/agents/document_extractor.py`

- **IMPLEMENT**: `async def run(ctx)` — reads `document_id` from `ctx.trigger_payload`; loads the PDF blob bytes; base64-encodes. Calls Claude (Sonnet 4.6 first; Opus 4.7 reserved for Phase 3) via the Anthropic runner with `max_tokens=2000`, `timeout=15.0` override (per `§7.9` doc-extractor exception). System prompt: "Extract structured invoice data; convert all monetary amounts to integer cents; validate sums; respond via the `submit_invoice` tool only." User content: a `text` block + a `document` block with the base64 PDF. The single tool `submit_invoice` has the strict schema from `RealMetaPRD §7.3` (supplier_name, invoice_number, date, due_date, items[], subtotal_cents, vat_percent, vat_cents, total_cents, currency, confidence). Returns `AgentResult` with `output={...extraction...}`.
- **PATTERN**: Phase 2 reference §H; `04_AGENT_PATTERNS.md:90-107`.
- **IMPORTS**: `from backend.orchestration.registries import get_runner`; `base64`; `aiosqlite`.
- **GOTCHA**: The runner already enforces `submit_*` tool forcing. The runner's default `deadline_s=4.5` must be overridden via the `deadline_s=15.0` argument when calling `runner.run(...)` from this agent. Confirm the runner accepts the kwarg (it does, per Phase 1 audit).
- **GOTCHA**: VAT-percent validation. `vat_cents` must equal `(subtotal_cents * vat_percent * 100) // 10000` — within ±1 cent for rounding. Tighter validation in Task 44 (`validate_totals`).
- **GOTCHA**: PDF blob loading: read sync from disk via `aiofiles.open` to avoid blocking the event loop.
- **VALIDATE**: `python -m pytest backend/tests/test_document_extractor.py -v` (mock the Anthropic client).

### Task 44 — CREATE `backend/orchestration/tools/document_extractor.py:validate_totals`

- **IMPLEMENT**: `async def validate_totals(ctx)`. Reads `extraction = ctx.get("extract").output`. Asserts:
  1. `sum(item['amount_cents'] for item in items) == subtotal_cents`
  2. `subtotal_cents + vat_cents == total_cents`
  3. `currency == 'EUR'`
  Returns `{ok: bool, errors: [...]}`.
- **PATTERN**: Phase 2 reference §H.
- **IMPORTS**: None beyond stdlib.
- **GOTCHA**: ±1 cent tolerance on the sum check is acceptable for VAT rounding; keep it strict for line-item sum.
- **VALIDATE**: `python -m pytest backend/tests/test_validate_totals.py -v`.

### Task 45 — CREATE `backend/api/runs.py`

- **IMPLEMENT**: Router with five endpoints per `RealMetaPRD §10`:
  - `POST /pipelines/run/{name}` — manual trigger.
  - `GET /runs/{run_id}` — full reconstruction (joined view).
  - `GET /runs/{run_id}/stream` — SSE per-run.
  - `GET /journal_entries/{id}/trace` — drilldown.
  - `POST /review/{entry_id}/approve` — review approval.
- **PATTERN**: `RealMetaPRD §10`.
- **IMPORTS**: `fastapi.APIRouter`, `fastapi.responses.StreamingResponse`, `event_bus.subscribe`, `aiosqlite`.
- **GOTCHA**: SSE headers: `Cache-Control: no-cache`, `X-Accel-Buffering: no` (disable nginx buffering). `Connection: keep-alive` is implicit.
- **GOTCHA**: The `GET /runs/{id}/stream` route holds the connection open — limit the per-process file descriptor budget. For MVP `--workers 1`, this is fine; documented in `§9.5`.
- **GOTCHA**: `POST /review/{id}/approve` must run in a `write_tx` (UPDATE entry status, UPDATE trace approver, INSERT envelope allocation) and re-emit `ledger.entry_posted` + `envelope.decremented` on the dashboard bus.
- **VALIDATE**: `python -m pytest backend/tests/test_runs_api.py -v`.

### Task 46 — CREATE `backend/api/dashboard.py`

- **IMPLEMENT**: Router with `GET /dashboard/stream` that subscribes to a top-level dashboard bus. The dashboard bus is a singleton `asyncio.Queue`-fanout structure (see Task 51) keyed on the literal string `"dashboard"` instead of a run_id.
- **PATTERN**: As Task 45.
- **IMPORTS**: As Task 45.
- **GOTCHA**: Long-lived stream; never auto-closes. Heartbeat every 15s.
- **VALIDATE**: `python -m pytest backend/tests/test_dashboard_sse.py -v`.

### Task 47 — UPDATE `backend/api/main.py` — mount routers and create `data/blobs/`

- **IMPLEMENT**: In `lifespan`: create `data/blobs/` directory. Register all production tools/agents/conditions (call `_register_production`). Mount routers: `swan_webhook`, `external_webhook`, `documents`, `runs`, `dashboard`. Attach `store` and `dashboard_bus` to `app.state`.
- **PATTERN**: Existing lifespan in `main.py`.
- **IMPORTS**: As required.
- **GOTCHA**: Routers depend on `app.state.store` and `app.state.dashboard_bus`. Use FastAPI's `Depends` with a getter, or read from `request.app.state`.
- **VALIDATE**: `curl http://localhost:8000/healthz` still returns `{"status":"ok"}`; `curl -I http://localhost:8000/swan/webhook -X POST` returns `401` (no signature).

### Task 48 — UPDATE `backend/orchestration/event_bus.py` — add dashboard bus

- **IMPLEMENT**: Add `_DASHBOARD_BUS_KEY = "__dashboard__"` (or similar reserved key); `publish_event_dashboard(event)` is a thin wrapper for `publish_event(_DASHBOARD_BUS_KEY, event)`. `subscribe_dashboard()` is `subscribe(_DASHBOARD_BUS_KEY)`. The reaper TTL does NOT reap the dashboard bus (it's expected long-lived).
- **PATTERN**: Existing `event_bus.py`; the multi-fanout, backpressure-safe pattern is already there.
- **IMPORTS**: None new.
- **GOTCHA**: `publish_event` is `async`; the dashboard wrapper must be too. Tools that emit dashboard events use `await ctx.event_bus.publish_dashboard(...)` pattern.
- **VALIDATE**: `python -m pytest backend/tests/test_event_bus.py::test_dashboard_bus -v`.

### Task 49 — CREATE comprehensive tests `test_pipeline_transaction_booked.py`

- **IMPLEMENT**: End-to-end: fixture `Transaction.Booked` webhook + GraphQL mock returning a card transaction at Anthropic → execute pipeline → assert: one `journal_entries` row with basis='cash', `SUM(debits)==SUM(credits)`, two `journal_lines`, two `decision_traces`, one `agent_decisions` row (the resolver hit cache; the GL classifier hit rule; no AI call). Also assert one `budget_allocations` row, `pipeline_events` count = `2*N + 2`, `envelope.decremented` event arrived on dashboard bus.
  Re-run the same webhook → no new rows; `pipeline_events` shows `cache_hit` on `resolve-counterparty`.
- **PATTERN**: `backend/tests/test_executor.py` for fixture style.
- **IMPORTS**: As Task 10.
- **GOTCHA**: GraphQL mock — patch `SwanGraphQLClient.fetch_transaction` to return a fixture. The OAuth client doesn't run; mock at the higher level.
- **VALIDATE**: `python -m pytest backend/tests/test_pipeline_transaction_booked.py -v`.

### Task 50 — CREATE comprehensive tests `test_pipeline_transaction_released.py`

- **IMPLEMENT**: Run booking pipeline → posted entry → run release pipeline with same `transaction_id` → assert: two entries linked by `reversal_of_id`, `swan_transactions` shows updated status, `budget_allocations` net to zero (positive + negative), bank-mirror balance == GL balance after reversal. Run release a second time → no-op (find_original short-circuits).
- **PATTERN**: As Task 49.
- **IMPORTS**: As Task 49.
- **GOTCHA**: Order matters; the test must first run `transaction_booked` then `transaction_released`. Use a deterministic seed.
- **VALIDATE**: `python -m pytest backend/tests/test_pipeline_transaction_released.py -v`.

### Task 51 — CREATE comprehensive tests `test_pipeline_document_ingested.py`

- **IMPLEMENT**: Drop a fixture extraction (skip the actual Claude vision call by mocking `runner.run`) → assert accrual entry posts with basis='accrual', AP and Expense + VAT lines balanced, decision trace points to documents.id and to an agent_decisions row. Drop the same SHA256 again → no duplicate; same `document_id` returned. Drop a fixture with bad totals → entry lands in `review_queue`; no journal entry posted.
- **PATTERN**: As Task 49.
- **IMPORTS**: As Task 49.
- **GOTCHA**: Mocking the agent runner: build a fake `AgentResult` and patch `get_runner('anthropic').run` to return it. The `propose_checkpoint_commit` audit row still gets written.
- **VALIDATE**: `python -m pytest backend/tests/test_pipeline_document_ingested.py -v`.

### Task 52 — CREATE `test_dashboard_sse.py`, `test_runs_api.py`, `test_trace_api.py`

- **IMPLEMENT**: For each route in Task 45 / 46, an end-to-end test using `httpx.AsyncClient(app=app, base_url='http://test')` (FastAPI's HTTPX-based test client). For SSE, read the response body in chunks; assert the framing `data: {...}\n\n` and the events arrive in the expected order.
- **PATTERN**: FastAPI testing docs; `httpx.AsyncClient` async streaming.
- **IMPORTS**: `httpx.AsyncClient`, `pytest_asyncio`.
- **GOTCHA**: `EventSource` is a browser primitive; in tests, parse the raw SSE response yourself. Each event is `data: <json>\n\n`; heartbeats are `: heartbeat\n\n` (single colon prefix).
- **GOTCHA**: SSE tests must time out. Use `pytest-timeout` or wrap the read in `asyncio.wait_for(timeout=10)`.
- **VALIDATE**: `python -m pytest backend/tests/test_dashboard_sse.py backend/tests/test_runs_api.py backend/tests/test_trace_api.py -v`.

### Task 53 — CREATE `test_wedge_query.py` and `test_cross_db_orphan_check.py`

- **IMPLEMENT**:
  - `test_wedge_query`: against the seed dataset (after running migrations), execute the `RealMetaPRD §7.11` SQL via `ATTACH DATABASE 'audit.db' AS audit;`. Assert: 3 rows (one per employee), sorted DESC by `usd_this_month`, sums match the seed (within rounding).
  - `test_cross_db_orphan_check`: ATTACH all three DBs. Run three queries asserting zero orphans:
    - `SELECT COUNT(*) FROM audit.agent_decisions a LEFT JOIN orchestration.pipeline_runs r ON r.id = CAST(a.run_id_logical AS INTEGER) WHERE a.run_id_logical IS NOT NULL AND r.id IS NULL;`
    - similar for `agent_decisions.line_id_logical → accounting.journal_lines.id`
    - similar for `accounting.decision_traces.agent_decision_id_logical → audit.agent_decisions.id`
- **PATTERN**: `RealMetaPRD §7.11`, `§14 risk #5` mitigation.
- **IMPORTS**: `aiosqlite` for `ATTACH`.
- **GOTCHA**: SQLite `ATTACH` requires absolute paths; use `Path.absolute()`.
- **VALIDATE**: `python -m pytest backend/tests/test_wedge_query.py backend/tests/test_cross_db_orphan_check.py -v`.

### Task 54 — CREATE `tests/fixtures/` PDFs and webhook payloads

- **IMPLEMENT**: Three fixture PDFs (Anthropic invoice €50, Notion invoice $45, paired-supplier invoice €1,200) — generated programmatically via reportlab or hand-crafted; commit to `tests/fixtures/`. Three webhook payloads as JSON files (`swan_anthropic_debit.json`, `swan_customer_credit.json`, `swan_release_anthropic.json`).
- **PATTERN**: `RealMetaPRD §15.2`.
- **IMPORTS**: `reportlab` (for PDF generation; install only as a dev dep) — or hand-craft static fixtures.
- **GOTCHA**: PDFs are binary; commit them once and never modify. Use deterministic generation if possible.
- **VALIDATE**: `ls tests/fixtures/` shows the files.

### Task 55 — UPDATE `backend/orchestration/runners/anthropic_runner.py` — confirm timeouts

- **IMPLEMENT**: Audit and confirm: client default is `timeout=4.5, max_retries=2` (Phase 1 should have set this; verify). The `run` method accepts `deadline_s` kwarg overriding per call. The vision agent passes `deadline_s=15.0`. Add explicit `try/except APITimeoutError` at the call sites that the PRD names; on timeout, return an `AgentResult` with `finish_reason='timeout'`, `confidence=None`, `output=None` — DO NOT raise. The pipeline gates on `confidence` and routes to deterministic fallback or review.
- **PATTERN**: `RealMetaPRD §7.9`.
- **IMPORTS**: `from anthropic import APITimeoutError`.
- **GOTCHA**: `max_retries=2` plus `timeout=4.5` can give worst-case ~13.5s. The §7.9 policy of **no retry on `APITimeoutError`** is what bounds this. Per-request, set `timeout=4.5` and let `max_retries=2` only retry on connection errors, not on timeouts. The `httpx`/SDK semantics surface this distinction; verify the SDK version.
- **VALIDATE**: `python -m pytest backend/tests/test_runner_shape.py -v` (already exists; should still pass) plus a new `test_anthropic_timeout_fallback.py` that simulates timeout via a slow mock.

### Task 56 — CREATE `frontend/` skeleton (Vite + React + TS + Tailwind)

- **IMPLEMENT**:
  - `npm create vite@latest frontend -- --template react-ts`
  - `cd frontend && npm install zustand tailwindcss postcss autoprefixer`
  - `npx tailwindcss init -p`
  - Configure Tailwind to scan `src/**/*.{ts,tsx}`
  - Update `index.html` and `src/main.tsx` to be the entry point
  - Add `vite.config.ts` with proxy: `'/healthz', '/swan', '/external', '/documents', '/runs', '/dashboard', '/journal_entries', '/review'` → `http://localhost:8000`
- **PATTERN**: Vite docs.
- **IMPORTS**: N/A.
- **GOTCHA**: `npm install` must complete inside the project venv constraints — frontend runs as a separate process via `npm run dev` on port 5173. The proxy in `vite.config.ts` makes `/runs/...` from the frontend forward to the backend.
- **VALIDATE**: `cd frontend && npm run dev` shows the Vite dev server at `http://localhost:5173`; the React default page loads.

### Task 57 — CREATE `frontend/src/lib/api.ts` and `sse.ts`

- **IMPLEMENT**:
  - `api.ts`: thin wrapper for `fetch`; methods `getJournalEntries(limit)`, `getEnvelopes(employee_id)`, `getTrace(entry_id)`, `uploadDocument(file, employee_id)`, `approveReview(entry_id, approver_id)`, `triggerPipeline(name, payload)`.
  - `sse.ts`: `subscribeRun(run_id, onEvent)`, `subscribeDashboard(onEvent)` — both wrap `EventSource` with onopen/onmessage/onerror handlers; auto-reconnect with backoff.
- **PATTERN**: Standard fetch/EventSource shape.
- **IMPORTS**: None (browser globals).
- **GOTCHA**: `EventSource` doesn't follow CORS for cross-origin; the Vite proxy handles it.
- **VALIDATE**: Manual: open the browser console, call `subscribeDashboard(console.log)`, watch heartbeats arrive every 15s.

### Task 58 — CREATE `frontend/src/components/{Ledger,EnvelopeRings,UploadZone,TraceDrawer,ReviewQueue,InfraTab}.tsx`

- **IMPLEMENT**:
  - `Ledger.tsx` — fetches initial 50 entries; subscribes to `ledger.entry_posted`; animates new rows in (CSS transition or Framer Motion). Each row shows date, counterparty, amount, GL account, basis (cash/accrual badge), confidence (color-coded). Click → emits a `setSelectedEntry(id)` to Zustand store.
  - `EnvelopeRings.tsx` — fetches initial state per employee; subscribes to `envelope.decremented`; renders three or five rings per employee using SVG arcs (or a Tailwind-styled circle). Color crosses red at 80% (soft threshold).
  - `UploadZone.tsx` — drag-drop a single PDF; POSTs to `/documents/upload`; opens an SSE subscription to `/runs/{run_id}/stream`; shows node-by-node progress (extract → validate → resolve → classify → build → post).
  - `TraceDrawer.tsx` — opens when `selectedEntry` is set; calls `getTrace(id)`; renders chain: Journal line → Decision trace → Agent decision (model, prompt_hash, alternatives, confidence) → Agent costs (token breakdown, USD) → Source webhook/document.
  - `ReviewQueue.tsx` — list of `status='review'` entries; each row shows entry summary + reason; "Approve" button POSTs to `/review/{id}/approve`.
  - `InfraTab.tsx` — three cards: DB sizes, recent runs (last 10), recent events (last 50). Refreshes every 5s.
- **PATTERN**: Standard React function components with hooks.
- **IMPORTS**: React, Zustand, the `api`/`sse` libs.
- **GOTCHA**: SSE subscriptions must be cleaned up in `useEffect`'s return function. Otherwise reconnects accumulate.
- **GOTCHA**: The trace drawer's "click into agent_costs" interaction is the demo's wow moment. Make sure the cost decomposition is visible without scrolling: show input tokens × rate, output tokens × rate, total in USD.
- **VALIDATE**: Manual: load `http://localhost:5173`; trigger a fixture webhook; watch the ledger row appear; click → trace drawer opens.

### Task 59 — CREATE `frontend/src/App.tsx` with tabs + Zustand store

- **IMPLEMENT**: Tabbed layout: Dashboard (Ledger + EnvelopeRings + UploadZone), Review, Infra. `store.ts`: Zustand store with `selectedEntry`, `setSelectedEntry`, `entries`, `appendEntry`, `envelopes`, `updateEnvelope`. Subscribe to dashboard SSE on mount; dispatch updates to the store.
- **PATTERN**: Standard.
- **IMPORTS**: Zustand, components.
- **GOTCHA**: One global SSE subscription, not one per component. Components read from the store.
- **VALIDATE**: Full app loads; tab switching works; a fixture webhook drives a new ledger row.

### Task 60 — UPDATE `README.md` and `CLAUDE.md` to reflect Phase 2 lands

- **IMPLEMENT**: Both files have a "Maintenance flag — read first" directive in `CLAUDE.md`. Update:
  - `README.md` "What works today" to add Swan path, document path, frontend, dashboard SSE.
  - `CLAUDE.md` "in one paragraph" to reference Phase 2 done; add the new directories (`frontend/`, `backend/orchestration/swan/`, `backend/ingress/`).
- **PATTERN**: Existing prose tone.
- **IMPORTS**: N/A.
- **GOTCHA**: This is the maintenance flag's purpose. Out-of-date scaffolding is worse than missing scaffolding.
- **VALIDATE**: `git diff README.md CLAUDE.md` shows the updates.

---

## TESTING STRATEGY

Mirror the Phase 1 pattern: `pytest` + `pytest-asyncio` (already configured via `pytest.ini`'s `asyncio_mode = auto`). Frontend has no automated tests in MVP — manual rehearsal is the gate (per `RealMetaPRD §11` and `§14 risk #1`).

### Unit Tests

- `test_swan_oauth.py` — token cache, refresh, invalidate, refresh-on-401.
- `test_swan_graphql.py` — query happy path, system errors, mutation-error union helper for `Rejection`/`ValidationRejection`/success.
- `test_counterparty_resolver.py` — each of the four stages independently; cache hit on second call; AI fallback only on miss.
- `test_counterparty_classifier_agent.py` — closed-list constraint, confidence + alternatives surface, cache writeback.
- `test_gl_classifier.py` — rule precedence; counterparty rules beat MCC rules; miss returns None.
- `test_gl_classifier_agent.py` — closed-enum constraint via tool schema; cache writeback to `account_rules`.
- `test_journal_entry_builder.py` — each booking pattern produces balanced lines; reversal pattern is a perfect mirror; match_accrual returns the right link or empty.
- `test_gl_poster.py` — balanced entry posts; unbalanced raises and rolls back; one decision_trace per line.
- `test_invariants.py` — each of the five §7.6 asserts independently.
- `test_budget_envelope.py` — happy path; uncategorized skip; multi-line sum; employee→company fallback; reversal negative allocation.
- `test_confidence_gate.py` — multiplicative; None=0.5; floor read from DB; pipeline-scoped override.
- `test_review_queue.py` — enqueue inserts; emits dashboard event.
- `test_validate_totals.py` — sum check; subtotal+vat=total check; bad totals → ok=False.
- `test_document_extractor.py` — mocked Claude vision; extraction result surfaces; timeout fallback returns `confidence=None`.
- `test_conditions.py` — every named condition is testable in isolation.
- `test_external_payload_parser.py` — Stripe shape parses; expected_payment row inserts.

### Integration Tests

- `test_swan_webhook.py` — full HTTP path; signature verify; idempotency; routing.
- `test_external_webhook.py` — full HTTP path; verifier registry; default route.
- `test_document_upload.py` — multipart; SHA256 idempotency; pipeline trigger.
- `test_pipeline_transaction_booked.py` — end-to-end: fake webhook → posted entry + envelope decrement + dashboard events. Re-run = cache hits.
- `test_pipeline_transaction_released.py` — booking → release → reversal; net allocations zero; second release is no-op.
- `test_pipeline_document_ingested.py` — fixture extraction → accrual posted; bad totals → review queue.
- `test_pipeline_external_event.py` — Stripe-shaped fake → `external_event` pipeline runs to completion.
- `test_runs_api.py` — manual trigger, GET /runs/{id}, /trace, /approve.
- `test_dashboard_sse.py` — three event types arrive in order; heartbeats present.
- `test_employee_attribution.py` — known IBAN sets `employee_id_logical`; unknown IBAN leaves NULL; run still completes.
- `test_envelope_routing.py` — Anthropic SEPA-out lands in `ai_tokens`; boulangerie card spend lands in `food`.
- `test_wedge_query.py` — seeded data + wedge SQL = sensible output.
- `test_cross_db_orphan_check.py` — three orphan checks all return zero.

### Edge Cases

- **NULL employee on company-account transactions** — wedge query tolerates via `WHERE employee_id IS NOT NULL` in the query, not the data.
- **Backfilled / historical transactions** — `entry_date[:7]` period derivation; do not use wall clock.
- **Double-release** — short-circuit on `(provider, event_id)` unique; second is a no-op.
- **Counterparty category change mid-month** — already-posted entries are not retroactively re-categorized.
- **Envelope cap exceeded** — does not block; allocation still inserts; ring shows red.
- **Reversal of an entry that is in review (not posted)** — must be a no-op.
- **Out-of-order Swan events** (`Booked` before `Pending`) — only `Booked` posts; `Pending` is mirror-only. Tolerant by design.
- **PDF with bad totals** — lands in review; no journal entry; trace records the validation failure.
- **PDF re-upload (same SHA256)** — same `document_id` returned; no new run; idempotent.
- **Webhook signature mismatch** — 401, no insert, no run.
- **GraphQL 401 mid-pipeline** — OAuth invalidate + retry; if still 401, run fails with `pipeline_failed`; no partial entry.
- **Anthropic timeout** — deterministic fallback; `agent_decisions.source='cache'` or `'rule'` and `finish_reason='timeout'`; the 5s SLA holds.
- **Multi-currency** — out of scope; PRD asserts EUR-only via CHECK constraint. Do not weaken.
- **Five rings per employee where some envelopes don't exist for a category** — UI shows "no envelope" placeholder; the decrement node emits `envelope.no_envelope`.

---

## VALIDATION COMMANDS

Execute every command to ensure zero regressions and 100% feature correctness.

### Level 1: Syntax & Style

```bash
cd "/home/developer/Projects/HEC Paris" && python -m compileall backend/ -q && echo PASS
```

```bash
cd "/home/developer/Projects/HEC Paris" && uv run ruff check backend/  # if ruff configured; else skip
```

Float-arithmetic-on-money guard (`RealMetaPRD §7.7`, `§11 line 1551`):

```bash
# zero matches expected on any line touching journal_lines / budget_allocations / agent_costs
grep -rEn '\b(float|/(?!/))[^\n]*(cents|amount|cost|debit|credit|vat)' backend/orchestration/tools/ backend/orchestration/agents/ \
  | grep -Ev '__pycache__|\.pyc' \
  && echo "FAIL: floats on money path" && exit 1 || echo "OK"
```

Single-chokepoint guard (`RealMetaPRD §6.4`):

```bash
grep -rEn 'INSERT INTO journal_entries' backend/orchestration/ | grep -Ev 'gl_poster\.py|migrations/' && echo "FAIL: journal_entries write outside gl_poster" && exit 1 || echo "OK"
```

### Level 2: Unit Tests

```bash
cd "/home/developer/Projects/HEC Paris" && python -m pytest backend/tests/ -q
```

Focused suites:

```bash
python -m pytest backend/tests/ -k "oauth or graphql or counterparty or gl_classifier or builder or invariants or envelope or confidence or validate_totals or extractor or conditions" -v
```

### Level 3: Integration Tests

```bash
python -m pytest backend/tests/ -k "swan_webhook or external_webhook or document_upload or pipeline_ or runs_api or dashboard_sse or employee_attribution or envelope_routing or wedge or cross_db_orphan" -v
```

The PRD §11 sentinel — a 5-node clean run produces `2N+2 = 12` `pipeline_events` rows. With Phase 2's longer pipelines (transaction_booked has 12 nodes), recompute the sentinel test and assert the new count:

```bash
python -m pytest backend/tests/ -k event_count_contract -v
```

Full suite:

```bash
python -m pytest backend/tests/ -v
```

### Level 4: Manual Validation

Boot backend + frontend in two terminals:

```bash
# Terminal 1 — backend (--workers 1 per RealMetaPRD §9.5)
cd "/home/developer/Projects/HEC Paris" && uv run uvicorn backend.api.main:app --workers 1 --reload --port 8000
```

```bash
# Terminal 2 — frontend
cd "/home/developer/Projects/HEC Paris/frontend" && npm run dev
```

Open `http://localhost:5173` in a browser. Walk the `RealMetaPRD §11` 10-step demo:

1. Drop a fixture `Transaction.Booked` for an Anthropic SEPA-out:
   ```bash
   curl -X POST http://localhost:8000/swan/webhook \
     -H "x-swan-secret: <test secret>" -H "x-swan: present" \
     -H "Content-Type: application/json" \
     -d @backend/tests/fixtures/swan_anthropic_debit.json
   ```
2. Watch the ledger row animate in within 5s. Click → trace drawer opens; trace shows: counterparty resolved via `iban_exact` (confidence 1.0); GL account `626100` via rule (confidence 1.0); no LLM call; cost $0.
3. Drag `tests/fixtures/anthropic_invoice_2026_03.pdf` onto the upload zone. Watch the audit log animate: extract → validate → resolve (cache hit on Anthropic) → classify (rule R) → build accrual → post. ~10s total.
4. Click the new accrual line → see `decision_traces` → click into `agent_decisions` (Claude Sonnet 4.6, prompt hash, confidence 0.94) → click into `agent_costs` (input/output tokens, $0.0023).
5. Watch the per-employee `ai_tokens` envelope ring drop on the dashboard.
6. Run the wedge SQL on stage:
   ```bash
   sqlite3 data/accounting.db <<'EOF'
   ATTACH DATABASE 'data/audit.db' AS audit;
   ATTACH DATABASE 'data/orchestration.db' AS orch;
   SELECT e.email, COUNT(*), SUM(c.cost_micro_usd)/1e6 AS usd
     FROM audit.agent_costs c
     JOIN audit.employees e ON e.id = c.employee_id
    WHERE c.provider='anthropic'
      AND strftime('%Y-%m', c.created_at)=strftime('%Y-%m','now')
    GROUP BY e.id ORDER BY usd DESC;
   EOF
   ```
   Expect one row per employee with non-zero AI cost.
7. Replay the same Swan webhook → no duplicate journal entry; `cache_hit` event arrives in the per-run stream.
8. Drop the same PDF again → `{"status": "duplicate"}`-style response with the existing `document_id`.
9. Drop a fixture `Transaction.Released` for the original Anthropic transaction. Watch a reversal entry animate in; the AP envelope returns to its pre-charge value.
10. Open the Infra tab; confirm the recent runs include all of the above.

### Level 5: Additional Validation (Optional)

If MCP servers / linters from the project's `claude-code` setup expose a security-review or schema-check, run them:

```bash
/security-review  # via Claude Code; reviews uncommitted backend changes
```

Otherwise skip.

---

## ACCEPTANCE CRITERIA

- [ ] `POST /swan/webhook` verifies `x-swan-secret` in constant time, idempotent-inserts on `(provider, event_id)`, resolves `employee_id` from IBAN at trigger time, dispatches the right pipeline via `routing.yaml`, returns 200 within ~50ms target.
- [ ] `POST /external/webhook/{provider}` exists with at least one registered verifier (Stripe HMAC-SHA256); duplicates are no-ops; unknown event types route to `defaults.unknown_event`; unknown providers 404.
- [ ] `POST /documents/upload` is idempotent on SHA256; saves blob; triggers `document_ingested.yaml`; accepts and persists `employee_id`.
- [ ] `transaction_booked.yaml`, `transaction_released.yaml`, `document_ingested.yaml`, `external_event.yaml` all parse, all nodes resolve in the registry, all conditions resolve.
- [ ] Counterparty resolver hits the correct stage deterministically: IBAN exact for known suppliers, fuzzy for novel ones; cache writeback creates a rule on every AI hit.
- [ ] GL classifier hits `account_rules` for known counterparties; AI fallback writes back to `account_rules` so the next request is deterministic.
- [ ] Journal entries balance: `SUM(debit_cents) == SUM(credit_cents)` on every posted entry. CI guard.
- [ ] Confidence gate routes `< floor` to review, `≥ floor` to post. Multiplicative, None=0.5.
- [ ] Budget envelope decrement gated on `gating.posted`, runs in both Swan and document pipelines, allocates per-expense-line, falls back employee→company, skips on `uncategorized`.
- [ ] Compensation pipeline produces a reversal entry with `reversal_of_id` set, marks original `'reversed'`, inserts negative allocations, idempotent on double-release.
- [ ] All five `§7.6` invariants pass on every posted entry.
- [ ] Every `journal_lines.id` has at least one `decision_traces` row.
- [ ] `pipeline_runs.employee_id_logical` populated on every webhook-triggered or upload-triggered run that has a resolvable employee.
- [ ] Wedge query (`§7.11`) returns one row per employee with sensible sums against the seed dataset.
- [ ] Cross-DB orphan check returns zero for all three logical FK relationships.
- [ ] All `AsyncAnthropic` calls use `timeout=4.5, max_retries=2` (15.0 for document extractor); `APITimeoutError` triggers the deterministic fallback, not a retry.
- [ ] `GET /runs/{id}/stream` and `GET /dashboard/stream` deliver SSE events with correct framing; heartbeat every 15s.
- [ ] Dashboard emits `ledger.entry_posted`, `envelope.decremented`, `envelope.skipped`, `review.enqueued` for every relevant event.
- [ ] `GET /journal_entries/{id}/trace` returns the joined chain (line → trace → agent decision → cost → source).
- [ ] `POST /review/{id}/approve` writes approver + approved_at, transitions entry to `'posted'`, emits the deferred dashboard events.
- [ ] Frontend renders ledger, envelope rings, upload zone, trace drawer, review queue, infra tab; SSE-driven; tabs switch cleanly.
- [ ] On-stage demo (Manual Validation Level 4) walks all 10 steps without intervention.
- [ ] Same demo runs twice in a row with no duplicate entries (idempotency).
- [ ] `RealMetaPRD §6.6` PRAGMAs unchanged; no new `conn.commit()` outside `write_tx`.
- [ ] No floats introduced on money paths.
- [ ] No PRD or briefing files modified.
- [ ] Backup video of the full demo recorded (per `§14 risk #1`).

---

## COMPLETION CHECKLIST

- [ ] All 60 tasks completed in order, top to bottom
- [ ] Each task's validation passed before moving to the next
- [ ] All four validation levels executed successfully on a fresh DB
- [ ] Manual on-stage demo (Level 4) executed end-to-end without intervention, twice
- [ ] No linting, type-checking, or money-path-float errors
- [ ] All acceptance criteria met
- [ ] `README.md` and `CLAUDE.md` updated (Task 60)
- [ ] PRD and briefing files unchanged (`git diff --stat Orchestration/PRDs/ "Dev orchestration/"` empty)
- [ ] Phase 1 plans (`phase1-metalayer-foundation.md`, `phase-1-gap-audit.md`, `phase-1-critical-gap-remediation.md`) unchanged

---

## NOTES

### Reference guides relied on

- `Orchestration/PRDs/RealMetaPRD.md` — the contract (`§4`, `§5`, `§6.4`, `§6.5`, `§6.6`, `§7.1`–`§7.11`, `§9.2`, `§10`, `§11`, `§12 Phases D/E/F`, `§14`, `§15.2`, `§15.3`)
- `Orchestration/Plans/phase1-metalayer-foundation.md` — task format and section ordering
- `Orchestration/Plans/phase-1-critical-gap-remediation.md` — wiring lessons (employee attribution, envelope decrement, compensation, generic external ingress, §7.9 timeouts) baked in from the start of Phase 2 instead of remediated after
- `Dev orchestration/_exports_for_b2b_accounting/01_ORCHESTRATION_REFERENCE.md` — event semantics, SSE pattern
- `Dev orchestration/_exports_for_b2b_accounting/02_YAML_WORKFLOW_DSL.md` — DSL grammar
- `Dev orchestration/_exports_for_b2b_accounting/03_SQLITE_BACKBONE.md` — WAL, single-writer, migrations, payload_version
- `Dev orchestration/_exports_for_b2b_accounting/04_AGENT_PATTERNS.md` — closed-list classifier, multiplicative confidence, cache-warmer cascade
- `Dev orchestration/_exports_for_b2b_accounting/05_swan_integration.md` — webhook lifecycle, idempotency seam, mutation-error union
- `Dev orchestration/swan/SWAN_API_REFERENCE.md` — OAuth flow, GraphQL queries, event types, IP allowlist
- `Orchestration/research/ANTHROPIC_SDK_STACK_REFERENCE.md` — vision API, tool use, timeout/retry policy

### Architectural decisions made by this plan

1. **Mounted routers, not a monolithic `main.py`.** Each domain (Swan, external, documents, runs, dashboard) is its own `APIRouter`, mounted in `main.py`'s lifespan. This keeps `main.py` short and makes deletion easy if a phase gets cut.
2. **Single dashboard bus, not per-domain buses.** All cross-cutting events (`ledger.entry_posted`, `envelope.*`, `review.enqueued`) share one `event_bus.subscribe(_DASHBOARD_BUS_KEY)`. Per-run buses keep their existing fanout. Simpler than per-domain buses; sufficient for MVP.
3. **`gl_poster` is the single chokepoint for journal writes.** Every other tool builds and returns; `gl_poster.post` is the only one that writes to `journal_entries`/`journal_lines`/`decision_traces`. Enforced by a CI grep.
4. **`envelope_category` lives on `counterparties`, not in a separate mapping table.** Single source of truth; cache-friendly; one fewer join. The trade-off is a column add on a hot table; mitigated by SQLite ALTER TABLE ADD COLUMN being O(1) on STRICT tables.
5. **The `external_event.yaml` pipeline is a stub.** Routing claim only. No live third-party provider wired. The verifier registry has the Stripe shape so a Phase 3 implementer can add Shopify/HubSpot in one line.
6. **Frontend is a parallel work track.** Backend Tasks 1–55 must be sequential; frontend Tasks 56–59 can begin as soon as the API surface is route-skeleton-complete (around Task 47).
7. **Demo seed is in migrations, not a separate fixtures script.** Migrations are idempotent and version-controlled; running `migrate_all` on a fresh DB produces the demo state with no extra step. The trade-off is that fresh-fresh DB tests need a `conftest.py` that resets after each test.
8. **No new dependencies on the AI side.** Anthropic SDK is already pinned; the runner is already wired; the document extractor and the two classifier agents all flow through the same registered runner.

### Confidence score for one-pass success: **6.5/10.**

Reasons it's not 8/10:

- **Scope.** Phase 2 is ~14h backend + ~5h frontend; ~60 tasks; spans Swan integration, Claude vision, real-time SSE, three database write paths, and a TypeScript frontend. The size alone is the main risk.
- **Swan sandbox dependencies.** The integration tests can run against mocked HTTP, but a true on-stage rehearsal requires live Swan sandbox credentials. If the sandbox is rate-limited or down on demo day, fall back to the fully-mocked path with `tests/fixtures/`.
- **Demo seed determinism.** The `0006_demo_swan_transactions.py` migration is the most likely place to introduce subtle bugs (balance tracking, date arithmetic). If invariant 2 fails on the seed, debug the seed, not the assert.
- **Frontend stack switch.** The implementer goes from Python+SQLite to TypeScript+React mid-plan. If the implementer is more confident in one stack, sequence accordingly: backend first to a working API, then frontend.
- **Integer-cents discipline at the VAT boundary.** PDF extraction returns `vat_percent` (a number); the builder converts to integer cents via `(subtotal_cents * vat_rate_bp + 5000) // 10000`. One sloppy `*0.20` slips a float into the money path. The CI guard catches it; do not regress.

Reasons it's not 5/10:

- The Phase 1 metalayer is complete and well-tested (70 tests, 1945 LOC). The plumbing every Phase 2 task plugs into is reliable.
- The PRD is unusually specific. `§7.4` and `§7.3` give the verbatim YAML; `§7.5` gives the verbatim SQL; `§7.6` gives the five invariants; `§7.9` gives the timeout numbers. Most tasks are translation, not design.
- The wiring lessons from `phase-1-critical-gap-remediation.md` are baked in from the start. The four most common Phase D/E/F mistakes (employee not attributed, envelope not decremented, compensation skipped, timeouts not applied) are explicitly addressed in the early tasks.
- The frontend can be cut to 50% scope (drop the Infra tab and the Review queue UI; keep ledger + envelopes + upload + trace) without breaking the demo. The §11 success criteria do not require all six components.

### Mitigations and fallback paths

- **If Swan sandbox is down on demo day:** the GraphQL client is mockable at the `SwanGraphQLClient.fetch_transaction` boundary; substitute fixture data and run the pipeline against the seed Swan transactions. This is also the unit-test path.
- **If the frontend is incomplete:** the `RealMetaPRD §11` demo can be walked from `curl` + `sqlite3` queries; the audit-trail story works without animation. The "click any number, see the why" demo beat is the load-bearing one; keep the trace drawer over the envelope rings if forced to choose.
- **If a node is too slow under timeouts:** the §7.9 deterministic-fallback path is the safety net. Per-node, the executor's fail-fast still bounds the run.
- **If pipeline parsing fails on boot:** `Task 36`'s validation script catches this; CI should run it on every commit. Failed parse aborts startup loudly; do not partial-start.
