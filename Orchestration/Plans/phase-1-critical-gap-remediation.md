# Feature: Phase 1 Critical-Gap Remediation — Wedge Wiring, Compensation, External Ingress, and Reliability Floor

The following plan should be complete, but its important that you validate documentation and codebase patterns and task sanity before you start implementing.

Pay special attention to naming of existing utils types and models. Import from the right files etc.

## Feature Description

This plan is **not** a redesign and **not** a PRD modification. It is a post-Phase-1 (post-Phases A–F) audit-and-remediation pass that closes the wiring gaps that most commonly break the product's demo wedge ("AI spend per employee — invoice from Swan booked against the right employee's AI envelope, with a clickable trace") even when every component named in the PRD has been built.

Phase A–F implementers tend to build the *boxes* (schemas, tools, agents, runners, registries, the executor) and then leave a handful of *arrows between boxes* loose. Those arrows are what this plan tightens. The product's demo lands or fails on five of them:

1. **Employee attribution**: every `pipeline_runs` row that comes from a Swan transaction or a PDF invoice must carry an `employee_id_logical`, populated at trigger time, so the wedge query (`SUM by employee`) returns sensible numbers without joining through `trigger_payload` JSON.
2. **Envelope decrement**: `budget_envelope.decrement` must be a real node in both the Swan booking pipeline and the document ingestion pipeline, gated by `posted`, with a `category` resolved from the counterparty (so an Anthropic line lands on `ai_tokens`, a boulangerie lands on `food`, etc.).
3. **Counterparty → envelope category mapping**: a deterministic, configurable mapping (rule table or column on `counterparties`) that the decrement tool consults — so adding "OpenAI → ai_tokens" is a row insert, not a code change.
4. **Compensation pipeline (`transaction_released.yaml`)**: `Released` and `Canceled` Swan transactions must reverse the prior cash entry and credit back the matching `budget_allocations` rows. Otherwise the live ledger drifts away from the bank-mirror after a single cancellation during the demo.
5. **Generic external webhook ingress (`/external/webhook/{provider}`)**: the registry-driven verifier + routing path must exist even if no provider is wired live, because the pitch frame is "Swan is one source — anything Stripe-shaped works tomorrow."

Plus a reliability floor (§7.9 retry/timeout/idempotency, dashboard SSE for envelope events) that Phase F often defers and the demo notices.

> **Note on AI cost.** The product allocates AI cost via the **Swan transaction itself** (Anthropic SEPA-out → `ai_tokens` envelope) and via **PDF invoices** (Anthropic subscription invoice → accrual entry → `ai_tokens` envelope). It does **not** use `audit.agent_costs` token telemetry to feed the wedge. `agent_costs` is implemented per the PRD for completeness/audit; this plan does not extend it. If the implementer of Phase C over-engineered cost telemetry, leave it alone.

## User Story

As the engineer demoing the product to judges
I want every Swan transaction and every PDF invoice to land in the right employee's right-category envelope, with a click-through trace, even after a cancellation
So that the wedge ("Marie spent €X on AI this month — here's why, here's the source") survives a live demo and any judge follow-up.

## Problem Statement

Phase A–F implementers, working under hackathon time pressure, frequently:

- Implement the `pipeline_runs.employee_id_logical` column but never populate it (the Swan webhook handler doesn't resolve `employee_id` from the transaction's IBAN/account before triggering the run).
- Build `budget_envelope.decrement` as a tool but forget to wire it into the YAML pipeline, or wire it but forget the `category` argument (so every transaction lands on a default envelope, or none).
- Resolve counterparties but never map them to envelope categories — meaning the dashboard rings show one big bucket instead of `food / travel / saas / ai_tokens / leasing`.
- Skip `transaction_released.yaml` because the demo path is the happy path; first cancellation in the live demo causes ledger ↔ bank-mirror drift and the invariant check fails on stage.
- Skip `/external/webhook/{provider}` because no third-party CRM is wired — but the pitch claim is "we route any provider," and judges ask.
- Use the Anthropic SDK defaults instead of the §7.9 timeouts/retries — meaning a slow-network demo hangs the booking pipeline past the 5s SLA.

Each of these is a small wiring fix, but together they're the difference between "the product works" and "the product demos."

## Solution Statement

A six-task remediation pass, each task structured as **(a) audit the existing code → (b) document what's missing → (c) implement the smallest patch that closes the loop → (d) prove it with a test that fails before the patch and passes after.** The plan is deliberately not file-pinned; the implementing agent is expected to discover the actual file layout from the Phase A–F output and apply the patches in-place rather than introducing parallel structures.

## Feature Metadata

**Feature Type**: Bug Fix + Enhancement (wiring remediation; no new domains)
**Estimated Complexity**: Medium (most patches are 5–40 lines each; the test scaffolding is the larger time cost)
**Primary Systems Affected**:
- Orchestration: trigger handlers, executor, registries, conditions
- Accounting: counterparty resolver, journal builder, GL poster, budget envelope tool
- Audit: employees seed (only if missing)
- API: Swan webhook handler, generic external webhook ingress, dashboard SSE
- Pipelines: `transaction_booked.yaml`, `transaction_released.yaml`, `document_ingested.yaml`, optional `external_event.yaml`

**Dependencies**: Whatever Phase A–F shipped — no new external libraries. If retries/timeouts are missing, use stdlib `asyncio.wait_for` and the Anthropic SDK's built-in `timeout` / `max_retries` parameters; do not introduce `tenacity`.

---

## CONTEXT REFERENCES

### Relevant Codebase Files — IMPORTANT: YOU MUST READ THESE BEFORE IMPLEMENTING

The Phase A–F implementation is the source of truth for what exists. Before writing anything, walk the tree and read in this order:

- **`Orchestration/PRDs/RealMetaPRD.md`** (full file) — Why: This plan implements *against* the PRD; do not deviate. Pay special attention to:
  - §6.4 "Design patterns" — `propose → checkpoint → commit`, named conditions, registries-as-dicts
  - §6.5 "Directory structure" — likely (but not guaranteed) shape of the implemented tree
  - §7.1 "Webhook ingress (Swan)" — what the Swan handler must do before triggering a run
  - §7.2 "Webhook ingress (external / CRM)" — generic ingress contract
  - §7.4 "Swan transaction booking pipeline" — the canonical pipeline shape and node list
  - §7.5 "SQLite schemas (the seam)" — the column names you must populate (`employee_id_logical`, `category`, `accrual_link_id`, `reversal_of_id`)
  - §7.6 "Hard invariants" — the asserts the compensation pipeline must preserve
  - §7.9 "Retry / timeout / idempotency policy" — the exact numbers (4.5s default, 15s document, `max_retries=2`, no retry on `APITimeoutError`)
  - §7.11 "The wedge query" — the SQL that must work after this plan lands
  - §10 "API Specification" — the inbound/internal endpoint contracts
  - §12 Phase D / Phase F bullets — what was supposed to be built
- **`backend/orchestration/` (or wherever the executor lives)** — Why: discover the registry signatures, `FingentContext` shape, and how runners receive `employee_id`.
- **`backend/api/swan_webhook.py` (or equivalent)** — Why: this is where employee resolution must happen *before* `executor.run(...)`.
- **`backend/pipelines/*.yaml`** — Why: the pipelines you will edit. Confirm node IDs and `depends_on` shape before adding nodes.
- **`backend/tools/budget_envelope.py` (or equivalent)** — Why: confirm the existing signature; do not change it if Phase D shipped one.
- **`backend/tools/counterparty_resolver.py`** — Why: this is where the counterparty → envelope category mapping likely belongs (or near it).
- **`ingress/routing.yaml`** — Why: the routing table that compensation and external pipelines must register against.
- **`tests/`** — Why: copy the existing test fixture style (likely fake-webhook + replay assertions); do not invent a new one.

### New Files to Create (only if Phase A–F omitted them)

Do **not** create any of these if a file with the same role already exists. Discover first, then create.

- `backend/pipelines/transaction_released.yaml` — compensation pipeline (only if missing).
- `backend/pipelines/external_event.yaml` — generic external-event pipeline scaffold (only if Phase D skipped §7.2).
- `backend/api/external_webhook.py` — generic external webhook handler (only if missing).
- `backend/conditions/budget.py` — named conditions for envelope routing (e.g. `budget.envelope_category_known`), only if conditions live in per-domain files in this codebase. If conditions are co-located in `gating.py`, add there instead.
- `tests/test_compensation_path.py`, `tests/test_external_webhook.py`, `tests/test_employee_attribution.py`, `tests/test_envelope_routing.py` — only the ones not already present.

### Relevant Documentation — YOU SHOULD READ THESE BEFORE IMPLEMENTING

- **PRD §7.1, §7.2, §7.4, §7.5, §7.9, §7.11** — referenced above; the contractual surface for this plan.
- **`Dev orchestration/_exports_for_b2b_accounting/05_swan_integration.md`** — Why: the `Released`/`Canceled` lifecycle and the `(provider, eventId)` idempotency shape; the union-typed mutation error pattern. Specific section: "Webhook lifecycle" and "Idempotency on the seam."
- **`Dev orchestration/_exports_for_b2b_accounting/01_ORCHESTRATION_REFERENCE.md` §1c** — Why: the event sequence the executor emits; the compensation pipeline must emit the same envelope (`pipeline_started` → per-node events → `pipeline_completed`).
- **`Dev orchestration/_exports_for_b2b_accounting/02_YAML_WORKFLOW_DSL.md` §1–3** — Why: confirm the `when:` named-guard shape used in this codebase before adding new conditions.
- **`Dev orchestration/_exports_for_b2b_accounting/04_AGENT_PATTERNS.md` §3** — Why: confidence floor + decision-trace patterns; the compensation entries also need decision traces.
- **Anthropic Python SDK — `AsyncAnthropic` constructor parameters** — Why: §7.9 mandates `timeout=4.5, max_retries=2` per request; the document path overrides to `timeout=15.0`. Confirm the constructor signature in the version pinned by the implementer's `pyproject.toml` before patching. Specific section: client configuration / per-request overrides.
- **Python `asyncio.wait_for` docs** — Why: per-node timeout fallback if a tool does not accept a `timeout` parameter natively.

### Patterns to Follow

Patterns are extracted from PRD §6.4 and the briefing/exports — but **always confirm against the actual implementation** before mirroring; the implementer may have made small idiomatic adjustments.

**Named-condition registry pattern** (PRD §6.4): conditions are Python functions registered in `_CONDITION_REGISTRY` keyed by `"domain.name"` (e.g. `"gating.posted"`, `"counterparty.unresolved"`). YAML `when:` references the key. Do not introduce expression DSLs.

**Single-writer lock pattern** (PRD §6.6): every cross-row write within one DB happens inside `async with ctx.write_locks["accounting"]:` (or `"orchestration"` / `"audit"`). Compensation entries and budget reversals must hold the lock the same way the forward path does.

**`propose → checkpoint → commit` wrapper** (PRD §6.4): every agent write (and every envelope decrement / reversal) goes through one chokepoint that creates the `agent_decisions` row and the `decision_traces` row. Do not bypass this — even for rule-based decrements, write a `source='rule'` decision trace.

**Append-only discipline** (PRD §6.4): the compensation pipeline does **not** UPDATE the original `journal_entries.status` to "reversed" by destroying data — it INSERTs a new entry with `reversal_of_id` set, and (only) updates the original's `status` from `'posted'` to `'reversed'`. Same for budget allocations: do not delete; insert a negative-amount allocation with a `decision_traces` row pointing to the reversal entry.

**Integer-cents discipline** (PRD §7.7): never introduce floats on a money path. The CI grep audit (if Phase A–F set one up) will catch this.

**Logical FK convention** (PRD §6.2): cross-DB references are by integer-as-text id with the suffix `_logical` and are not enforced by SQL FOREIGN KEY. The compensation pipeline must populate `source_run_id` and `agent_decision_id_logical` exactly as the forward pipeline does.

---

## IMPLEMENTATION PLAN

### Phase 1: Audit the Phase A–F Implementation

Before any code changes, produce a one-page audit. This is not optional — every later phase reads from this audit.

**Tasks:**

- Walk `backend/` (or wherever the implementation lives) and record the actual directory layout. The PRD §6.5 layout is suggestive, not authoritative.
- For each of the six gap areas (employee attribution, envelope decrement, counterparty→category, compensation, external ingress, reliability floor), record one of: **PRESENT** / **PARTIAL** / **MISSING**, with the file:line evidence.
- For each PARTIAL or MISSING item, write one paragraph: "what's there, what's missing, what the smallest patch looks like."
- Confirm the test framework, fixture layout, and how runs are exercised end-to-end (look at how the existing `test_swan_path.py` or `test_executor.py` triggers a fake webhook). Mirror this style in new tests.
- Confirm whether `agent_costs` was implemented; if so, leave it alone. If it was skipped, **do not add it** in this plan — out of scope per user direction.

**Deliverable:** a short audit file at `Orchestration/Plans/phase-1-gap-audit.md`, with one section per gap, that the rest of this plan will reference. This file is throwaway-after-implementation; it is not committed long-term.

### Phase 2: Foundation — Counterparty → Envelope Category Mapping

This is upstream of the decrement node; do it first.

**Tasks:**

- Decide where the mapping lives. Two reasonable shapes; pick whichever is closer to the existing code:
  - **Option A (preferred if `account_rules` is already populated):** add a column `envelope_category` (TEXT, nullable) to `counterparties`, populated by `counterparty_resolver` when it identifies a counterparty. Mapping table is implicit in `counterparties` rows. Pro: single source of truth, cache-friendly. Con: a migration.
  - **Option B (preferred if no migrations have been run yet):** a new table `counterparty_categories(counterparty_id PK, category TEXT NOT NULL)` in `accounting.db`. Pro: no schema change to a hot table. Con: extra join.
- Seed the mapping for the demo dataset: at minimum **Anthropic → `ai_tokens`**, **OpenAI → `ai_tokens`**, **Notion → `saas`**, **the boulangerie → `food`**, **OFI utility → `saas` or a `utilities`** category if the PRD seed list uses one. Source the names from PRD §15.2 "Demo seed dataset."
- Extend `counterparty_resolver` to write `envelope_category` whenever it creates or updates a `counterparties` row (or the new mapping table). For the AI handoff path (`counterparty_classifier.run`), the LLM should be prompted to suggest a category from the closed enum used by `budget_envelopes.category`; record alternatives via the existing `agent_decisions.alternatives_json` mechanism so a low-confidence category doesn't silently mis-route.
- Default category for unresolved counterparties: `'uncategorized'`. The decrement node must treat this as a "skip + log" rather than crash.

**Why this phase is first:** every later phase that touches budget envelopes needs a category lookup. Building decrement before mapping forces a hardcoded enum or a placeholder, which then leaks into tests.

### Phase 3: Core — Employee Attribution at Trigger Time

**Tasks:**

- In the Swan webhook handler (`api/swan_webhook.py` or equivalent), after the GraphQL re-fetch of `transaction(id)` and before `executor.run(...)`:
  - Resolve `employee_id` by joining the transaction's `account_id` (or owning IBAN) against `audit.employees.swan_account_id` / `swan_iban`. Read-only join; no lock needed.
  - If unresolved (company-account transaction or new IBAN), set `employee_id_logical` to NULL and proceed — do **not** fail the run. The wedge query handles NULL via `WHERE employee_id IS NOT NULL`.
  - Pass `employee_id` into the run as a top-level parameter (alongside `trigger_payload`), persisted on `pipeline_runs.employee_id_logical`. Do not bury it inside `trigger_payload` JSON.
- In the document upload handler, accept an `employee_id` form field on `POST /documents/upload` (per PRD §10), persist it on `documents.employee_id`, and pass it through to the run trigger the same way.
- The `propose → checkpoint → commit` wrapper should already accept the run's `employee_id` and propagate it onto `agent_costs.employee_id` if Phase C wired it. Do not modify that flow; just confirm it.
- Add a regression test: webhook fixture with a known IBAN → assert `pipeline_runs.employee_id_logical` is set on the resulting run; webhook with a company IBAN → assert it is NULL and the run still completes.

**Gotcha:** if the implementer stored the IBAN in `swan_transactions.raw` JSON only and not as a column, the resolver must extract it via SQLite `json_extract(raw, '$.account.iban')` rather than a column lookup. Confirm in audit phase.

### Phase 4: Core — Envelope Decrement Wired Into Both Pipelines

**Tasks:**

- In `transaction_booked.yaml` (or wherever the Swan booking pipeline lives), add — if missing — a node `decrement_envelope` (or whatever convention the codebase uses) that:
  - `depends_on: [post-entry]` (i.e. depends on the `gl_poster.post` node id; confirm in audit)
  - `when: gating.posted` (only decrements after the entry actually posted; on `needs_review` path, do not decrement)
  - Tool: `budget_envelope.decrement`
  - Inputs: `entry_id`, `employee_id` (from `ctx.employee_id`), `category` (from the counterparty mapping), `period` (`YYYY-MM` from `entry_date`)
- In `document_ingested.yaml`, add the same node post-`build_accrual` + `gl_poster.post`, with the same gating.
- Ensure `budget_envelope.decrement` itself:
  - Looks up the active envelope by `(scope_kind='employee', scope_id=employee_id, category, period)`, falling back to `('company', NULL, category, period)` if no employee envelope exists. (Per PRD §7.5 schema; confirm against implementation.)
  - Inserts a `budget_allocations` row of `(envelope_id, line_id, amount_cents)` for the *expense* line of the journal entry — not the bank/AP counter-line. The expense line is the one whose `account_code` is in chart class 6 (PCG `6xxxxx`); confirm via the existing chart structure.
  - Emits a custom executor event (or fires through the SSE bus) named `envelope.decremented` carrying `{employee_id, category, period, new_used_cents, cap_cents}`. This is what the dashboard rings subscribe to.
  - Writes a `decision_traces` row with `source='rule'`, `confidence=1.0`, pointing at the `line_id` and the `agent_decision_id_logical` of the resolver that produced the category.
- If `category == 'uncategorized'`, skip the decrement (insert nothing) and emit `envelope.skipped` so the dashboard can show "uncategorized — needs counterparty review."

**Gotcha:** the decrement is **post-charge only** in MVP (PRD Phase F / §7.4). Do **not** introduce a pre-check / blocking gate; that is post-hackathon work.

**Gotcha:** the period key. Use `entry_date[:7]` (slice the YYYY-MM prefix) — do not call `datetime.now()`, because backfilled transactions need the historical month.

### Phase 5: Core — Compensation Pipeline (`transaction_released.yaml`)

**Tasks:**

- Confirm the Swan webhook handler routes `Transaction.Released` and `Transaction.Canceled` events to a different pipeline name (per `ingress/routing.yaml`). If the routing.yaml only lists `transaction_booked`, add the entry.
- Create `transaction_released.yaml` (only if missing) with this rough shape:
  - `fetch_transaction` (`swan_query.fetch_transaction`) — same as forward path
  - `find_original_entry` — look up the `journal_entries` row whose `source_run_id` was the original booking's `run_id`, or whose `journal_lines.swan_transaction_id` matches the released tx id. Tool: a small, deterministic lookup; if no tool exists, add a new one and register it.
  - `build_reversal` (`journal_entry_builder.build_reversal` — add if missing) — produces a new entry with debit/credit lines swapped, `reversal_of_id` pointing at the original entry, `basis` matching the original.
  - `post_reversal` (`gl_poster.post`) — same poster, same invariants.
  - `mark_original_reversed` — UPDATE `journal_entries.status` from `'posted'` to `'reversed'` for the original. This is the single legitimate UPDATE outside `pipeline_runs.status` (PRD §6.4 rule); document it inline.
  - `decrement_envelope_reversal` — insert `budget_allocations` rows with **negative** `amount_cents` referencing the *reversal* entry's lines. Do not delete the original allocations.
  - `invariant_checker.run` — after the reversal, the AP/cash account balance + the matching original must net to zero per PRD §7.6 invariant 5.
- Add a regression test: forward webhook → posted entry → released webhook → reversed entry; assert (a) two entries exist, linked by `reversal_of_id`; (b) `swan_transactions` shows `Released`; (c) `budget_envelopes` net usage for that line is zero; (d) the bank-mirror balance == GL balance after the reversal.

**Gotcha:** if the original entry's `status='reversed'` already, the pipeline must short-circuit (idempotent on `(provider='swan', event_id)`). Do not double-reverse.

**Gotcha:** the reversal entry needs its own `decision_traces` rows. The whole "auditable AI" pitch breaks if reversals appear in the ledger without explanations.

### Phase 6: Core — Generic External Webhook Ingress

**Tasks:**

- If `api/external_webhook.py` does not exist, create it. Endpoint: `POST /external/webhook/{provider}` per PRD §10.
- Implement a `_VERIFIER_REGISTRY: dict[str, Callable[[bytes, dict[str, str]], bool]]` keyed by provider name. Register at least **one** verifier (Stripe HMAC-SHA256 is the canonical example); leave the registry open so adding Shopify is one line. Do not actually wire a third-party provider — this is for the routing claim, not for live integration.
- Inside the handler:
  - Verify signature via the registered verifier (constant-time compare).
  - Insert into `external_events` with `(provider, event_id)` unique constraint — duplicate webhooks return 200 with a `cache_hit`-style log, no run triggered.
  - Look up `routing.yaml`'s `external.{provider}.{event_type}` key; if no match, fall back to `defaults.unknown_event` (per PRD §7.2). Default behavior: log_and_continue, do not 500.
- Create a stub pipeline `external_event.yaml` that simply logs the payload and emits `pipeline_completed`. This proves the routing path works end-to-end without committing to a downstream behavior.
- Add a regression test: POST to `/external/webhook/stripe` with a valid HMAC and a known event type → assert one `external_events` row, one `pipeline_runs` row in `completed` status. POST again with the same `event_id` → assert no new rows. POST with an unknown `event_type` → assert the `defaults.unknown_event` route fired.

**Gotcha:** signature verification must happen **before** body parsing — if the implementer JSON-decodes first, they may have already trusted attacker-controlled input. Verify on the raw `await request.body()`.

**Gotcha:** the `event_id` field name varies by provider (Stripe: `id`; Shopify: header `X-Shopify-Webhook-Id`). The verifier should also extract a normalized `event_id` and return it alongside the boolean — otherwise you re-parse the body twice.

### Phase 7: Reliability Floor — Retry / Timeout / Idempotency

**Tasks:**

- Audit every `AsyncAnthropic` instantiation. Per PRD §7.9, the default must be `timeout=4.5, max_retries=2`. The document-extractor agent overrides to `timeout=15.0` (longer because vision). If the codebase instantiates the client once and reuses it, set defaults on the singleton; if it instantiates per call (less efficient but possible), pass per-request overrides via `client.messages.create(..., timeout=...)`.
- Add the explicit `try/except APITimeoutError` fallback at the call sites that the PRD names (counterparty resolver, GL classifier): on timeout, **do not retry** — fall back to the deterministic path (rule lookup with no AI handoff). This is opposite to the SDK default.
- For tools that take longer than 5s and aren't LLM calls (e.g. PDF blob hashing for very large files), wrap the body in `await asyncio.wait_for(coro, timeout=...)` at the executor layer if the executor doesn't already enforce per-node timeouts. The PRD recommends this; verify whether Phase B implemented it.
- Confirm `Idempotency-Key` is set on outbound LLM calls per §7.9 (`swan-{event_id}`, `doc-{sha256}`); add if missing.
- Add a test using a fake transport that delays past the timeout: assert the run completes via the deterministic fallback (not via an exception bubbling to the executor) and that `agent_decisions.source` is `'rule'` (not `'agent'`) on the fallback row.

**Gotcha:** the SDK's `max_retries` and `timeout` semantics interact — `max_retries=2` plus `timeout=4.5` can in the worst case give you ~13.5s end-to-end. The §7.9 policy of **no retry on `APITimeoutError`** is what bounds this. Do not regress.

### Phase 8: Dashboard SSE — Envelope Events

**Tasks:**

- In the executor (or wherever `pipeline_events` are written), detect `envelope.decremented` events and re-emit them on `/dashboard/stream` (the top-level SSE bus, not the per-run stream). If a dashboard SSE bus does not yet exist, the smallest viable shape is an `asyncio.Queue` instance kept on app state, with `dashboard_stream` reading from it via a generator. Do not introduce Redis or a real pub/sub.
- The event payload to the frontend: `{type: 'envelope.decremented', employee_id, category, period, used_cents, cap_cents, soft_threshold_pct, ledger_entry_id}`. The frontend's envelope-rings component reads this directly.
- Mirror `ledger.entry_posted` (when `gl_poster.post` succeeds) and `review.enqueued` (when `confidence_gate` routes to review) on the same dashboard stream. The PRD §10 lists these three; if Phase F shipped the SSE route but only emits one of them, add the others.
- Add a regression test: trigger one fake Swan webhook → connect a test SSE client to `/dashboard/stream` → assert at least three events arrive in order: `ledger.entry_posted`, `envelope.decremented`, and (if confidence is below the floor on the seed agent) `review.enqueued`.

**Gotcha:** SSE clients must be served `text/event-stream` with the right framing (`data: {...}\n\n`). FastAPI's `StreamingResponse` plus an `async def event_gen()` is the conventional shape. If the implementer used `EventSourceResponse` from `sse-starlette`, that's also fine — do not change libraries.

### Phase 9: Validation

See [Validation Commands](#validation-commands) below. After every implementation phase above, run the relevant level. After all phases, run all levels.

---

## STEP-BY-STEP TASKS

Execute every task in order, top to bottom. Each task is atomic and independently testable. The pattern within each task is **AUDIT → PATCH → TEST**, because we are remediating an existing implementation rather than building from a blank slate.

### AUDIT existing Phase A–F implementation

- **IMPLEMENT**: Walk the implemented tree; produce `Orchestration/Plans/phase-1-gap-audit.md` with one section per gap (employee attribution, envelope decrement, counterparty→category, compensation, external ingress, retries/timeouts, dashboard SSE, envelope events). Each section: PRESENT / PARTIAL / MISSING + file:line evidence + smallest-patch description.
- **PATTERN**: PRD §6.5 directory structure as starting point; trust the actual layout over the PRD layout when they disagree.
- **IMPORTS**: None.
- **GOTCHA**: Do not start patching during the audit. Audit first; patch second.
- **VALIDATE**: `test -f Orchestration/Plans/phase-1-gap-audit.md && wc -l Orchestration/Plans/phase-1-gap-audit.md` (audit file exists and is non-trivial).

### ADD counterparty → envelope category mapping

- **IMPLEMENT**: Per Phase 2 above. Choose Option A (column on `counterparties`) or Option B (new mapping table) based on whether migrations have already been run. Seed Anthropic, OpenAI, Notion, the boulangerie, and OFI per PRD §15.2.
- **PATTERN**: Existing migration style in `migrations/accounting/`. Mirror the seed-data pattern from `0001_init.py` (or whatever the implementer named it). Categories must come from the closed enum in `budget_envelopes.category`.
- **IMPORTS**: Whatever the existing migrations import (`sqlite3`, project's migration helper).
- **GOTCHA**: Do not introduce free-text categories; the dashboard rings group on a fixed enum. Confirm the enum from `budget_envelopes.category` comments in PRD §7.5 and the seed dataset.
- **VALIDATE**: `pytest -k counterparty_category` (write the test first; should pass after the migration).

### UPDATE counterparty_resolver to write category on resolution

- **IMPLEMENT**: When the resolver creates or updates a `counterparties` row, also write the envelope category (column or mapping table). For AI-handoff path, prompt the classifier with the closed category enum; record alternatives.
- **PATTERN**: PRD §6.4 `propose → checkpoint → commit`; the existing resolver already wraps writes in this. Do not bypass.
- **IMPORTS**: Existing.
- **GOTCHA**: Cache invalidation. If `counterparties` is cached (likely yes, per `04_AGENT_PATTERNS.md` cache-warmer pattern), the cache must be busted on category update — otherwise the decrement node reads a stale category.
- **VALIDATE**: `pytest -k counterparty_resolver` and a new `tests/test_envelope_routing.py::test_anthropic_resolves_to_ai_tokens`.

### ADD employee resolution at Swan webhook trigger time

- **IMPLEMENT**: Per Phase 3 above. Resolve `employee_id` from IBAN/account before `executor.run(...)`; pass as run parameter; persist on `pipeline_runs.employee_id_logical`.
- **PATTERN**: The existing webhook handler shape — look at how it currently constructs `trigger_payload` and add `employee_id` as a sibling parameter, not a payload field.
- **IMPORTS**: Existing audit DB connection / read helper.
- **GOTCHA**: This is a read-only join across DBs (orchestration writes runs; audit owns employees). Do not introduce a foreign key; this is a logical FK by convention (PRD §6.2).
- **VALIDATE**: `pytest tests/test_employee_attribution.py` (new file). Two cases: known IBAN → set; unknown IBAN → NULL + run still completes.

### ADD employee_id pass-through on document upload

- **IMPLEMENT**: Accept `employee_id` form field on `POST /documents/upload`; persist on `documents.employee_id`; pass to run.
- **PATTERN**: Existing multipart handler style.
- **IMPORTS**: Existing.
- **GOTCHA**: For the demo, the implementer may have hardcoded `employee_id=1`. Replace with a real form field (default to `NULL` if absent — out-of-pocket / company invoice).
- **VALIDATE**: `pytest -k document_upload_employee`.

### UPDATE transaction_booked.yaml to wire envelope decrement

- **IMPLEMENT**: Per Phase 4 above. Add `decrement_envelope` node, gated on `gating.posted`, depending on the GL poster node, with `category` resolved from the counterparty.
- **PATTERN**: Existing YAML node shape (depends_on, when, tool, inputs). Do not change the node-type system.
- **IMPORTS**: N/A (YAML).
- **GOTCHA**: Period key derivation — use `entry_date[:7]`, not `datetime.now()`, so historical/backfilled transactions go to the right month.
- **VALIDATE**: `pytest tests/test_swan_path.py` extended to assert one `budget_allocations` row exists after a successful Swan run.

### UPDATE document_ingested.yaml to wire envelope decrement

- **IMPLEMENT**: Same node, post-`build_accrual` + `gl_poster.post`, gated on `posted`.
- **PATTERN**: Mirror the Swan pipeline structure.
- **IMPORTS**: N/A.
- **GOTCHA**: For accrual entries, the *expense* line is what gets allocated to the envelope, not the AP counter-line. Confirm `journal_entry_builder.build_accrual`'s line ordering; allocate the line whose `account_code` starts with `6` (PCG expense class).
- **VALIDATE**: `pytest tests/test_document_path.py` extended to assert envelope decrement after PDF upload.

### UPDATE budget_envelope.decrement tool implementation

- **IMPLEMENT**: Per Phase 4 above — envelope lookup with employee→company fallback; `budget_allocations` insert; `envelope.decremented` event; `decision_traces` row; uncategorized skip path.
- **PATTERN**: PRD §6.4 single chokepoint; the tool must hold `ctx.write_locks["accounting"]` for the allocation insert.
- **IMPORTS**: Existing accounting DB write helper, existing event emit helper.
- **GOTCHA**: Multi-line entries. If a single journal entry has multiple expense lines (rare in MVP but possible — e.g. an invoice with VAT), decrement once per expense line, allocating each line's `debit_cents` to the same envelope. Sum to the entry's expense total.
- **VALIDATE**: `pytest -k budget_envelope`.

### CREATE transaction_released.yaml + supporting tools

- **IMPLEMENT**: Per Phase 5 above. Pipeline file, optional new tools (`build_reversal`, `mark_original_reversed`), routing.yaml entry, regression test.
- **PATTERN**: Mirror `transaction_booked.yaml` node shape; reuse `gl_poster.post`, `invariant_checker.run`.
- **IMPORTS**: Existing tool/agent registry surface.
- **GOTCHA**: Idempotency on double-reversal. The pipeline must short-circuit if the original is already `status='reversed'`. Use the existing `external_events` `(provider, event_id)` unique constraint plus a status check in `find_original_entry`.
- **VALIDATE**: `pytest tests/test_compensation_path.py`. Forward → posted → release → reversed; assert net allocations = 0; assert bank-mirror == GL.

### CREATE generic external webhook handler + verifier registry

- **IMPLEMENT**: Per Phase 6 above. `POST /external/webhook/{provider}` route; verifier registry with at least Stripe HMAC; `external_events` insert; routing lookup; default `log_and_continue` path.
- **PATTERN**: Existing Swan webhook handler — mirror the signature-verify, idempotent-insert, route-and-trigger shape.
- **IMPORTS**: `hmac`, `hashlib`, existing `external_events` write helper, existing routing.yaml loader.
- **GOTCHA**: Verify before parse. Constant-time compare (`hmac.compare_digest`).
- **VALIDATE**: `pytest tests/test_external_webhook.py`. Three cases: valid + new event triggers run; valid + duplicate event_id is no-op; valid + unknown event_type hits default.

### UPDATE Anthropic client configuration to §7.9 policy

- **IMPLEMENT**: Per Phase 7 above. Set `timeout=4.5, max_retries=2` on every `AsyncAnthropic` (15s for document extractor). Wrap targeted call sites in `try/except APITimeoutError` with deterministic fallback.
- **PATTERN**: Existing client construction; do not introduce a wrapper if one already exists.
- **IMPORTS**: `from anthropic import APITimeoutError`.
- **GOTCHA**: Per-request `timeout=` overrides override the client default. If the implementer overrides for some calls, do not silently undo it.
- **VALIDATE**: `pytest -k retry_timeout` with a fake transport that simulates timeout. Assert deterministic-fallback path runs and `agent_decisions.source='rule'`.

### UPDATE executor to emit envelope events on dashboard SSE bus

- **IMPLEMENT**: Per Phase 8 above. Detect `envelope.decremented`, `ledger.entry_posted`, `review.enqueued`; re-emit on dashboard stream via `asyncio.Queue` on app state.
- **PATTERN**: Existing per-run SSE route as the template; the dashboard stream is the same shape with a different filter.
- **IMPORTS**: `asyncio.Queue` on FastAPI app state; existing SSE response helper.
- **GOTCHA**: Backpressure. If no client is connected, the queue grows. Cap queue length (e.g. `maxsize=1000`) and drop oldest on overflow; this is acceptable for the MVP because the dashboard rings reconcile from DB on connect.
- **VALIDATE**: `pytest tests/test_dashboard_sse.py` (new file). Trigger fake Swan webhook → connect SSE client → assert events arrive in order.

### RUN full validation suite

- **IMPLEMENT**: Run all four levels of validation (see below).
- **VALIDATE**: All commands return zero exit codes; the wedge query (PRD §7.11) returns sensible numbers against the demo seed dataset.

---

## TESTING STRATEGY

Mirror the existing test framework discovered in the audit (almost certainly `pytest` + `pytest-asyncio`). Do **not** introduce a different runner.

### Unit Tests

- `test_counterparty_category_mapping.py` — Anthropic resolves to `ai_tokens`, OpenAI resolves to `ai_tokens`, unknown counterparty resolves to `uncategorized`.
- `test_employee_resolution.py` — known IBAN resolves; unknown IBAN returns NULL; company IBAN returns NULL but does not crash.
- `test_budget_envelope_decrement.py` — happy path inserts allocation; uncategorized skips; multi-line entry sums correctly; missing employee envelope falls back to company envelope.
- `test_external_webhook_verifier.py` — Stripe HMAC accepts valid; rejects tampered body; rejects wrong secret; constant-time compare path is the only one used.
- `test_anthropic_timeout_fallback.py` — slow transport triggers `APITimeoutError`; fallback runs; `agent_decisions.source='rule'`.

### Integration Tests

- `test_swan_path_with_envelope.py` — fake `Transaction.Booked` webhook → entry posted → envelope decremented → SSE events emitted → trace clickable.
- `test_compensation_path.py` — `Booked` → `Released` → reversal entry posted → net allocation = 0 → bank-mirror == GL.
- `test_document_path_with_envelope.py` — PDF upload (Anthropic invoice) → accrual entry → AI envelope decremented.
- `test_external_webhook_e2e.py` — Stripe webhook → routed to `external_event.yaml` → run completes; duplicate event_id no-ops.
- `test_dashboard_sse.py` — connect to `/dashboard/stream`, trigger Swan webhook, assert event ordering.
- `test_wedge_query.py` — given the demo seed dataset post-remediation, the wedge query (PRD §7.11) returns the expected per-employee breakdown.

### Edge Cases

- **NULL employee on company-account transactions** — wedge query must tolerate; `WHERE employee_id IS NOT NULL` in the query, not in the data.
- **Backfilled / historical transactions** — `entry_date[:7]` period derivation; do not use wall clock.
- **Double-release** (Swan sends `Released` twice) — short-circuit on `(provider, event_id)` unique; second one is a no-op.
- **Counterparty category change mid-month** — already-posted entries are not retroactively re-categorized; only future entries pick up the new category. Document this in the test as expected behavior.
- **Envelope cap exceeded** — MVP does not block; `envelope.decremented` event still fires; `used_cents > cap_cents` is allowed; dashboard ring shows red.
- **Reversal of an entry that was below confidence gate** (i.e. in review queue, not yet posted) — must be a no-op; only `posted` entries are reversible.
- **Multi-currency** — out of scope; PRD asserts EUR-only via CHECK constraint. Do not weaken this.

---

## VALIDATION COMMANDS

Discover the actual commands during the audit phase; the below assumes the conventional Python/uv/pytest stack the PRD §8 implies. If the implementer used Poetry or pip-tools, substitute accordingly.

Execute every command to ensure zero regressions and 100% feature correctness.

### Level 1: Syntax & Style

```bash
# adjust to whatever the project uses; ruff/black/mypy are the most likely
ruff check backend/
ruff format --check backend/
mypy backend/  # or pyright, whichever the project pinned
```

The CI grep audit for floats on money paths (PRD §7.7) — confirm or add if missing:

```bash
# zero matches expected on lines that touch journal_lines / budget_allocations / agent_costs
grep -rE "float\(.+(_cents|amount|cost)" backend/ && echo "FAIL: floats on money path" && exit 1 || echo "OK"
```

### Level 2: Unit Tests

```bash
pytest tests/ -k "counterparty or envelope or employee_resolution or webhook_verifier or timeout_fallback" -v
```

### Level 3: Integration Tests

```bash
pytest tests/ -k "swan_path or compensation or document_path or external_webhook_e2e or dashboard_sse or wedge_query" -v
```

The full suite:

```bash
pytest tests/ -v
```

The PRD §12 Phase B sentinel — a 5-node clean run produces exactly 12 `pipeline_events` rows. Confirm this still holds after the new envelope-decrement nodes (it will not — recompute and update the sentinel test):

```bash
pytest tests/ -k event_count_contract -v
```

### Level 4: Manual Validation

Boot the app and walk the demo path:

```bash
# whatever the project's run command is; adjust:
uv run uvicorn backend.api.main:app --workers 1 --reload
```

In another terminal:

1. Drop a fake `Transaction.Booked` webhook with a known employee IBAN (Anthropic counterparty):
   ```bash
   curl -X POST http://localhost:8000/swan/webhook -H "X-Swan-Secret: <test>" -d @tests/fixtures/swan_anthropic_debit.json
   ```
2. Open `/dashboard/stream` in a browser or via `curl -N`. Confirm `ledger.entry_posted` and `envelope.decremented` events arrive within 5s.
3. Run the wedge query against `accounting.db`:
   ```bash
   sqlite3 data/accounting.db < tests/sql/wedge_query.sql
   ```
   Expect the seed employee's `ai_tokens` envelope to show the Anthropic charge.
4. Drop a `Transaction.Released` for the same transaction id. Confirm a reversal entry posts and the envelope's net usage drops back.
5. Drop the Anthropic invoice PDF via the upload UI (Phase F frontend). Confirm an accrual entry posts and lands on the same `ai_tokens` envelope.
6. Open the trace drawer for any AI line. Confirm the chain: `journal_lines` → `decision_traces` → `agent_decisions` → source webhook or document.

### Level 5: Additional Validation (Optional)

If MCP servers / linters from the project's `claude-code` setup expose a security-review or schema-check, run them. Otherwise skip.

---

## ACCEPTANCE CRITERIA

- [ ] Every Swan webhook that has a resolvable IBAN produces a `pipeline_runs` row with `employee_id_logical` populated; unknown IBANs produce NULL but the run still completes.
- [ ] Every PDF upload flow accepts `employee_id` and persists it on `documents.employee_id`.
- [ ] Counterparty resolver writes an envelope category (or the mapping table is populated) for all PRD §15.2 seed counterparties.
- [ ] `transaction_booked.yaml` and `document_ingested.yaml` both contain a `decrement_envelope` node, gated on `posted`, with category resolved from counterparty.
- [ ] `transaction_released.yaml` exists, is registered in `routing.yaml`, and produces a reversal entry with `reversal_of_id` pointing at the original.
- [ ] Reversal entries also produce negative `budget_allocations` rows, restoring envelope net usage to its pre-charge state.
- [ ] `POST /external/webhook/{provider}` exists with at least one registered verifier (Stripe HMAC); duplicates are no-ops; unknown event types hit `defaults.unknown_event`.
- [ ] All `AsyncAnthropic` clients use `timeout=4.5, max_retries=2` (15s on the document extractor); `APITimeoutError` triggers the deterministic fallback, not a retry.
- [ ] `/dashboard/stream` emits `ledger.entry_posted`, `envelope.decremented`, and `review.enqueued` events.
- [ ] The wedge query (PRD §7.11) returns the expected per-employee, per-category breakdown against the seed dataset.
- [ ] PRD §7.6 invariants 1–5 still pass after the compensation pipeline runs.
- [ ] No regressions in any pre-existing Phase A–F test.
- [ ] No floats introduced on money paths.
- [ ] No PRD or briefing files modified.

---

## COMPLETION CHECKLIST

- [ ] Audit file produced at `Orchestration/Plans/phase-1-gap-audit.md`
- [ ] All step-by-step tasks completed in order
- [ ] Each task's validation passed before moving to the next
- [ ] All four validation levels executed successfully
- [ ] Manual demo walk-through (Level 4) completed end-to-end without intervention
- [ ] No linting, type-checking, or money-path-float errors
- [ ] Acceptance criteria all met
- [ ] PRD and briefing files unchanged (`git diff --stat Orchestration/PRDs/ "Dev orchestration/"` empty)

---

## NOTES

**On scope discipline.** This plan deliberately does not add: agent-cost token telemetry beyond what Phase C shipped; pre-check / hard-limit budget enforcement (post-hackathon per PRD §12); LangGraph; Postgres; real OAuth/SCA; per-API-key allocation; tamper-evident audit. If during implementation the temptation arises to "while we're in here, also add X," resist — those are out-of-scope per the PRD's explicit Phase 12 Post-hackathon section.

**On the "AI cost" allocation model.** The wedge ("Marie's AI spend this month") is fed by *Swan transactions to AI providers* (SEPA-out to Anthropic, card swipes for OpenAI) and by *PDF invoices for AI-provider subscriptions* (Anthropic monthly invoice → accrual). It is **not** fed by `audit.agent_costs` token rollups. `agent_costs` is implemented per the PRD for completeness/audit; this plan does not extend it. The category routing built in Phase 2 is what makes the wedge work.

**On compensation idempotency.** The single most overlooked failure mode in this plan is double-firing of `Transaction.Released`. The `(provider, event_id)` unique constraint catches duplicates at the ingress layer; the status check in `find_original_entry` catches duplicates at the pipeline layer. Both are needed: ingress catches webhook retries, pipeline catches manual replays.

**On YAML-vs-code balance.** Adding nodes to existing pipelines is YAML-only. Adding new pipelines is YAML + a routing entry. New tools are Python + a registry entry. New conditions are Python + a registry entry. Nothing in this plan changes the metalayer engine itself.

**On test order.** Run the new tests against the *pre-patch* code first to confirm they fail (red), then apply the patch and run again (green). This is not pedantry — it's the only way to catch tests that pass for the wrong reason (e.g. a test that asserts `employee_id_logical IS NOT NULL` but in fact the column is NULL on every row, just not because of resolution).

**Confidence score for one-pass success: 7/10.**

Reasons it's not 9/10:
- The audit phase carries real risk: if the Phase A–F implementation deviates significantly from PRD §6.5 directory structure, the file references in this plan become orienteering hints rather than coordinates. The plan accommodates this by being filename-agnostic, but the implementing agent will need to read code carefully before patching.
- Phases 4 and 5 (envelope decrement + compensation) depend on details of `journal_entry_builder.build_accrual` and `gl_poster.post` that are PRD-described but not PRD-mandated in their internals. If the Phase D implementer made unconventional choices (e.g. inverted debit/credit ordering), the "expense line is the one starting with `6`" heuristic in Phase 4 may need adjustment.
- The dashboard SSE phase assumes Phase F shipped *some* SSE infrastructure. If Phase F was skipped entirely, this phase grows from "add events" to "build the SSE bus + add events," which is closer to a day's work than an hour's.

Mitigations: the audit phase is mandatory and front-loaded; every later phase has a fallback path described in its Gotchas.
