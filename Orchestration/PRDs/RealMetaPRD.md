---
title: RealMetaPRD — Autonomous CFO for SMEs (Hackathon MVP, Merged Scope)
status: draft v1
date: 2026-04-25
owner: HEC Paris team — Paris Fintech Hackathon 2026
supersedes: Orchestration/PRDs/MetaPRD.md, Orchestration/PRDs/PRD1.md (this doc merges both)
companions: pennylane_vs_us.md, projectbriefing.md, architecure.md, pitch_research.md, Dev orchestration/_exports_for_b2b_accounting/*, Dev orchestration/swan/SWAN_API_REFERENCE.md
---

# RealMetaPRD — Autonomous CFO for SMEs

> **What this document is.** A merged successor to `MetaPRD.md` (the full demo
> story) and `PRD1.md` (the orchestration metalayer carve-out). Both are kept
> for reference; this is the document the implementer reads. The merge reflects
> scoping decisions taken on 2026-04-25:
>
> 1. **Three SQLite databases**, not two: `accounting.db` / `orchestration.db` /
>    `audit.db`. Decision/cost/employee tables live in `audit.db`.
> 2. **Cash vs. accrual** is a `basis` column on `journal_entries`, not two
>    schemas — accrual entries from PDF invoices, cash entries from Swan
>    settlement, with a deterministic accrual-reverse pattern when the SEPA
>    out matches.
> 3. **Allocation unit = Swan transaction**, not API-key telemetry. Anthropic
>    bills employee X's Swan account €50 → booked under "API costs / Anthropic"
>    against employee X. Notion invoice → "SaaS / Notion." Same shape.
> 4. **PDF-invoice upload is the second demo path**, simplest possible —
>    one file lands, vision extraction runs, an accrual entry posts, audit
>    trail records every step.
> 5. **External (CRM/Shopify-style) webhook ingress** is generic in the
>    schema and routing table; not wired to a specific provider in the demo.
> 6. **Three agent runners** (Anthropic / Google ADK / Pydantic AI) ship
>    pluggable; **Anthropic is the demo default**; the others exist to prove
>    the pluggability claim.

---

## 1. Executive Summary

**Pennylane gave the accountant a co-pilot. We're giving the SME founder an
autonomous CFO.** Same Swan rails, sub-second instead of sub-day, every AI
decision auditable down to the prompt, every euro that moves through a
company's Swan accounts becoming a journal line, a budget movement, and a
decision trace within seconds.

This PRD scopes the **hackathon-weekend MVP**: a meta-layer pipeline runtime
(YAML DSL + Kahn DAG executor + tool/agent/runner registries + cross-run node
cache + decision/audit recording + per-employee AI-credit ledger) that drives
two end-to-end demo paths — Swan webhook → GL → live dashboard, and PDF
invoice upload → vision extraction → accrual entry → audit trail. Both paths
use the same DAG runtime, the same registries, the same decision-trace shape.
Higher-order capabilities (campaigns, agentic DD/board reports, payment-control
hook, full PCG) compile down onto this same foundation post-hackathon.

**Core value proposition.** A meta-layer where webhooks, agent pipelines, and
ingestion paths are **data, not code**. New event types, new policies, new
report templates ship as YAML + a registered Python tool — never a redeploy
of ingress logic. Every AI decision is a queryable row in `audit.db`. Every
journal line carries a citable trace. Every euro is integer cents.

**MVP demo goal.** Within 5 seconds of a Swan `Transaction.Booked` webhook
firing, the corresponding journal entry posts to the GL with a decision
trace, the affected per-employee budget envelope decrements, and the live
dashboard updates — driven entirely by a YAML pipeline definition with no
event-type-specific Python code in the ingress path. As a second beat: a PDF
invoice (Anthropic bills employee Tim's Swan account €50) lands via drag-drop
or `POST /documents/upload`, vision extraction runs, an accrual entry posts
under "API costs / Anthropic," and the audit log shows every step from file
hash to posted entry.

---

## 2. Mission

**Mission statement.** Be the brain — not the bookkeeper — for European SMEs
that don't have a CFO. Every euro that moves through a Swan account becomes a
journal line, a budget movement, and a decision trace within seconds. Every
PDF invoice that arrives is read, classified, accrued, and matched to its
eventual settlement. Every AI decision is auditable down to the prompt.

**Core principles.**

1. **Deterministic by default, AI surgically.** Rules, exact identifier
   matches, and cache hits before the LLM. AI never does arithmetic, never
   produces journal entries directly, never bypasses the confidence gate.
   Every LLM resolution writes back to a deterministic cache so the next
   occurrence skips the model.
2. **Decision trace is non-negotiable.** Every meaningful node output carries
   a structured row in `audit.db` (`source`, `runner`, `model`, `prompt_hash`,
   `alternatives`, `confidence`, `approver_id`, `cost_micro_usd`). Built in,
   not bolted on; impossible to forget because every agent write goes through
   a single `propose → checkpoint → commit` function.
3. **Pipelines are data.** Webhooks, pipelines, conditions, and tools are
   declarative. Adding a new Swan event type or a new agent pipeline is YAML
   + one registry line, not a redeploy.
4. **Append-only, idempotent, replayable.** Every external event lands in an
   immutable table keyed on the provider's event ID (Swan `eventId`, document
   SHA256, CRM `event_id`). Pipeline runs are reconstructable from
   `(pipeline_runs ⋈ pipeline_events ⋈ agent_decisions)`.
5. **Integer cents, integer micro-USD, no floats.** Money is integer cents
   everywhere. AI cost is integer micro-USD. VAT splits use integer arithmetic
   with a documented rounding rule. Floats are an outage waiting to happen.
6. **Three layers kept separate.** Bank movements (Swan / `accounting.db`
   bank mirror), accounting entries (the GL), and counterparties (the entity
   layer) are three distinct concerns. They reference each other through
   clear foreign keys but they are not the same table, the same identifier
   space, or the same lifecycle.

---

## 3. Target Users

**Primary persona — Founder-CFO of a 5–50-person SME.** Not accounting-trained.
Owns the bank account, sets the budgets, signs the payments. Today juggles
Swan + spreadsheet + Pennylane. Wants answers, not ledgers. Wants to see at a
glance how much each employee has spent on AI tokens, SaaS, leasing, travel,
food — and how much is left.

**Secondary persona — Part-time bookkeeper / external accountant.** Reviews
flagged transactions, approves AI proposals at checkpoints, runs the close.
Cares about the audit trail. Cares that AI explains itself. Cares about cash
vs. accrual being explicit and correct.

**Tertiary persona — Hackathon judge / B2B fintech investor.** Will probe the
agentic claims. Will ask "what does the AI actually *do*?" The decision-trace
UI must answer that without hand-waving.

**Implementation persona (PRD-internal) — Future Claude / Codex implementing
Phase 2+ (campaigns, full reports, frontend hardening).** Needs stable
schemas, stable Python interfaces, and a clean contract between the metalayer
and the domain so adding a campaign engine or a DD-pack agent doesn't require
re-shaping the executor.

**Technical comfort.** Founder is product-literate but not SQL-literate.
Bookkeeper is spreadsheet-literate. Neither writes Python. The product
surface is a chat copilot, a live ledger view, an approval queue, and a
mobile envelope view (Phase 2).

---

## 4. MVP Scope

The MVP is a single weekend's work. It ships **two end-to-end demo paths**
on top of one shared runtime, plus the underlying metalayer that PRD1
described.

### ✅ In Scope

**Metalayer (the engine)**

- ✅ YAML pipeline DSL: `name`, `version`, `trigger`, `nodes` with
      `tool`/`agent` + `depends_on` + `when` (named condition) + `runner`
      (per-node runtime selector) + `cacheable`
- ✅ DAG parser using Kahn's algorithm; reject cycles at parse time
- ✅ DAG executor: per-layer `asyncio.gather`, fail-fast within a run,
      `pipeline_started` / `node_started` / `node_completed` / `node_skipped` /
      `node_failed` / `cache_hit` / `pipeline_completed` / `pipeline_failed`
      events emitted to `orchestration.db`
- ✅ `AgnesContext` dataclass propagated through every node
- ✅ Tool registry, agent registry, runner registry, named-condition
      registry — all populated by `module.path:symbol` strings; no
      filesystem-scanning decorators
- ✅ Three runner implementations: `anthropic` (default), `adk`,
      `pydantic_ai` (last two behind optional extras)
- ✅ Cross-run node cache keyed on `sha256(node_id|code_version|canonical_input)`;
      `cache_hit` event emission; `last_hit_at` / `hit_count` bookkeeping;
      no eviction in MVP
- ✅ Per-decision audit row in `audit.agent_decisions` for every model call
      (model, prompt_hash, alternatives, confidence, response_id)
- ✅ Per-decision cost row in `audit.agent_costs` (input/output/cache_read/
      cache_write tokens, `cost_micro_usd`, `employee_id`)
- ✅ The wedge SQL works on day one:
      `SELECT employee_id, SUM(cost_micro_usd) FROM agent_costs WHERE month=...`

**Demo path A — Swan webhook → GL → dashboard**

- ✅ Swan webhook ingress endpoint (`POST /swan/webhook`) — signature verify,
      `INSERT OR IGNORE` into `orchestration.external_events` (provider='swan',
      event_id=Swan `eventId`), enqueue pipeline via `routing.yaml`, return
      200 within ~50ms
- ✅ Swan OAuth2 client (`client_credentials`, token cache, refresh-on-401)
- ✅ Swan GraphQL re-query of `transaction(id)` and `account(id)` from
      every booking pipeline (the webhook payload only carries IDs)
- ✅ Counterparty resolution cascade — IBAN exact → MCC + merchant ID →
      fuzzy name (rapidfuzz, threshold 85) → AI fallback. Every AI hit
      writes back to `accounting.counterparty_identifiers`.
- ✅ GL account classification cascade — `account_rules` lookup (by
      counterparty kind, MCC, type) → AI fallback constrained to existing
      chart of accounts → review queue on validation failure
- ✅ Deterministic journal-entry builder for the booking patterns this MVP
      needs (card spend, SEPA-in, SEPA-out, fee, internal). AI never enters
      this stage.
- ✅ Confidence gate (multiplicative; floor 0.50 by default; tunable per
      `confidence_thresholds`) routes weak runs to a review queue
- ✅ Hard invariants asserted at write time: `SUM(debits)=SUM(credits)` per
      entry; recorded bank-mirror balance equals re-queried Swan booked
      balance after every post
- ✅ One pipeline file: `pipelines/transaction_booked.yaml` plus a
      compensation pipeline `transaction_released.yaml` for `Released` /
      `Canceled`
- ✅ Per-employee budget envelope decrement after a successful post
      (`budget_envelopes`, `budget_allocations` — minimum-viable Domain F)
- ✅ Live dashboard via SSE — `/runs/{run_id}/stream` and a top-level
      `/dashboard/stream` that pushes ledger + envelope deltas
- ✅ Demo seed: 3 employees × 1 personal Swan account each + 1 shared
      company account; 12 months of synthetic transactions; one Anthropic
      supplier and one Notion supplier already in `counterparties`

**Demo path B — PDF invoice upload → accrual entry → audit log**

- ✅ Single endpoint: `POST /documents/upload` (multipart) **OR** drag-drop
      on the dashboard (one file at a time). SHA256 of the file is the
      idempotency key.
- ✅ `documents` row inserted; raw blob stored under `data/blobs/<sha256>`
- ✅ Pipeline `pipelines/document_ingested.yaml` runs:
        extract (Claude vision, strict JSON schema) →
        validate (line items sum to invoice total in cents; if not → review) →
        resolve counterparty (cache → fuzzy → AI fallback) →
        classify GL account (rule → AI fallback) →
        build accrual entry (Debit: Expense + VAT deductible; Credit:
            Supplier AP — `journal_entries.basis = 'accrual'`) →
        post + write decision trace
- ✅ Accrual-reverse pattern: when a future SEPA-out matches the supplier
      invoice (Pipeline 1 picks it up), the system posts a paired entry
      (`basis='cash'`) and reverses the AP side, all in one transaction.
      The architecture doc's seven booking patterns are the canonical
      reference; this MVP implements the four most demo-relevant.
- ✅ Audit trail visible in the UI: click a journal line → see the
      `decision_traces` row → click through to `agent_decisions` (model,
      prompt_hash, alternatives, confidence) and `pipeline_events` (every
      node's input/output) and `agent_costs` (token spend per call)

**External CRM webhook ingress (generic)**

- ✅ Same `POST /external/webhook/{provider}` endpoint pattern; signature
      verification is a per-provider tool registered in
      `tools/external_webhooks/`; `external_events` row keyed on
      `(provider, event_id)`; routing through `routing.yaml`
- ✅ Schema and routing prove the pattern; no concrete provider wired in
      the live demo. One slide in the pitch says "any CRM, Shopify, Stripe,
      etc., lands here — same DAG runtime, same audit trail."

**Persistence — three SQLite databases**

- ✅ `accounting.db` — domain truth: bank mirror, counterparties + identifiers,
      documents + line items, journal entries (with `basis` column), journal
      lines, decision traces (FK to `journal_lines.id`, logical FK to
      `audit.agent_decisions.id`), chart of accounts, account rules, VAT rates,
      budget envelopes, budget allocations, expected payments, employees-as-
      counterparties view
- ✅ `orchestration.db` — pipeline runtime: `pipeline_runs`, `pipeline_events`
      (append-only), `external_events` (idempotency boundary), `node_cache`
      (cross-run), `_migrations`
- ✅ `audit.db` — agent observability: `agent_decisions`, `agent_costs`,
      `employees` (canonical roster; matched to Swan accounts via
      `swan_account_id` / `swan_iban`), `_migrations`
- ✅ All three DBs opened with `WAL`, `foreign_keys=ON`, `busy_timeout=5000`,
      `BEGIN IMMEDIATE` for writes, per-DB `asyncio.Lock` for single-writer
      discipline (one writer per DB, three locks total)
- ✅ `_migrations` table on each DB from day one; bootstrap-replay round-trip
      test (schema-from-bootstrap == schema-from-migration-replay)

**Frontend (just enough for the demo)**

- ✅ Vite + React + TypeScript single-page app
- ✅ Live ledger view (SSE-driven; rows animate in)
- ✅ Per-employee envelope rings (food / travel / SaaS / AI tokens)
- ✅ Drag-drop PDF upload zone
- ✅ Click any journal line → drill panel with decision trace + Swan event +
      pipeline run + agent decisions + agent costs
- ✅ Review queue (low-confidence entries; one-click approve writes
      `approver_id` + `approved_at` to the trace and posts the entry)

### ❌ Out of Scope

**Domain breadth (deferred)**

- ❌ Goal-driven spending campaigns engine (Phase 3)
- ❌ Agentic DD pack / board pack / management commentary generators (Phase 4)
- ❌ Payment-control synchronous hook (`/swan/payment-control`, 1.5s budget)
      — endpoint stub may exist to claim the URL, but no real logic ships
- ❌ Per-API-key AI cost allocation. Allocation unit in MVP is
      *Swan-transaction-from-Anthropic*, not API-key telemetry.
- ❌ Campaign engine driving Swan card limits via `updateCard`
- ❌ Active treasury actions (sweep, FX, surplus push)
- ❌ E-invoicing Plateforme Agréée certification
- ❌ Full French Plan Comptable Général (subset only — see Appendix)
- ❌ Multi-currency, multi-entity consolidation
- ❌ Email / IMAP / Gmail document inbox (drag-drop + endpoint only)
- ❌ Generic OCR fallback for receipts (Claude vision only; failed
      extraction → review queue)

**Runtime / infrastructure (deferred)**

- ❌ Pipeline replay (`replay_pipeline(run_id)`) — the schema supports it;
      ship the function in Phase 2
- ❌ Human-in-the-loop polling tool (`decision_pending` + `wait_for_decision`)
      — review queue UI does the simple version; richer HITL is Phase 3
- ❌ Cache eviction policies (TTL, LRU)
- ❌ FTS5 over `pipeline_events.data`
- ❌ Tamper-evident audit (mirror to S3 Object Lock / QLDB)
- ❌ Postgres / multi-tenant production hardening
- ❌ LangGraph integration (the Anthropic SDK + named-condition pattern is
      the explicit MVP lock-in; reconsider only at Phase 3 if campaigns
      outgrow it)
- ❌ Production OAuth, real SCA flows on stage (mocked / sandbox)

---

## 5. User Stories

### Primary

**US-1 — Live ledger from Swan webhook.**
As a **founder**, I want a transaction that hits my Swan account to appear
in my live ledger within 5 seconds, **so that** my balance sheet is never
out of date when I'm making a decision.
*Example:* a customer pays €4,950 by SEPA at 14:03:12; by 14:03:16 the GL
shows the entry, the AR envelope drops by €4,950, and the dashboard
animates the new row.

**US-2 — Drag-drop a PDF invoice, see it accrued.**
As a **founder**, I want to drag an Anthropic invoice onto the dashboard and
see it land in the books as an accrual against the supplier, **so that**
my P&L reflects services consumed, not just cash paid.
*Example:* I drop `anthropic_2026_03.pdf` (€50, billed to tim@). The audit
trail shows: file SHA, Claude vision extraction (model, prompt_hash, line
items, confidence 0.94), counterparty match (`Anthropic` via vendor
identifier), GL account `626100` (rule R-fixed), accrual entry posted under
`basis='accrual'`. When the matching SEPA-out clears later, the system
reverses the AP and posts the cash entry — same trace, two booking events
linked.

**US-3 — Click any number, see the why.**
As a **founder or auditor**, I want to click any journal line and see model,
prompt hash, alternatives, confidence, and cost, **so that** I can trust
the books and contest a wrong call.
*Example:* I tap a line; I see "matched counterparty 'Acme SAS' via IBAN
exact (confidence 1.0); GL account 706000 via rule R-12 (confidence 1.0);
no LLM was called." For an AI-touched line: "Claude Sonnet 4.6, prompt hash
`b3a…`, alternatives [`706000` 0.87, `707000` 0.62], confidence 0.87, cost
$0.0023." Click the cost: see the decomposition by token bucket.

**US-4 — Correct the AI once, never again.**
As a **founder**, I want to correct the AI once and have it stick **so that**
the same merchant never gets miscategorized again.
*Example:* I rename "ACM CORP" → "Acme Corp"; the system writes a row to
`accounting.counterparty_identifiers` with `source='user'`; next inbound
transaction hits the cache, no LLM, audit log shows `cache_hit`.

**US-5 — Per-employee spend at a glance.**
As a **founder**, I want to see how much each employee has spent this month
across food, travel, SaaS, AI tokens, leasing, **so that** I can spot
anomalies and have informed conversations.
*Example:* dashboard shows three employees, each with five envelope rings.
Tim's "AI tokens" ring shows €127 / €200, with three contributions: a Swan
card swipe at OpenAI for €23, a SEPA-out to Anthropic for €54, and an
Anthropic invoice (accrual) for €50.

**US-6 — Per-employee AI-credit ledger query, in SQL.**
As **finance**, I want every model call linked to the employee whose action
triggered it, **so that** "Anthropic billed us $3,712 in March" can be
split per-employee by SQL alone.
*Example:* `SELECT e.email, SUM(c.cost_micro_usd)/1e6 AS usd_this_month
FROM audit.agent_costs c JOIN audit.employees e ON e.id=c.employee_id
WHERE strftime('%Y-%m', c.created_at)='2026-03' GROUP BY e.id;` returns
one row per employee, sorted descending.

**US-7 — Confidence gate routes the unsure to me.**
As a **bookkeeper**, I want low-confidence entries queued for my review
**so that** I see what the AI was unsure about and the books still post
the rest.
*Example:* a novel merchant arrives; classification confidence is 0.62;
the entry lands in `needs_review` with the trace; the rest of the day's
transactions post automatically. I click approve; trace gets `approver_id`
and `approved_at`; entry posts.

**US-8 — Idempotent and out-of-order tolerant.**
As an **SRE**, I want every external event keyed on its provider event ID
with at-least-once safety, **so that** webhook redelivery never doubles
a journal entry and out-of-order delivery (`Booked` before `Pending`)
never breaks the books.

### Technical / engineer-facing

**US-9 — Add a new event type by writing YAML.**
As a **backend engineer**, I want to add a new Swan or external event type
by writing one YAML pipeline and registering one Python tool, **so that**
I'm not editing the ingress controller.
*Example:* `Card.Created` arrives; I drop `pipelines/card_lifecycle.yaml`
and register `CardLifecycleTool`; one line in `routing.yaml`. Total LOC
change in `executor.py`, `dag.py`, `cache.py`, registries: 0.

**US-10 — Named conditions, not stringly-typed expressions.**
As a **pipeline author**, I want named conditions
(`when: conditions.gating:needs_review`) instead of expression strings
**so that** branch logic is testable Python and grep-able.

**US-11 — Per-node runner choice.**
As an **agent author**, I want to pick the runtime per node
(`runner: pydantic_ai`) without touching the executor, **so that** runtime
choice is local and reversible.

**US-12 — Future-proof seam to Phase 2.**
As a **future Claude implementing Phase 2**, I want a stable
`pipeline_runs.id → audit.agent_decisions.id → accounting.journal_lines.id`
chain across the three databases, **so that** I can join them without
re-shaping anything.

---

## 6. Core Architecture & Patterns

### 6.1 High-level architecture

```
┌──────────────────────────────────────────────────────────────────────┐
│  AGENT RUNTIME — planner, registries, named-condition gates,         │
│  decision-trace recording. Three runners pluggable per node:         │
│  Anthropic SDK (default) | Google ADK | Pydantic AI                  │
└──────────────────────────────────────────────────────────────────────┘
        │                     │                       │
        ▼                     ▼                       ▼
 ┌─────────────┐       ┌─────────────┐         ┌─────────────┐
 │  Ingress &  │       │  Classify   │         │  Reports &  │
 │  Injection  │       │  & Post     │         │  Read /     │
 │  pipelines  │       │  pipelines  │         │  Copilot    │
 │ (Swan,      │       │ (cash and   │         │  (Phase 2,  │
 │  documents, │       │  accrual,   │         │  read-only) │
 │  external)  │       │  invariants)│         │             │
 └─────────────┘       └─────────────┘         └─────────────┘
        │                     │                       │
        └───────────┬─────────┴───────────────────────┘
                    ▼
   ┌──────────────────────────────────────────────────────────┐
   │  audit.db                                                │
   │  agent_decisions ← agent_costs ← employees               │
   │  Every agent write through propose→checkpoint→commit     │
   └──────────────────────────────────────────────────────────┘
                    │  (logical FK on line_id_logical)
                    ▼
   ┌──────────────────────────────────────────────────────────┐
   │  accounting.db                                           │
   │  GL (basis: cash | accrual)  ← Budget envelopes          │
   │  Counterparties + identifiers cache                      │
   │  Documents + line items + expected payments              │
   │  Bank mirror (Swan transactions)                         │
   └──────────────────────────────────────────────────────────┘

   ┌──────────────────────────────────────────────────────────┐
   │  orchestration.db                                        │
   │  pipeline_runs ⟶ pipeline_events (append-only)           │
   │  external_events (provider, event_id) idempotency        │
   │  node_cache (cross-run, content-addressed)               │
   └──────────────────────────────────────────────────────────┘
```

Read top-to-bottom: a Swan webhook or a PDF upload triggers a pipeline; the
DAG executor walks named-condition-gated nodes layer by layer; tools and
agents resolve through registries; every agent call writes to `audit.db`
(with cost), every pipeline event writes to `orchestration.db`, every domain
write hits `accounting.db`. The three DBs are joined logically through
`run_id` (orchestration↔audit) and `line_id_logical` (audit↔accounting).
SQLite cannot enforce cross-DB foreign keys; we enforce by convention plus
a CI test that scans for orphaned references.

### 6.2 Why three databases

| DB | Role | Primary writers | Replay shape |
|---|---|---|---|
| `accounting.db` | Domain truth — books, entities, documents, budgets | Tools (deterministic) | Reverse + repost; never delete |
| `orchestration.db` | Runtime journal — runs, events, ingress, cache | Executor + ingress | Append-only; replay reads from here |
| `audit.db` | Agent observability — decisions, cost, employees | Runners + cost helper | Append-only; the EU AI Act story |

Splitting them isolates blast radius (a runaway agent runner can't corrupt
the GL), keeps the books query-clean (no AI noise in `accounting.db`), and
makes the audit story crisp ("here is the database that records every AI
decision we ever made"). The cost is two cross-DB seams instead of one.

### 6.3 Cash vs. accrual — one column, two views

`journal_entries.basis` is `'cash' | 'accrual'`. The deterministic builder
emits one or the other based on the source pipeline. When a SEPA-out
matches a previously-accrued supplier invoice, the system posts a paired
entry in one transaction:

```
-- accrual was: Dr Expense + VAT deductible; Cr Supplier AP
-- now post:    Dr Supplier AP; Cr Bank
-- both entries share an `accrual_link_id`
```

Reports filter on `basis`. The dashboard shows cash by default; a toggle
exposes the accrual view. A combined "true P&L" view UNIONs both with a
de-duplication rule so paired entries don't double-count.

### 6.4 Design patterns

- **Append-only event tables.** `swan_transactions` (in `accounting.db`,
  bank-mirror reflection), `external_events` (in `orchestration.db`,
  idempotency boundary keyed on `(provider, event_id)`), `pipeline_runs`,
  `pipeline_events`, `agent_decisions`, `agent_costs`. No UPDATE outside
  `pipeline_runs.status` and
  `completed_at`. No DELETE. Replay is reading them back.
- **Topologically-ordered DAG executor.** Kahn's algorithm at parse time
  (cycles → `PipelineLoadError`); layers run in parallel via
  `asyncio.gather(..., return_exceptions=True)`; first exception in a
  layer triggers `pipeline_failed` and cancels remaining work in the run.
- **Tool/agent/runner/condition registries over decorators.** Plain
  `dict[str, str]` mapping a registry key → `module.path:symbol`. Lazy
  import on first call. No filesystem-scanning magic.
- **Cache-warmer cascade.** Resolution stages — exact identifier → curated
  synonym → fuzzy match → LLM fallback. Each LLM resolution writes back to
  `accounting.counterparty_identifiers` or `accounting.account_rules` so
  the next occurrence skips the LLM.
- **Compound (multiplicative) confidence.** A pipeline's overall confidence
  is the product, not the average, of its node confidences. Weak links
  collapse the chain; this is the surfacing mechanism for "needs review."
  Missing confidence is treated as 0.5 (unknown = half-trust).
  `RefusalEngine.CONFIDENCE_FLOOR = 0.50` by default; tunable per
  `confidence_thresholds` row.
- **Named conditions.** `when:` references a registered Python function
  `def cond(ctx) -> bool`; never an expression string. Conditions are
  pure, unit-tested, and grep-able.
- **Cross-run node cache.** Content-addressed by
  `sha256(node_id|code_version|canonical(input))`. Tools opt in via
  `cacheable: true`. Agent runs (LLM calls) are **not** cross-run cached
  by default — Anthropic's 5-min ephemeral prompt cache covers within-call
  reuse; cross-run cache without a richer policy is unsafe.
- **`propose → checkpoint → commit`** for every agent write. The agent
  produces a candidate (`propose`); the framework writes
  `agent_decisions` + `agent_costs` (`checkpoint`); the deterministic
  layer applies the change to `accounting.db` (`commit`). Three steps,
  three rows, never skipped.

### 6.5 Directory structure

```
backend/
  api/
    main.py                  # FastAPI app: webhooks, uploads, run triggers, SSE
    swan_webhook.py          # /swan/webhook — verify, idempotent insert, route
    external_webhook.py      # /external/webhook/{provider} — generic
    documents.py             # /documents/upload — multipart, SHA256, blob store
    runs.py                  # /pipelines/run/{name}, /runs/{id}, /runs/{id}/stream
    dashboard.py             # /dashboard/stream — top-level SSE
  orchestration/
    context.py               # AgnesContext dataclass
    dag.py                   # Kahn parser, topo layers, cycle detection
    executor.py              # async layer-by-layer runner
    registries.py            # _TOOL_REGISTRY, _AGENT_REGISTRY, _RUNNER_REGISTRY,
                             #   _CONDITION_REGISTRY
    cache.py                 # cross-run node cache
    cost.py                  # token → micro_usd helpers
    runners/
      base.py                # AgentRunner Protocol + AgentResult
      anthropic_runner.py    # raw AsyncAnthropic — default
      adk_runner.py          # Google ADK InMemoryRunner — optional
      pydantic_ai_runner.py  # Pydantic AI — optional
    store/
      bootstrap.py           # opens all three DBs with PRAGMAs
      writes.py              # async-locked single-writer helpers
      schema/
        accounting.sql       # canonical bootstrap
        orchestration.sql
        audit.sql
      migrations/
        accounting/0001_init.py …
        orchestration/0001_init.py …
        audit/0001_init.py …
    pipelines/
      transaction_booked.yaml
      transaction_released.yaml
      document_ingested.yaml
      external_event.yaml          # generic CRM/Shopify-style entry
    tools/                          # deterministic, all Python; cacheable opt-in
      swan_query.py                # GraphQL re-fetch
      counterparty_resolver.py     # cascade with cache writeback
      gl_account_classifier.py     # rule lookup; AI handoff if no hit
      journal_entry_builder.py     # 4 booking patterns; cash/accrual aware
      gl_poster.py                 # write to accounting.db with invariants
      invariant_checker.py         # SUM(D)=SUM(C); balance reconcile
      budget_envelope.py           # decrement after post
      review_queue.py              # enqueue weak runs
      document_extractor.py        # Claude vision wrapper (callable as tool)
      external_payload_parser.py   # generic CRM event → expected_payment
    agents/                         # LLM-backed; one per agent class
      counterparty_classifier.py
      gl_account_classifier_agent.py
      document_extractor_agent.py
    conditions/
      gating.py                    # passes_confidence, needs_review, posted, …
      counterparty.py              # counterparty_unresolved, …
    swan/
      oauth.py                     # client_credentials cache, refresh-on-401
      graphql.py                   # gql/httpx client, mutation error union helper
  ingress/
    routing.yaml                   # event_type → pipeline_name(s)
  data/
    blobs/                         # PDF storage (sha256 → file)
    accounting.db
    orchestration.db
    audit.db
  tests/
    test_executor.py
    test_dag.py
    test_cache.py
    test_audit.py
    test_cost.py
    test_invariants.py
    test_idempotency.py
    test_swan_path.py              # fake webhook → posted entry
    test_document_path.py          # fake PDF → accrual entry
    test_routing.py
    test_employee_ledger.py
frontend/
  src/
    main.tsx, App.tsx
    components/Ledger.tsx
    components/EnvelopeRings.tsx
    components/UploadZone.tsx
    components/TraceDrawer.tsx
    components/ReviewQueue.tsx
    lib/sse.ts
```

### 6.6 Concurrency & write discipline

Three `asyncio.Lock` instances (one per DB) held across `BEGIN IMMEDIATE`
→ `COMMIT`. Tools and runners pass `AgnesContext`; the context exposes
`async with ctx.write_locks["accounting"]: …` etc. PRAGMAs on connection
open:

```sql
PRAGMA journal_mode       = WAL;
PRAGMA foreign_keys       = ON;
PRAGMA synchronous        = NORMAL;        -- REF-SQLITE-BACKBONE:209
PRAGMA busy_timeout       = 5000;
PRAGMA temp_store         = MEMORY;        -- REF-SQLITE-BACKBONE:213
PRAGMA cache_size         = -65536;        -- 64 MB; REF-SQLITE-BACKBONE:214
PRAGMA mmap_size          = 134217728;     -- 128 MB; REF-SQLITE-BACKBONE:215
PRAGMA wal_autocheckpoint = 1000;
PRAGMA journal_size_limit = 67108864;      -- 64 MB
```

Default `synchronous=FULL` fsyncs every commit — at our event volumes
that's ~10× slower than `NORMAL` with WAL, and `NORMAL` is durability-
safe for crash recovery. `cache_size`, `mmap_size`, `temp_store` matter
specifically for the rollup queries the dashboard issues over
`agent_costs` and `journal_lines`.

---

## 7. Tools / Features

### 7.1 Webhook ingress (Swan)

`POST /swan/webhook` — async event ingress.

- Verify `x-swan-secret` (constant-time compare with persisted subscription
  secret). 401 on mismatch.
- `INSERT OR IGNORE` into `orchestration.external_events` (provider='swan',
  event_id=Swan `eventId`); `processed=0`.
- Look up `eventType` in `ingress/routing.yaml`; resolve to one or more
  pipeline names. Unknown event type → `log_and_continue` default route.
- Fire the pipeline(s) via `asyncio.create_task`; return `200` within
  ~50ms target, 10s hard ceiling.
- Out-of-order tolerant by design: only `Transaction.Booked` produces a
  posted journal entry; `Pending` → `Upcoming` are no-ops; `Released` /
  `Canceled` → compensation pipeline reverses if previously posted.

Routing table (`ingress/routing.yaml`):

```yaml
routes:
  swan.Transaction.Booked:    [transaction_booked]
  swan.Transaction.Released:  [transaction_released]
  swan.Transaction.Canceled:  [transaction_released]
  swan.Transaction.Enriched:  [transaction_reclassify]
  swan.Card.Created:          [card_lifecycle]
  swan.Account.Updated:       [reconcile_balance]

  document.uploaded:          [document_ingested]

  external.crm.invoice_paid:  [external_invoice_paid]
  external.shop.order_paid:   [external_order_paid]

defaults:
  unknown_event: [log_and_continue]
```

### 7.2 Webhook ingress (external / CRM)

`POST /external/webhook/{provider}` — generic.

- Provider is path-routed (`shopify`, `hubspot`, `stripe`, …); a registered
  per-provider verifier checks signature.
- `INSERT OR IGNORE` into `external_events` keyed on `(provider, event_id)`
  with the raw envelope.
- Routing by `external.{provider}.{event_type}` in `routing.yaml`.
- The MVP demo does **not** wire a specific provider in the live walk-through.
  A unit test exercises the generic path with a fake provider to prove the
  pattern works end-to-end.

### 7.3 PDF document ingress

`POST /documents/upload` (multipart; or drag-drop in the dashboard).

- Compute SHA256 of the file. `INSERT OR IGNORE` into `accounting.documents`
  keyed on `sha256` (idempotency boundary). Save the blob to
  `data/blobs/<sha256>`.
- Fire `document_ingested` pipeline.
- Pipeline body:

```yaml
name: document_ingested
version: 1
trigger: { source: external_event:document.uploaded }
nodes:
  - id: extract
    agent: agents.document_extractor:run
    runner: anthropic
    cacheable: false

  - id: validate
    tool: tools.document_extractor:validate_totals
    depends_on: [extract]

  - id: needs-review-on-bad-totals
    tool: tools.review_queue:enqueue
    depends_on: [validate]
    when: conditions.documents:totals_mismatch

  - id: resolve-counterparty
    tool: tools.counterparty_resolver:run
    depends_on: [validate]
    when: conditions.documents:totals_ok
    cacheable: true

  - id: ai-counterparty-fallback
    agent: agents.counterparty_classifier:run
    depends_on: [resolve-counterparty]
    when: conditions.counterparty:unresolved

  - id: classify-gl-account
    tool: tools.gl_account_classifier:run
    depends_on: [resolve-counterparty, ai-counterparty-fallback]
    cacheable: true

  - id: ai-account-fallback
    agent: agents.gl_account_classifier_agent:run
    depends_on: [classify-gl-account]
    when: conditions.gl:unclassified

  - id: build-accrual-entry
    tool: tools.journal_entry_builder:build_accrual
    depends_on: [classify-gl-account, ai-account-fallback]

  - id: gate-confidence
    tool: tools.confidence_gate:run
    depends_on: [build-accrual-entry]

  - id: post-entry
    tool: tools.gl_poster:post
    depends_on: [build-accrual-entry, gate-confidence]
    when: conditions.gating:passes_confidence

  - id: queue-review
    tool: tools.review_queue:enqueue
    depends_on: [build-accrual-entry, gate-confidence]
    when: conditions.gating:needs_review

  - id: assert-balance
    tool: tools.invariant_checker:run
    depends_on: [post-entry]
    when: conditions.gating:posted
```

### 7.4 Swan transaction booking pipeline

```yaml
name: transaction_booked
version: 1
trigger: { source: external_event:swan.Transaction.Booked }
nodes:
  - id: fetch-transaction
    tool: tools.swan_query:fetch_transaction
    cacheable: false   # always re-fetch from Swan

  - id: resolve-counterparty
    tool: tools.counterparty_resolver:run
    depends_on: [fetch-transaction]
    cacheable: true

  - id: ai-counterparty-fallback
    agent: agents.counterparty_classifier:run
    depends_on: [resolve-counterparty]
    when: conditions.counterparty:unresolved

  - id: classify-gl-account
    tool: tools.gl_account_classifier:run
    depends_on: [resolve-counterparty, ai-counterparty-fallback]
    cacheable: true

  - id: ai-account-fallback
    agent: agents.gl_account_classifier_agent:run
    depends_on: [classify-gl-account]
    when: conditions.gl:unclassified

  - id: match-accrual
    tool: tools.journal_entry_builder:match_accrual
    depends_on: [classify-gl-account, ai-account-fallback]
    # Tries to find a previously-accrued supplier invoice that this
    # SEPA-out matches; returns {accrual_link_id} or {}.

  - id: build-cash-entry
    tool: tools.journal_entry_builder:build_cash
    depends_on: [match-accrual]
    # If accrual_link_id present, builds the AP-reversal pair.

  - id: gate-confidence
    tool: tools.confidence_gate:run
    depends_on: [build-cash-entry]

  - id: post-entry
    tool: tools.gl_poster:post
    depends_on: [build-cash-entry, gate-confidence]
    when: conditions.gating:passes_confidence

  - id: queue-review
    tool: tools.review_queue:enqueue
    depends_on: [build-cash-entry, gate-confidence]
    when: conditions.gating:needs_review

  - id: assert-balance
    tool: tools.invariant_checker:run
    depends_on: [post-entry]
    when: conditions.gating:posted

  - id: decrement-envelope
    tool: tools.budget_envelope:decrement
    depends_on: [post-entry]
    when: conditions.gating:posted
```

### 7.5 SQLite schemas (the seam)

#### `accounting.db`

```sql
-- Bank mirror (Domain A): faithful Swan reflection
CREATE TABLE swan_transactions (
    id                   TEXT PRIMARY KEY,         -- Swan's transaction id
    swan_event_id        TEXT NOT NULL,
    side                 TEXT NOT NULL,            -- 'Debit' | 'Credit'
    type                 TEXT NOT NULL,            -- subtype string from Swan
    status               TEXT NOT NULL,            -- 'Booked' | 'Pending' | …
    amount_cents         INTEGER NOT NULL,
    currency             TEXT NOT NULL,            -- 'EUR' enforced
    counterparty_label   TEXT,
    payment_reference    TEXT,
    external_reference   TEXT,
    execution_date       TEXT NOT NULL,
    booked_balance_after INTEGER,
    raw                  TEXT NOT NULL,            -- normalized JSON
    created_at           TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CHECK (currency = 'EUR'),
    CHECK (json_valid(raw))
) STRICT;
CREATE INDEX idx_swan_tx_status ON swan_transactions(status);
CREATE INDEX idx_swan_tx_date   ON swan_transactions(execution_date);

-- Entity layer (Domain B)
CREATE TABLE counterparties (
    id           INTEGER PRIMARY KEY,
    legal_name   TEXT NOT NULL,
    kind         TEXT NOT NULL,        -- 'customer' | 'supplier' | 'employee' |
                                       --   'tax_authority' | 'bank' | 'internal'
    primary_iban TEXT,
    vat_number   TEXT,
    confidence   REAL,
    sources      TEXT,                 -- JSON array
    created_at   TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
) STRICT;

CREATE TABLE counterparty_identifiers (
    id              INTEGER PRIMARY KEY,
    counterparty_id INTEGER NOT NULL REFERENCES counterparties(id),
    identifier_type TEXT NOT NULL,     -- 'iban' | 'vat' | 'mcc' | 'merchant_id' |
                                       --   'email_domain' | 'name_alias'
    identifier      TEXT NOT NULL,
    source          TEXT NOT NULL,     -- 'rule' | 'config' | 'ai' | 'user'
    confidence      REAL,
    created_at      TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (identifier_type, identifier)
) STRICT;

-- Documents (Domain C)
CREATE TABLE documents (
    id              INTEGER PRIMARY KEY,
    sha256          TEXT NOT NULL UNIQUE,
    kind            TEXT NOT NULL,        -- 'invoice_in' | 'invoice_out' |
                                          --   'receipt' | 'contract'
    direction       TEXT NOT NULL,        -- 'inbound' | 'outbound'
    counterparty_id INTEGER REFERENCES counterparties(id),
    amount_cents    INTEGER,
    vat_cents       INTEGER,
    issue_date      TEXT,
    due_date        TEXT,
    employee_id     INTEGER,              -- logical FK → audit.employees.id
    extraction      TEXT,                 -- JSON of full extraction
    blob_path       TEXT NOT NULL,        -- data/blobs/<sha256>
    created_at      TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CHECK (extraction IS NULL OR json_valid(extraction))
) STRICT;

CREATE TABLE document_line_items (
    id          INTEGER PRIMARY KEY,
    document_id INTEGER NOT NULL REFERENCES documents(id),
    description TEXT,
    amount_cents INTEGER NOT NULL,
    vat_rate_bp INTEGER,                  -- VAT rate in basis points (2000 = 20%)
    gl_hint     TEXT
) STRICT;

CREATE TABLE expected_payments (
    id              INTEGER PRIMARY KEY,
    direction       TEXT NOT NULL,        -- 'inbound' | 'outbound'
    counterparty_id INTEGER NOT NULL REFERENCES counterparties(id),
    document_id     INTEGER REFERENCES documents(id),
    amount_cents    INTEGER NOT NULL,
    due_date        TEXT,
    status          TEXT NOT NULL,        -- 'open' | 'partial' | 'paid' |
                                          --   'overdue' | 'written_off'
    created_at      TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
) STRICT;

-- GL (Domain D)
CREATE TABLE chart_of_accounts (
    code      TEXT PRIMARY KEY,           -- e.g. '512', '401', '626100'
    name      TEXT NOT NULL,
    type      TEXT NOT NULL,              -- 'asset' | 'liability' | 'equity' |
                                          --   'revenue' | 'expense' | 'contra'
    parent    TEXT REFERENCES chart_of_accounts(code)
) STRICT;

CREATE TABLE journal_entries (
    id                INTEGER PRIMARY KEY,
    basis             TEXT NOT NULL,      -- 'cash' | 'accrual'
    entry_date        TEXT NOT NULL,
    description       TEXT,
    source_pipeline   TEXT NOT NULL,
    source_run_id     INTEGER NOT NULL,   -- logical FK → orchestration.pipeline_runs.id
    status            TEXT NOT NULL,      -- 'draft' | 'posted' | 'reversed'
    accrual_link_id   INTEGER REFERENCES journal_entries(id),
                                          -- pairs cash and accrual entries
    reversal_of_id    INTEGER REFERENCES journal_entries(id),
                                          -- explicit reversal pointer
    created_at        TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CHECK (basis IN ('cash','accrual'))
) STRICT;

CREATE TABLE journal_lines (
    id                  INTEGER PRIMARY KEY,
    entry_id            INTEGER NOT NULL REFERENCES journal_entries(id),
    account_code        TEXT NOT NULL REFERENCES chart_of_accounts(code),
    debit_cents         INTEGER NOT NULL DEFAULT 0,
    credit_cents        INTEGER NOT NULL DEFAULT 0,
    counterparty_id     INTEGER REFERENCES counterparties(id),
    swan_transaction_id TEXT REFERENCES swan_transactions(id),
    document_id         INTEGER REFERENCES documents(id),
    description         TEXT,
    CHECK (debit_cents >= 0 AND credit_cents >= 0),
    CHECK (NOT (debit_cents > 0 AND credit_cents > 0))
) STRICT;
CREATE INDEX idx_lines_entry  ON journal_lines(entry_id);
CREATE INDEX idx_lines_account ON journal_lines(account_code);

CREATE TABLE decision_traces (
    id              INTEGER PRIMARY KEY,
    line_id         INTEGER NOT NULL REFERENCES journal_lines(id),
    source          TEXT NOT NULL,        -- 'webhook' | 'agent' | 'rule' | 'human'
    rule_id         TEXT,
    confidence      REAL,
    -- cross-DB seam to audit.agent_decisions:
    agent_decision_id_logical TEXT,
    parent_event_id TEXT,                 -- swan_event_id or document.sha256
    approver_id     INTEGER,              -- logical FK → audit.employees.id
    approved_at     TEXT,
    created_at      TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
) STRICT;
CREATE INDEX idx_traces_line ON decision_traces(line_id);

-- Configuration / policy (Domain E)
CREATE TABLE account_rules (
    id              INTEGER PRIMARY KEY,
    pattern_kind    TEXT NOT NULL,        -- 'mcc' | 'counterparty' | 'iban' |
                                          --   'merchant_name'
    pattern_value   TEXT NOT NULL,
    gl_account      TEXT NOT NULL REFERENCES chart_of_accounts(code),
    precedence      INTEGER NOT NULL DEFAULT 100,
    source          TEXT NOT NULL,        -- 'config' | 'ai' | 'user'
    created_at      TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
) STRICT;

CREATE TABLE vat_rates (
    id           INTEGER PRIMARY KEY,
    gl_account   TEXT REFERENCES chart_of_accounts(code),
    rate_bp      INTEGER NOT NULL,        -- basis points; 2000 = 20%
    valid_from   TEXT NOT NULL,
    valid_to     TEXT
) STRICT;

CREATE TABLE confidence_thresholds (
    id           INTEGER PRIMARY KEY,
    scope        TEXT NOT NULL,           -- 'global' | 'pipeline:<name>'
    floor        REAL NOT NULL DEFAULT 0.50,
    created_at   TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
) STRICT;

-- Budgets (Domain F — minimum-viable in MVP)
CREATE TABLE budget_envelopes (
    id              INTEGER PRIMARY KEY,
    scope_kind      TEXT NOT NULL,        -- 'employee' | 'team' | 'company'
    scope_id        INTEGER,              -- employee_id or team_id (NULL for company)
    category        TEXT NOT NULL,        -- 'food' | 'travel' | 'saas' | 'ai_tokens' | …
    period          TEXT NOT NULL,        -- 'YYYY-MM'
    cap_cents       INTEGER NOT NULL,
    soft_threshold_pct INTEGER DEFAULT 80,
    created_at      TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
) STRICT;

CREATE TABLE budget_allocations (
    id           INTEGER PRIMARY KEY,
    envelope_id  INTEGER NOT NULL REFERENCES budget_envelopes(id),
    line_id      INTEGER NOT NULL REFERENCES journal_lines(id),
    amount_cents INTEGER NOT NULL,
    created_at   TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
) STRICT;

CREATE TABLE _migrations (
    id INTEGER PRIMARY KEY, name TEXT UNIQUE NOT NULL,
    applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
) STRICT;
```

#### `orchestration.db`

```sql
CREATE TABLE pipeline_runs (
    id                INTEGER PRIMARY KEY,
    pipeline_name     TEXT NOT NULL,
    pipeline_version  INTEGER NOT NULL,
    trigger_source    TEXT NOT NULL,
    trigger_payload   TEXT NOT NULL,
    employee_id_logical TEXT,             -- logical FK → audit.employees.id
    status            TEXT NOT NULL,
    error             TEXT,
    started_at        TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    completed_at      TEXT,
    metadata          TEXT,
    CHECK (json_valid(trigger_payload)),
    CHECK (metadata IS NULL OR json_valid(metadata))
) STRICT;

CREATE TABLE pipeline_events (
    id              INTEGER PRIMARY KEY,
    run_id          INTEGER NOT NULL REFERENCES pipeline_runs(id),
    event_type      TEXT NOT NULL,
    node_id         TEXT,
    data            TEXT NOT NULL,
    payload_version INTEGER NOT NULL DEFAULT 1,    -- REF-SQLITE-BACKBONE:578
    elapsed_ms      INTEGER,
    created_at      TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CHECK (json_valid(data))
) STRICT;
CREATE INDEX idx_events_run ON pipeline_events(run_id, created_at);

CREATE TABLE external_events (
    id              INTEGER PRIMARY KEY,
    provider        TEXT NOT NULL,        -- 'swan' | 'shopify' | 'document' | …
    event_id        TEXT NOT NULL,
    event_type      TEXT NOT NULL,
    resource_id     TEXT,
    payload         TEXT NOT NULL,
    payload_version INTEGER NOT NULL DEFAULT 1,    -- REF-SQLITE-BACKBONE:578
    processed       INTEGER NOT NULL DEFAULT 0,
    created_at      TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (provider, event_id),
    CHECK (json_valid(payload))
) STRICT;

CREATE TABLE node_cache (
    cache_key       TEXT PRIMARY KEY,
    node_id         TEXT NOT NULL,
    pipeline_name   TEXT NOT NULL,
    code_version    TEXT NOT NULL,
    input_json      TEXT NOT NULL,
    output_json     TEXT NOT NULL,
    payload_version INTEGER NOT NULL DEFAULT 1,    -- REF-SQLITE-BACKBONE:578
    created_at      TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_hit_at     TEXT,
    hit_count       INTEGER NOT NULL DEFAULT 0,
    CHECK (json_valid(input_json) AND json_valid(output_json))
) STRICT;

CREATE TABLE _migrations (
    id INTEGER PRIMARY KEY, name TEXT UNIQUE NOT NULL,
    applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
) STRICT;
```

#### `audit.db`

```sql
CREATE TABLE employees (
    id                  INTEGER PRIMARY KEY,
    email               TEXT NOT NULL UNIQUE,
    full_name           TEXT,
    swan_iban           TEXT UNIQUE,
    swan_account_id     TEXT UNIQUE,
    manager_employee_id INTEGER REFERENCES employees(id),
    department          TEXT,             -- free-form for MVP
    active              INTEGER NOT NULL DEFAULT 1,
    created_at          TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
) STRICT;

CREATE TABLE agent_decisions (
    id                  INTEGER PRIMARY KEY,
    run_id_logical      INTEGER NOT NULL,        -- logical FK → orchestration.pipeline_runs.id
    node_id             TEXT NOT NULL,
    source              TEXT NOT NULL,           -- 'agent' | 'rule' | 'cache' | 'human'
    runner              TEXT NOT NULL,           -- 'anthropic' | 'adk' | 'pydantic_ai'
    model               TEXT,
    response_id         TEXT,
    prompt_hash         TEXT,
    alternatives_json   TEXT,
    confidence          REAL,
    line_id_logical     TEXT,                    -- logical FK → accounting.journal_lines.id
    -- LLM-call observability (ANTHROPIC_SDK_STACK_REFERENCE:1087-1107)
    latency_ms          INTEGER,
    finish_reason       TEXT,                    -- 'end_turn' | 'tool_use' | 'max_tokens' | …
    temperature         REAL,
    seed                INTEGER,
    started_at          TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    completed_at        TEXT,
    CHECK (alternatives_json IS NULL OR json_valid(alternatives_json)),
    CHECK (confidence IS NULL OR (confidence >= 0 AND confidence <= 1))
) STRICT;
CREATE INDEX idx_decisions_run  ON agent_decisions(run_id_logical);
CREATE INDEX idx_decisions_line ON agent_decisions(line_id_logical);

CREATE TABLE agent_costs (
    decision_id        INTEGER PRIMARY KEY REFERENCES agent_decisions(id),
    employee_id        INTEGER REFERENCES employees(id),
    provider           TEXT NOT NULL,
    model              TEXT NOT NULL,
    input_tokens       INTEGER NOT NULL DEFAULT 0,
    output_tokens      INTEGER NOT NULL DEFAULT 0,
    cache_read_tokens  INTEGER NOT NULL DEFAULT 0,
    cache_write_tokens INTEGER NOT NULL DEFAULT 0,
    reasoning_tokens   INTEGER NOT NULL DEFAULT 0,
    cost_micro_usd     INTEGER NOT NULL,
    created_at         TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
) STRICT;
CREATE INDEX idx_costs_employee_month ON agent_costs(employee_id, created_at);
CREATE INDEX idx_costs_provider_month ON agent_costs(provider, created_at);

CREATE TABLE _migrations (
    id INTEGER PRIMARY KEY, name TEXT UNIQUE NOT NULL,
    applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
) STRICT;
```

### 7.6 Hard invariants (asserted at write time)

1. `SUM(debit_cents) = SUM(credit_cents)` for every `entry_id`.
2. After every cash-basis post, `swan_transactions.booked_balance_after`
   for the same Swan account equals the GL bank-account balance computed
   from `journal_lines`.
3. Every `journal_lines.id` has at least one `decision_traces` row.
4. Every accrual entry posted from a PDF invoice has a non-null
   `documents.sha256` reachable through `journal_lines.document_id`.
5. `paired` cash + accrual entries link via `accrual_link_id` and the
   AP account's net balance returns to zero across the pair.

### 7.7 Cost helper

Integer micro-USD per million tokens, per `(provider, model)`. Pinned in
`orchestration/cost.py` with a `# verified <date>` comment. Update monthly.

```python
# Rates are integer micro-USD per million tokens.
# Verified 2026-04-25 against ANTHROPIC_SDK_STACK_REFERENCE:556 and
# CEREBRAS_STACK_REFERENCE:357-368. Refresh monthly.
COST_TABLE_MICRO_USD: dict[tuple[str, str], dict[str, int]] = {
    ("anthropic", "claude-opus-4-7"):
        {"input": 15000, "output": 75000, "cache_read": 1500, "cache_write": 18750},
    ("anthropic", "claude-sonnet-4-6"):
        {"input":  3000, "output": 15000, "cache_read":  300, "cache_write":  3750},
    ("anthropic", "claude-haiku-4-5"):
        {"input":   800, "output":  4000, "cache_read":   80, "cache_write":  1000},
    ("cerebras",  "llama3.3-70b"):
        {"input":   600, "output":   600, "cache_read":  600, "cache_write":   600},
    ("cerebras",  "gpt-oss-120b"):
        {"input":   350, "output":   750, "cache_read":  350, "cache_write":   350},
    ("cerebras",  "qwen-3-235b"):
        {"input":   600, "output":  1200, "cache_read":  600, "cache_write":   600},
}

def micro_usd(usage, provider, model):
    r = COST_TABLE_MICRO_USD[(provider, model)]
    return (usage.input_tokens       * r["input"]
          + usage.output_tokens      * r["output"]
          + usage.cache_read_tokens  * r["cache_read"]
          + usage.cache_write_tokens * r["cache_write"]) // 1_000_000
```

### 7.8 Prompt-hash canonicalization

`agent_decisions.prompt_hash` is the cache key for cross-run prompt
identity. Source: `ANTHROPIC_SDK_STACK_REFERENCE.md:901-914`. Hash only
the **last user message** (system + tools + model are the policy frame;
earlier turns are conversation context that should not invalidate
identity). `model` is included so a model swap forces re-evaluation.

```python
import hashlib, json

def prompt_hash(model: str, system: str, tools: list, messages: list) -> str:
    last_user = next(
        (m for m in reversed(messages) if m["role"] == "user"),
        None,
    )
    canonical = json.dumps(
        {"model": model, "system": system, "tools": tools, "user": last_user},
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode()).hexdigest()[:16]
```

### 7.9 Retry / timeout / idempotency policy

Cite `ANTHROPIC_SDK_STACK_REFERENCE.md:571-619`. The 5s end-to-end SLA
in §11 is only reachable with these defaults pinned.

```python
# Default Anthropic client config — pinned at construction time.
client = AsyncAnthropic(timeout=4.5, max_retries=2)

# Per-request idempotency for external-event-triggered runs.
extra_headers = {"Idempotency-Key": f"swan-{event_id}"}

# On APITimeoutError: do NOT retry the LLM call. Fall back to the
# deterministic path (rule lookup → review queue) and write an
# agent_decisions row with source='cache' or source='rule' and
# finish_reason='timeout'. The 5s SLA is preserved; the audit trail
# records that the LLM didn't return.
```

Document-extraction calls override `timeout=15.0` (vision is slower);
they do not feed the synchronous webhook path so the 5s SLA does not
apply.

### 7.10 `AgentResult` — cross-runtime normalization

Every runner returns the same dataclass. Fields below are populated
from each runtime as shown in §7.10.1.

```python
@dataclass
class AgentResult:
    output:         Any                # parsed tool/JSON output
    model:          str
    response_id:    str | None         # provider-side id, for replay
    prompt_hash:    str                # see §7.8
    alternatives:   list[dict] | None  # [{value, score}, …]
    confidence:     float | None
    usage:          TokenUsage         # input/output/cache_read/cache_write/reasoning
    latency_ms:     int
    finish_reason:  str | None
    temperature:    float | None
    seed:           int | None
```

#### 7.10.1 Field provenance per runtime

| Field          | Anthropic                                | Pydantic AI                                  | ADK                          |
|---             |---                                       |---                                           |---                           |
| `response_id`  | `msg.id`                                 | `result.all_messages()[-1].id`               | runner-supplied               |
| `usage`        | `msg.usage` (cache fields native)        | `result.usage()` (input/output/requests)     | runner-supplied               |
| `confidence`   | via `submit_*` tool convention           | via `submit_*` tool convention               | via `submit_*` tool convention|
| `alternatives` | via `submit_*` tool convention           | via `submit_*` tool convention               | via `submit_*` tool convention|
| `finish_reason`| `msg.stop_reason`                        | `result.all_messages()[-1].finish_reason`    | runner-supplied               |
| `temperature`  | echo of request param                    | echo of request param                        | echo of request param         |
| `seed`         | echo of request param (if set)           | echo of request param (if set)               | echo of request param (if set)|

`TokenUsage` is the unified shape; runtimes that don't surface a field
(Pydantic AI doesn't split cache_read / cache_write) zero-fill it. Test
`test_agent_result_shape.py` asserts every runner produces a fully
populated dataclass for the same fixture prompt.

### 7.11 The wedge query

```sql
-- "How much did we pay Anthropic this month, broken down by employee?"
ATTACH DATABASE 'audit.db' AS audit;

SELECT e.email, e.full_name, e.swan_iban,
       COUNT(*)                       AS call_count,
       SUM(c.cost_micro_usd)/1e6      AS usd_this_month
FROM   audit.agent_costs c
JOIN   audit.employees   e ON e.id = c.employee_id
WHERE  c.provider = 'anthropic'
   AND strftime('%Y-%m', c.created_at) = strftime('%Y-%m', 'now')
GROUP BY e.id
ORDER BY usd_this_month DESC;
```

Joining to `accounting.journal_lines` via `line_id_logical` shows that
each AI call is also a journal line (where applicable), unifying the
agent-cost story with the books.

---

## 8. Technology Stack

| Layer | Choice | Rationale |
|---|---|---|
| Language | Python 3.12 async | Async-first executor, broad SDK support |
| HTTP framework | FastAPI | Native async + SSE + dependency injection |
| ASGI server | uvicorn | Standard |
| DB | SQLite (×3) WAL | Embedded, transactional, demo-bulletproof |
| DB driver | `aiosqlite` ≥ 0.19 | Async parity with executor |
| Pipeline DSL | YAML + named conditions | Data, not code |
| Default agent runtime | `anthropic` Python SDK | Tool use, prompt cache, thinking |
| Optional runtimes | `google-adk`, `pydantic-ai` | Pluggability proof |
| GraphQL client | `httpx` (no full `gql`) | Lighter, async, enough for hand-typed queries |
| Fuzzy match | `rapidfuzz` (token_set_ratio ≥ 85) | Counterparty cascade |
| YAML loader | `pyyaml` (safe_load) | Std |
| Hashing | `hashlib.sha256` | Std |
| Validation | `pydantic` v2 | Tool/agent output dataclasses |
| Tests | `pytest` + `pytest-asyncio` | Std |
| Migrations | hand-rolled, `_migrations` table per DB | Portable, reviewable |
| Frontend | Vite + React + TypeScript + Tailwind | Speed |
| Frontend state | Zustand + EventSource | SSE-native |

Default models:

- **Agent runs:** `claude-sonnet-4-6` for classification fallback and
  document extraction. Can downgrade to `claude-haiku-4-5` per node via
  YAML for cost.
- **Reserved (Phase 2+):** `claude-opus-4-7` for planner and report-writer
  agents. Not used in MVP.

Optional extras:

```toml
[project.optional-dependencies]
adk         = ["google-adk>=0.5"]
pydantic_ai = ["pydantic-ai>=0.1"]
```

---

## 9. Security & Configuration

### 9.1 Authentication

- **Swan OAuth2** (`client_credentials`). Token cached in-process,
  refreshed at expiry-60s or on 401.
- **Three separate Swan secrets** (do not conflate):
  - `SWAN_CLIENT_ID` / `SWAN_CLIENT_SECRET` — OAuth
  - `SWAN_WEBHOOK_SECRET` — async ingress shared secret
  - `SWAN_PAYMENT_CONTROL_SECRET` — synchronous hook (Phase 3 only;
    endpoint stub claims the URL)
- **Per-provider external webhook secrets** (`STRIPE_WEBHOOK_SECRET`,
  `SHOPIFY_WEBHOOK_SECRET`, …) loaded by name from env when a provider
  is registered.
- **Application auth** out of scope for hackathon (single-user demo, basic
  `Authorization: Bearer <static>` on internal API).

### 9.2 Webhook signature

- `hmac.compare_digest` for `x-swan-secret` against the persisted
  subscription secret.
- IP allowlist for Swan (`52.210.172.90`, `52.51.125.72`, `54.194.47.212`)
  enforced at the firewall level — defense in depth, not the primary
  control.
- External webhooks: per-provider verifier; Stripe uses HMAC-SHA256, Shopify
  uses base64 HMAC-SHA256, etc. — looked up by provider key.

### 9.3 Configuration management

- `.env.local` (loaded with `python-dotenv`); never committed.
- Pipelines, routing, and registries are version-controlled.
- `confidence_thresholds` is the runtime knob; UI-tunable in Phase 2.
- `AGNES_DATA_DIR` (default `./data/`) — DB location.
- `AGNES_RUNNERS_ENABLED` — comma-list of enabled runners
  (default `anthropic`).

### 9.4 Security in scope

- ✅ Webhook signature verification (constant-time)
- ✅ Idempotency on every external event
- ✅ Integer-cents money — no float drift
- ✅ Append-only audit tables
- ✅ Decision trace per AI write (EU AI Act readiness)
- ✅ Per-DB single-writer locks (no cross-write races)

### 9.5 Security out of scope (MVP)

- ❌ Tamper-evident audit (append-only is structural, not cryptographic;
  S3 Object Lock / QLDB mirroring is post-hackathon)
- ❌ Multi-tenant isolation
- ❌ Production SCA flows (mocked / sandbox)
- ❌ Secrets rotation playbook
- ❌ Encrypted-at-rest SQLite
- ❌ **Multi-process deployment.** MVP assumes a single-process,
  single-event-loop runtime (`uvicorn --workers 1`). Per-DB
  `asyncio.Lock` does not coordinate across processes; multi-worker
  deployment requires a file-lock or advisory-lock layer and is a
  post-hackathon hardening item.

### 9.6 Deployment

- Single container, single host for the hackathon
- HTTPS via Caddy or Nginx reverse proxy
- SSE requires sticky / stateful host (not edge functions)

---

## 10. API Specification

### Inbound (third parties → us)

**`POST /swan/webhook`**

Headers: `x-swan-secret: <subscription secret>`, `x-swan: present`.
Body (Swan envelope):

```json
{
  "eventType":  "Transaction.Booked",
  "eventId":    "<uuid>",
  "eventDate":  "2026-04-25T13:21:04.673Z",
  "projectId":  "<project-id>",
  "resourceId": "<transaction-id>"
}
```

Behavior: verify → `INSERT OR IGNORE` into `external_events` →
enqueue pipeline → `200` (≤ 50ms target, 10s ceiling).
Errors: `401` on bad signature; `200` on duplicate (idempotent); `200` on
unknown event_type (logged for review).

**`POST /external/webhook/{provider}`**

Provider-specific signature header verified by a registered per-provider
verifier. Same idempotent insert + route + 200 contract.

**`POST /documents/upload`**

`multipart/form-data` with one PDF. Returns:

```json
{
  "document_id":   42,
  "sha256":        "ab12…",
  "run_id":        4271,
  "stream_url":    "/runs/4271/stream"
}
```

Idempotent on `sha256`: a duplicate upload returns the existing
`document_id` and the most recent `run_id` for it.

### Internal

- **`POST /pipelines/run/{name}`** — manual trigger.
  Body: `{"trigger_payload": {…}, "employee_id": <int|null>}`.
  Returns: `{"run_id", "stream_url"}`.

- **`GET /runs/{run_id}`** — full reconstruction (joined `pipeline_runs` +
  `pipeline_events` + the `agent_decisions` rows for this run).

- **`GET /runs/{run_id}/stream`** — SSE: `node_started`, `node_completed`,
  `node_skipped`, `cache_hit`, `node_failed`, `pipeline_completed`,
  `pipeline_failed`.

- **`GET /dashboard/stream`** — SSE: top-level events
  (`ledger.entry_posted`, `envelope.decremented`, `review.enqueued`).

- **`GET /journal_entries/{id}/trace`** — drills from a journal line to
  its decision trace, source pipeline run, agent decisions, agent costs,
  and source webhook event or document.

- **`POST /review/{entry_id}/approve`** — bookkeeper approves a queued
  entry; writes `approver_id` + `approved_at`; posts the entry.

---

## 11. Success Criteria

### MVP success — "judge can do this on stage"

1. Watch a Swan sandbox `Transaction.Booked` fire.
2. See the journal entry appear in the live UI within **5 seconds**.
3. Click the entry → see the full decision trace (rule fired, confidence 1.0,
   no LLM call); cost: $0.
4. Drag-drop the Anthropic invoice onto the dashboard.
5. Within ~10 seconds, watch the audit log animate: extract → validate →
   resolve counterparty (cache hit on Anthropic) → classify GL account
   (rule R-fixed) → build accrual → post.
6. Click the new accrual line → see `decision_traces` → click into
   `agent_decisions` (Claude Sonnet 4.6, prompt hash, confidence) → click
   into `agent_costs` (input/output tokens, $0.0023).
7. Watch the per-employee envelope ring drop on the dashboard.
8. Run the wedge SQL on stage: "Anthropic billed us $X this month, split
   per employee." Result is one row per employee, sorted descending.
9. Replay the same Swan webhook → no duplicate journal entry; `cache_hit`
   appears in the event stream.
10. Drop the same PDF again → no duplicate accrual; same `document_id`
    returned.

### Functional requirements

- ✅ End-to-end Swan webhook → posted GL entry: p95 latency ≤ 5s
- ✅ Idempotent on duplicate Swan `eventId`
- ✅ Idempotent on duplicate document SHA256
- ✅ Out-of-order tolerance (`Booked` before `Pending` is harmless)
- ✅ Adding a new event type is YAML + one tool, zero ingress LOC
- ✅ Every journal line has exactly one `decision_traces` row
- ✅ Balance invariant holds across the demo run
- ✅ Confidence gate routes < threshold to review and ≥ threshold to post
- ✅ Cache warmer: second occurrence of a previously-AI-classified merchant
  skips the LLM (verified in event log)
- ✅ Three runners registerable; the demo path uses Anthropic; tests cover
  all three
- ✅ Cost recording: stubbed agent run with known `usage` produces an
  `agent_costs` row whose `cost_micro_usd` matches `cost.micro_usd(usage)`
- ✅ Employee linking: a run started with `employee_id=42` produces
  `pipeline_runs.employee_id_logical = '42'` and every `agent_costs` row
  for that run has `employee_id = 42`
- ✅ Migrations: bootstrap-from-SQL == replay-from-migrations on all 3 DBs
- ✅ Pipeline replayability via `(pipeline_runs, pipeline_events)` join

### Quality indicators

- All tests pass under 30 seconds on a developer laptop
- Zero floating-point arithmetic on money paths (CI grep audit on PRs)
- Every external event keyed on its provider event ID (no internal
  sequence numbers as idempotency keys)
- A 5-node clean run produces exactly 12 `pipeline_events` rows
  (1 pipeline_started + 5×2 + 1 pipeline_completed)

### UX goals

- Founder sees a live-updating ledger view; events animate in
- Click any number → drill to source. Click a decision → see the trace
- Review queue is the only UI surface for low-confidence entries; the
  rest auto-posts
- Drag-drop a PDF on the dashboard; results visible within ~10 seconds

---

## 12. Implementation Phases

> Hackathon is a single weekend. Phases A–F below are sequenced
> dependencies inside that weekend, not calendar phases. Phases
> beyond the hackathon are listed as "post-hackathon" with no calendar.

### Phase A — Schemas, store, migrations (~3 hours)

**Goal:** three DBs open with PRAGMAs, every schema both bootstrap-able
and migration-replayable.

- ✅ `schema/{accounting,orchestration,audit}.sql`
- ✅ `migrations/{accounting,orchestration,audit}/0001_init.py`
- ✅ `_migrations` runner per DB
- ✅ `store.bootstrap.open_dbs()` returns three connections with PRAGMAs
- ✅ Round-trip test: schema-from-bootstrap == schema-from-migration-replay
  on each DB

### Phase B — Metalayer (DSL + executor + registries + cache) (~5 hours)

**Goal:** a `noop_demo.yaml` runs, emits the correct event sequence,
caches deterministically.

- ✅ YAML loader with strict-key rejection and required-field validation
- ✅ Kahn topological sort with cycle detection
- ✅ Four registries (tools / agents / runners / conditions); empty
- ✅ `AgnesContext` propagated through every node
- ✅ Layer-by-layer `asyncio.gather` with fail-fast
- ✅ `pipeline_runs` + `pipeline_events` writes via single-writer locks
- ✅ Cross-run cache: read-before-dispatch, write-after-success, hit event
- ✅ Three runners (anthropic real; adk + pydantic_ai with stub clients
  in tests)
- ✅ `cache_key()` round-trip fixture test: identical input dicts
  (including float values, nested arrays in different insertion orders,
  Unicode strings) produce identical keys; differing inputs (down to one
  whitespace char in a nested string, or a float represented as `1.0`
  vs. `1`) produce differing keys. Use `json.dumps(..., sort_keys=True,
  separators=(",",":"))` plus an explicit float canonicalizer
  (`repr(float(x))`) to defeat platform float drift.
- ✅ Tests: noop pipeline, cycle rejection, missing registry key,
  cache hit, fail-fast cancellation

### Phase C — Audit + cost + employees (~2 hours)

**Goal:** every agent call writes one `agent_decisions` + one
`agent_costs` row; the wedge query returns sensible numbers.

- ✅ `agent_decisions` writes wired into runner dispatch
- ✅ `agent_costs` writes wired into runner dispatch
- ✅ `employees` table seeded with 3 demo rows (Tim, plus two stand-ins)
- ✅ Wedge query test against fixture DB

### Phase D — Swan path (~6 hours)

**Goal:** one fake `Transaction.Booked` webhook drives a journal entry
end-to-end with decision trace.

- ✅ `swan/oauth.py` (token cache, refresh-on-401)
- ✅ `swan/graphql.py` with mutation-error union helper
- ✅ `POST /swan/webhook` endpoint with signature verify and idempotent
  insert
- ✅ `routing.yaml` wired to `transaction_booked.yaml`
- ✅ Tools: `swan_query.fetch_transaction`, `counterparty_resolver.run`,
  `gl_account_classifier.run`, `journal_entry_builder.{build_cash,
  build_accrual,match_accrual}`, `gl_poster.post`, `invariant_checker.run`,
  `budget_envelope.decrement`, `confidence_gate.run`, `review_queue.enqueue`
- ✅ Agents: `counterparty_classifier.run`, `gl_account_classifier_agent.run`
- ✅ Conditions: `gating.{posted,passes_confidence,needs_review}`,
  `counterparty.{unresolved}`, `gl.{unclassified}`
- ✅ Seed dataset: 3 employees × 1 personal Swan account, 1 company
  account, ~12 months of synthetic transactions, Anthropic + Notion +
  one boulangerie + one OFI utility already in `counterparties`
- ✅ Test: replay the same webhook → one journal entry; `cache_hit` on the
  resolver on second run; `recorded balance == Swan re-queried balance`
  asserts after every post

### Phase E — Document path (~3 hours)

**Goal:** drop the Anthropic PDF, see an accrual entry post.

- ✅ `POST /documents/upload` with SHA256 idempotency
- ✅ `agents/document_extractor.py` (Claude vision, strict JSON schema)
- ✅ `tools/document_extractor.validate_totals` (line items sum to total
  in cents)
- ✅ `journal_entry_builder.build_accrual` (Dr Expense + VAT deductible;
  Cr Supplier AP)
- ✅ Pipeline `pipelines/document_ingested.yaml` (matches §7.3)
- ✅ Test: drop a fake PDF → accrual entry posts; drop again → no
  duplicate; trace points to the document and the Claude vision call

### Phase F — Frontend + dashboard SSE (~5 hours)

**Goal:** the demo lands visually.

- ✅ Vite + React + TypeScript skeleton
- ✅ Live ledger (SSE, animated row inserts)
- ✅ Per-employee envelope rings (food / travel / SaaS / AI / leasing)
- ✅ Drag-drop upload zone (calls `POST /documents/upload`)
- ✅ Trace drawer (drills line → trace → agent decision → cost → source)
- ✅ Review queue (one-click approve)
- ✅ One-page "infrastructure" tab showing the three DBs, recent runs,
  recent events — a credibility surface for judges who want to see the
  bones

### Post-hackathon (no calendar)

- ✅ Pipeline replay (`replay_pipeline(run_id)`)
- ✅ Real HITL polling (`decision_pending` table + `wait_for_decision`)
- ✅ Goal-driven campaigns engine + planner agent
- ✅ Agentic DD pack / board pack / monthly commentary generators
- ✅ Synchronous payment-control hook (1.5s budget)
- ✅ Per-API-key allocation (when API providers expose per-key telemetry)
- ✅ Tamper-evident audit (S3 Object Lock / QLDB mirror)
- ✅ FTS5 over `pipeline_events.data`
- ✅ Postgres migration

---

## 13. Future Considerations

- **LangGraph adoption.** If campaign and planner state machines outgrow
  the named-condition pattern, port the agent runtime to LangGraph.
  Decision traces and the registries stay; only the executor changes.
- **Postgres migration.** SQLite is correct for MVP. At ~10k transactions/
  day or first paying customer, migrate. Schema is portable; the WAL
  ergonomics are not.
- **Tamper-evident audit.** Mirror `agent_decisions` and `pipeline_events`
  to S3 Object Lock or QLDB. Hook point: `db.write_event`. EU AI Act will
  require this.
- **OCR for arbitrary invoices.** Document AI / Mistral OCR / paddleOCR.
  Out of scope for MVP; deferred until campaigns drive enough manual
  upload volume.
- **Multi-entity consolidation.** Pennylane offloads to Joiin; we should
  own this primitive at Phase 5+.
- **E-invoicing PA certification.** September 2026 mandate; only relevant
  if we sell to French SMEs as primary buyer (vs. selling to founders who
  use Pennylane for the books).
- **Campaign budgets driving Swan card limits.** Phase 3; production
  hardening is rate-limit and SCA-flow management on `updateCard`.
- **Vector store / RAG over contracts.** Phase 4 prerequisite for the
  DD-pack agent.
- **Per-API-key cost allocation** (Ramp's 2025 US-only feature). Awaiting
  per-key telemetry from Anthropic, OpenAI, etc.

---

## 14. Risks & Mitigations

| # | Risk | Mitigation |
|---|---|---|
| 1 | Swan credentials / SCA friction kills the demo on stage | Mock all SCA flows in the sandbox path; pre-record any flow that requires user consent; backup video of the full demo |
| 2 | Webhook signature / replay subtleties cause a duplicate or missed entry mid-demo | `INSERT OR IGNORE` on `external_events.(provider, event_id)` is the idempotency boundary; test with a duplicate-fire script and out-of-order injection before the stage; the same key shape applies to the document path via SHA256 |
| 3 | Agentic claims collapse under judge questioning ("what does the AI actually *do*?") | Decision-trace UI is the answer — click any AI-touched line, see prompt hash, model, alternatives, confidence, cost. Build the trace UI before any feature breadth. |
| 4 | Three runners triple the surface area | Two runners ship behind optional extras; only Anthropic is on the demo path. Tests assert all three populate the same `AgentResult` shape. Skip ADK/Pydantic-AI runs from the live demo — show them in a unit test in the "infrastructure" tab. |
| 5 | Cross-DB pseudo-FK (`line_id_logical`, `run_id_logical`, `employee_id_logical`) drift over time | Document the seam loudly in §7.5; add a CI test in Phase 2 that joins the DBs and asserts no orphans; consider one-DB attach pattern for production |
| 6 | Cash/accrual pairing introduces double-count bugs in reports | One column on `journal_entries`; reports filter explicitly; the `accrual_link_id` ensures the pair is queryable; combined-view query has a documented dedup rule |
| 7 | `pipeline_events.data` JSON grows unbounded for chatty agents | 64 KiB cap + truncation marker; blob spillover is post-hackathon |
| 8 | Decision trace becomes a JSON sidecar (the architecture doc warned) | `decision_traces` is a real table with FKs from day one; `gl_poster.post` MUST write the trace before commit; CI lint: PR fails if `journal_lines` insert without a sibling `decision_traces` insert in the same code path |
| 9 | LangGraph debate consumes hackathon hours | Explicitly NOT in MVP. Anthropic SDK + named-condition gates is the lock-in. Reconsider at Phase 3, not before. |
| 10 | SQLite write contention as we add concurrent ingestion paths | One `asyncio.Lock` per DB; tools use short transactions; pipelines within a layer avoid writing the same downstream `node_id`. Profile before Phase 3. |

---

## 15. Appendix

### 15.1 Documented assumptions (resolve when more info available)

These three open questions from the scoping conversation have been
resolved with the following defaults; flag any to revisit:

1. **Cash and accrual both shown in the demo.** The PDF-invoice path
   posts an accrual; a synthetic SEPA-out in the seed converts it to
   cash on stage. The dual demo is the more honest story and lets the
   architecture's `basis` column do real work. *Reverse if* the dual
   demo can't be timed to fit in 3–5 minutes — fall back to cash-only.

2. **PDF upload UX is drag-drop on the dashboard.** Not a `curl` or a
   watched folder. Drag-drop sells better and fits the founder persona.
   The endpoint accepts both forms (`multipart` for the UI; the same
   endpoint can be hit from the terminal as a fallback if the file
   picker fails on stage).

3. **Employee↔Swan-account cardinality is 1:1 in the seed.** Three
   employees × one personal Swan account each, plus one shared company
   account. The schema (`employees.swan_account_id UNIQUE`) supports
   1:1; relax to many-cards-one-account in Phase 2 by introducing a
   `cards` table that FKs back to `employees`.

### 15.2 Demo seed dataset (rough)

- **Employees:** Tim (CEO), Marie (Engineering), Paul (Operations).
  Each with a personal Swan IBAN + one Mastercard. Plus one company
  account with two cards (Tim, Marie).
- **Counterparties — suppliers:** Anthropic, Notion, OFI (utility),
  Boulangerie Paul, SNCF, an Airbnb-style travel vendor, one SaaS
  (e.g., Linear), one leasing vendor (Fin/equivalent).
- **Counterparties — customers:** ~5 with virtual IBANs, recent
  invoices.
- **Transactions:** ~12 months of synthetic activity, ~200 rows;
  enough to make the dashboard charts non-empty and to seed a
  sensible `account_rules` cache.
- **Documents:** 3 PDF invoices to drop on stage — Anthropic
  (€50, billed to Tim), Notion ($45, billed to Marie), one
  €1,200 supplier invoice (billed to the company) that pairs with a
  scheduled SEPA-out in the seed.

### 15.3 Chart of accounts (PCG subset)

| Code | Name | Type | Used for |
|---|---|---|---|
| 411 | Customers (AR) | Asset | Customer-paid SEPA-in |
| 401 | Suppliers (AP) | Liability | Accrual entries from PDFs |
| 421 | Personnel — wages | Liability | Salary, reimbursements |
| 445 | VAT — to remit | Liability | VAT collected |
| 4456 | VAT — deductible | Asset | VAT on supplier invoices |
| 512 | Bank | Asset | All Swan account balances |
| 606100 | Office supplies | Expense | Card spend, supplies |
| 613 | Leasing | Expense | Fin / car leasing |
| 624 | Travel | Expense | SNCF, Airbnb, taxi |
| 6257 | Receptions / food | Expense | Boulangerie, restaurants |
| 626100 | API / cloud services | Expense | Anthropic, OpenAI, AWS |
| 626200 | SaaS subscriptions | Expense | Notion, Linear, Slack |
| 706000 | Service revenue | Revenue | Customer invoices |

Extendable; this is the demo subset.

### 15.4 Related documents

Read in this order if onboarding fresh:

1. `pennylane_vs_us.md` — strategic positioning, demo wow-moments
2. `projectbriefing.md` — product vision, personas, principles
3. `architecure.md` — domains, invariants, booking patterns
4. `pitch_research.md` — market, competitors, the wedge
5. **This document (`RealMetaPRD.md`)** — what we're building this weekend
6. `Dev orchestration/_exports_for_b2b_accounting/01_ORCHESTRATION_REFERENCE.md`
   — Kahn scheduler, executor loop
7. `Dev orchestration/_exports_for_b2b_accounting/02_YAML_WORKFLOW_DSL.md`
   — pipeline DSL, named conditions
8. `Dev orchestration/_exports_for_b2b_accounting/03_SQLITE_BACKBONE.md`
   — schemas, migrations gap
9. `Dev orchestration/_exports_for_b2b_accounting/04_AGENT_PATTERNS.md`
   — ToolResult shape, multiplicative confidence, refusal log
10. `Dev orchestration/swan/SWAN_API_REFERENCE.md` — events, GraphQL,
    OAuth, payment-control synchronous hook
11. `Orchestration/research/ANTHROPIC_SDK_STACK_REFERENCE.md` — runner,
    cost helper, prompt-hash shape
12. `Orchestration/research/CEREBRAS_STACK_REFERENCE.md` — Pydantic AI
    runner, ADK decision matrix
13. `Dev orchestration/tech framework/REF-SQLITE-BACKBONE.md` — WAL,
    migrations, single-writer discipline

### 15.5 Open questions to close before Phase 2

1. **Confidence threshold UX.** Where does the founder set them?
   MVP ships hardcoded; Phase 2 surfaces a settings UI.
2. **Virtual IBAN strategy at scale.** Per-customer issuance is elegant
   but quota-bounded. Confirm Swan production limits before relying on
   it for inbound matching at >100 customers.
3. **Webhook secret rotation.** Swan's grace-period behavior unconfirmed.
   Get answer before any production rollout.
4. **Department / cost-center dimension.** MVP has a free-form
   `employees.department TEXT`. Phase 2 likely needs a `departments`
   table + `employee_departments` link with effective dates.
5. **Per-API-key allocation.** Defer until at least one provider exposes
   per-key telemetry that maps cleanly to employees.
6. **Audit-DB tamper-evidence.** S3 Object Lock vs. QLDB vs. SIEM mirror —
   pick before EU AI Act enforcement.

### 15.6 What this PRD deliberately does *not* answer

- The exact wording of the demo script and stage choreography.
- The visual design of the dashboard beyond "rings + ledger + drawer +
  upload zone."
- The infrastructure (deployment / DNS / TLS) for the demo.
- Anything Phase 3+ in concrete schemas or interfaces — the campaign
  engine, DD-pack agent, payment-control hook, and replay function are
  all named but not specified here.

---

*RealMetaPRD v1 — written 2026-04-25. Revise after Phase A lands and
the executor question is answered in code, not in docs.*
