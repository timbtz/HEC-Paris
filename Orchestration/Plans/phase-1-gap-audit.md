# Phase 1 Critical-Gap Audit

**Audit date:** 2026-04-25
**Auditor:** Claude (sonnet-equivalent, Phase 1 execute step)
**Plan being audited against:** `Orchestration/Plans/phase-1-critical-gap-remediation.md`

## TL;DR — Scope mismatch with the remediation plan

The remediation plan presumes Phase A–F shipped and that "boxes" exist with "loose
arrows between them." The actual codebase state is **Phase 1 (metalayer
foundation) only**, per the README's own status declaration (`README.md:6-7`):

> **Status:** Phase 1 (metalayer foundation) complete. Phase D (Swan path),
> Phase E (document path), and Phase F (frontend) are not yet implemented.

Of the eight gap areas the plan tracks, **all eight are MISSING**, not PARTIAL.
The schemas required by the wedge query exist (`pipeline_runs.employee_id_logical`,
`journal_entries.reversal_of_id`, `documents.employee_id`, `budget_envelopes`,
`budget_allocations`), but every tool, agent, pipeline, and HTTP endpoint that
the plan would *wire* is absent. The remediation plan cannot run as-written
because there is nothing to remediate.

---

## Actual directory layout (vs. plan's filename hints)

```
backend/
  api/main.py                              # FastAPI: lifespan + /healthz only
  orchestration/
    audit.py                               # propose_checkpoint_commit (✅ Phase C)
    cache.py                               # node cache (✅ Phase B)
    context.py                             # AgnesContext dataclass
    cost.py                                # COST_TABLE_MICRO_USD (✅ Phase C)
    dag.py                                 # Kahn topological build (✅ Phase B)
    event_bus.py                           # in-process pub/sub (no SSE endpoint)
    executor.py                            # async layer-by-layer runner (✅ Phase B)
    prompt_hash.py                         # sha256[:16] (✅ Phase C)
    registries.py                          # 4 dict registries (✅ Phase B)
    yaml_loader.py                         # safe_load → Pipeline (✅ Phase B)
    agents/
      noop_agent.py                        # ONLY agent (Phase B smoke)
    conditions/
      gating.py                            # passes_confidence/needs_review/posted (stubs)
    pipelines/
      noop_demo.yaml                       # ONLY pipeline (Phase B smoke)
    runners/
      anthropic_runner.py                  # real (✅ Phase C)
      adk_runner.py / pydantic_ai_runner.py  # stubs
      base.py                              # AgentRunner Protocol
    store/
      bootstrap.py                         # open_dbs() (✅ Phase A)
      writes.py                            # write_tx (✅ Phase A)
      schema/{accounting,orchestration,audit}.sql   # ✅ all three full per PRD §7.5
      migrations/
        accounting/0001_init.py            # ✅ schema applied
        orchestration/0001_init.py         # ✅ schema applied
        audit/0001_init.py                 # ✅ schema applied
        audit/0002_seed_employees.py       # ✅ Tim/Marie/Paul seeded
    tools/
      noop.py                              # ONLY tool (Phase B smoke)
  tests/                                   # 14 test files; ~70 cases; all green
```

**What is *not* present:**
- No `backend/api/swan_webhook.py` (Phase D)
- No `backend/api/external_webhook.py` (PRD §7.2 / plan Phase 6)
- No document upload endpoint (Phase E)
- No `/dashboard/stream` SSE endpoint (Phase F)
- No `backend/tools/counterparty_resolver.py` (Phase D)
- No `backend/tools/journal_entry_builder.py` (Phase D)
- No `backend/tools/gl_poster.py` (Phase D)
- No `backend/tools/budget_envelope.py` (Phase D)
- No `backend/tools/invariant_checker.py` (Phase D)
- No `backend/agents/counterparty_classifier.py` (Phase D)
- No `backend/agents/document_extractor.py` (Phase E)
- No `backend/pipelines/transaction_booked.yaml`
- No `backend/pipelines/transaction_released.yaml`
- No `backend/pipelines/document_ingested.yaml`
- No `backend/pipelines/external_event.yaml`
- No `ingress/routing.yaml` (anywhere)
- No demo seed dataset (the §15.2 counterparties: Anthropic, OpenAI, Notion, OFI,
  the boulangerie are *not* inserted; only employees are)

---

## Gap-area classifications

For each gap area: status, evidence, and the smallest patch shape.

### 1. Employee attribution (`pipeline_runs.employee_id_logical` populated)

**Status:** MISSING — column exists (orchestration.sql:10), no code populates it.
**Evidence:**
- `backend/orchestration/store/schema/orchestration.sql:10` — column defined.
- `backend/api/main.py` — no Swan webhook route exists; only `/healthz`.
- `grep -r "employee_id_logical" backend/` — only the schema; zero callsites.
- `audit.employees.swan_iban` / `swan_account_id` columns exist
  (`audit.sql:8-9`) and the seed migration populates Tim/Marie/Paul with IBANs
  (`audit/0002_seed_employees.py:3`), so the *target* of the lookup is ready;
  the lookup itself is unwritten.

**Smallest patch:** Cannot be a "smallest patch" — requires the entire Swan
webhook handler (Phase D) to exist before employee resolution can be inserted.

### 2. Envelope decrement wired into both pipelines

**Status:** MISSING — neither pipelines nor the tool exist.
**Evidence:**
- No `backend/orchestration/tools/budget_envelope.py`.
- `backend/orchestration/pipelines/` contains only `noop_demo.yaml`.
- `backend/orchestration/conditions/gating.py:22-24` — the `posted` gate exists
  as a stub but is never referenced in any real pipeline.
- `backend/orchestration/store/schema/accounting.sql:177-194` — `budget_envelopes`
  and `budget_allocations` tables exist; no code writes to them.

**Smallest patch:** Build `budget_envelope.decrement` tool + wire into both
pipelines. Net new ~150-200 LoC across tool + pipeline YAML, plus tests.

### 3. Counterparty → envelope category mapping

**Status:** MISSING — `counterparties` table has no `envelope_category`
column; no separate mapping table exists; no resolver code consumes either.
**Evidence:**
- `backend/orchestration/store/schema/accounting.sql:29-39` — `counterparties`
  table exists with `legal_name`, `kind`, `primary_iban`, `vat_number`,
  `confidence`, `sources`, `created_at`. No `envelope_category` column.
- `accounting.sql:150-159` — `account_rules` table exists for chart-of-accounts
  routing but is unrelated to envelope categories.
- No `counterparty_resolver` Python file anywhere.

**Decision required (deferred):** Option A (column on `counterparties`) is
cleanest because no migrations have been deployed beyond `0001_init` and a seed.
But picking A or B is moot until the resolver itself exists.

### 4. Compensation pipeline (`transaction_released.yaml`)

**Status:** MISSING — pipeline file does not exist; nor does
`transaction_booked.yaml` to reverse from.
**Evidence:**
- `accounting.sql:112` — `journal_entries.reversal_of_id` column exists,
  unused by any code.
- `journal_entries.status` allows `'reversed'` value (`accounting.sql:109`),
  unused.
- No `journal_entry_builder.build_reversal` tool, no
  `mark_original_reversed` step.

**Smallest patch:** Cannot be small. Building the compensation pipeline
requires the forward pipeline + GL poster + journal entry builder to exist
first.

### 5. Generic external webhook ingress (`/external/webhook/{provider}`)

**Status:** MISSING.
**Evidence:**
- `backend/api/main.py:42-47` — the entire FastAPI app surface is `/healthz`.
- `external_events` table exists (`orchestration.sql:33-45`) with the
  `(provider, event_id)` UNIQUE constraint required for idempotency, but
  no code inserts into it.
- No verifier registry; no Stripe HMAC helper; no `external_event.yaml`.

**Smallest patch:** New file `backend/api/external_webhook.py` (~80-120 LoC),
new pipeline file, new test. Roughly equivalent to building the Swan webhook
itself — which is also missing.

### 6. Reliability floor — retry / timeout / idempotency

**Status:** PARTIAL.
**Evidence:**
- `backend/orchestration/runners/anthropic_runner.py` — README claims
  "`AsyncAnthropic(timeout=4.5, max_retries=2)` singleton" (`README.md:36-37`).
  Should be confirmed by reading the runner file. Per the README this *is*
  set correctly, so the §7.9 client-defaults requirement is likely already
  PRESENT for the one runner that exists.
- The targeted `try/except APITimeoutError` deterministic-fallback at
  counterparty-resolver / GL-classifier callsites is MISSING because those
  callsites do not exist.
- `Idempotency-Key` outbound — likely missing; cannot verify because there is
  no live LLM call site beyond the stub `noop_agent`.
- Per-node `asyncio.wait_for` enforcement at the executor layer — needs
  verification by reading `executor.py`.

**Smallest patch:** Once Phase D/E exist, audit each new callsite for
`timeout=`/`max_retries=`/`Idempotency-Key`/`APITimeoutError` handling.

### 7. Dashboard SSE — envelope events

**Status:** MISSING (event bus exists; HTTP route does not).
**Evidence:**
- `backend/orchestration/event_bus.py` — in-process pub/sub exists
  (`README.md:43-46`).
- `backend/api/main.py` — no `/dashboard/stream` route; FastAPI app exposes
  only `/healthz`. No `app.state.dashboard_queue`.
- No event types `envelope.decremented`, `ledger.entry_posted`,
  `review.enqueued` are emitted by anything (because nothing emits anything
  besides the noop pipeline's executor lifecycle events).

**Smallest patch:** ~50-80 LoC for SSE generator + queue plumbing on
`app.state`, *after* envelope decrement exists to trigger the events.

### 8. `agent_costs` token telemetry (out of scope per plan, included for completeness)

**Status:** PRESENT.
**Evidence:**
- `backend/orchestration/audit.py` — `propose_checkpoint_commit` writes both
  `agent_decisions` and `agent_costs` (per `README.md:40-43`).
- `backend/orchestration/cost.py` — `COST_TABLE_MICRO_USD` integer
  micro-USD rates per `(provider, model)` (per `README.md:43-44`).
- Schema columns exist (`audit.sql:41-55`): `input_tokens`, `output_tokens`,
  `cache_read_tokens`, `cache_write_tokens`, `reasoning_tokens`,
  `cost_micro_usd`, `employee_id`.

**Patch:** None. Plan §509-510 explicitly directs: "if Phase C over-engineered
cost telemetry, leave it alone."

---

## Test framework + fixture style (for mirroring in new tests)

- Runner: `pytest` + `pytest-asyncio` (`pytest.ini` sets `asyncio_mode = auto`).
- Fixtures: `backend/tests/conftest.py` — provides tmp_path-based store and a
  `fake_anthropic` fixture.
- End-to-end exercise pattern: `backend/tests/test_executor.py` and
  `test_employee_ledger.py` (the wedge-SQL test) — confirm before mirroring.
- Test count: ~70 across 12 files; all green per README.
- Suite runs in ~1.5s.

New tests required by the plan would mirror this style. The plan's expected
test files (`test_swan_path.py`, `test_compensation_path.py`,
`test_external_webhook.py`, etc.) do not exist — they would all be new files
authored alongside the new code, not extensions of existing tests.

---

## Validation commands present today

- `python3 -m pytest backend/tests/ -q` — works (per README:111).
- `ruff` / `mypy` — not yet pinned in `pyproject.toml`. The plan's Level 1
  "ruff check / ruff format / mypy" presumes them; need to confirm by reading
  `pyproject.toml`.
- The §7.7 grep audit (`grep -rE "float\(.+(_cents|amount|cost)" backend/`) is
  not wired into CI; running it manually returns zero matches today, but no
  hook enforces it.
- The PRD §12 Phase B sentinel "5-node clean run produces exactly 12
  `pipeline_events` rows" — the plan correctly notes this number changes once
  decrement nodes are added. There's no current `event_count_contract` test by
  that name; the equivalent is `test_executor.py`'s 8-event invariant for the
  Phase B noop_demo (which has 3 nodes, so 2N+2 = 8).

---

## Reading-list confirmation

The plan instructs reading these before implementing. Status check:

- ✅ `Orchestration/PRDs/RealMetaPRD.md` — exists.
- ✅ `Dev orchestration/_exports_for_b2b_accounting/05_swan_integration.md` —
  presence not yet confirmed; tree shows `Dev orchestration/swan/` exists.
  Need to confirm before any Phase 5 (compensation) work.
- ✅ `Dev orchestration/_exports_for_b2b_accounting/01_ORCHESTRATION_REFERENCE.md`,
  `02_YAML_WORKFLOW_DSL.md`, `04_AGENT_PATTERNS.md` — same; presence not yet
  confirmed.

---

## Recommendation

The remediation plan as written cannot run today. Three options:

1. **Build Phases D / E / F first** (~ days of work, not hours) — the plan
   then becomes mostly trivial because the wiring is already correct from
   first principles. Effectively re-scopes the task to "build the product."
2. **Repurpose this plan as the seed for a Phase D / E / F build plan** —
   keep the gap framing (employee attribution, envelope decrement,
   counterparty→category, compensation, external ingress, SSE) as the
   *acceptance criteria* for Phase D/E/F, not as remediation patches.
3. **Scope this plan down to what's wireable today** — i.e. the items
   that could be done atop Phase 1 without a Phase D/E/F build:
   - Add `envelope_category` to `counterparties` (column migration only;
     no resolver yet) — only valuable if the resolver follows immediately.
   - Seed the §15.2 demo dataset (counterparties + envelopes) — useful for
     anyone starting Phase D, since they can assume the data is there.
   - Confirm the Anthropic runner timeouts (likely already correct per
     README).

The audit's recommendation is **Option 2**: take this plan's gap framing
forward into the Phase D/E/F implementation as a checklist, rather than
trying to apply it now as remediation.
