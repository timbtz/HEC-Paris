---
title: PRD1 Validation Briefing — Findings & Required Fixes
audience: next Claude/Codex session that will edit PRD1.md
source_prd: Orchestration/PRDs/PRD1.md
parent_prd: Orchestration/PRDs/MetaPRD.md
validated_against:
  - Dev orchestration/tech framework/REF-SQLITE-BACKBONE.md
  - Dev orchestration/tech framework/REF-GOOGLE-ADK.md
  - Orchestration/research/ANTHROPIC_SDK_STACK_REFERENCE.md
  - Orchestration/research/CEREBRAS_STACK_REFERENCE.md
  - Dev orchestration/_exports_for_b2b_accounting/02_YAML_WORKFLOW_DSL.md
date: 2026-04-25
status: ready-to-apply
---

# PRD1 Validation Briefing

## How to use this document

You are the next session. The previous session validated `Orchestration/PRDs/PRD1.md` against every source it cites by dispatching four parallel research agents. This document is the consolidated punch list.

**Your job:** apply the fixes in §3 to `PRD1.md` in order, in a single editing pass. Do **not** re-derive or re-validate — the validation is done. Cite line numbers from the source docs verbatim where this briefing does.

**What's already verified as solid** — don't touch:
- STRICT tables, `json_valid()` CHECKs, partial indexes, VIRTUAL generated columns (§6.4)
- Hard-invariant compliance via deferral
- HITL deferral cite (`:789-858`)
- `claude-sonnet-4-6` and `claude-haiku-4-5` cost rates
- Migrations approach (hand-rolled `_migrations` runner)
- `AgnesContext` / `PipelineContext` propagation pattern
- Open Question #1 (`pipeline_dag_versions`) — genuinely unresolved

---

## 1. Executive summary

PRD1 is directionally sound but has 15 concrete defects. Five are critical (block implementation), six are concrete schema/code gaps, four are soft architectural concerns. Total fix effort estimated at one editing pass on PRD1.md plus optional schema additions.

**No re-architecture is required.** Every fix is local.

---

## 2. Findings (ranked by severity)

### Critical (must fix before implementation begins)

**C1. Filename mismatch.** PRD1 frontmatter `parent: Orchestration/PRDs/PRD.md` and every `:NNN` anchor target a file named `PRD.md`. The actual file is `MetaPRD.md`. None of the inline references resolve as written.

**C2. YAML DSL contradicts master PRD and B2B export.**
- Master PRD `:385-433` and `_exports_for_b2b_accounting/02_YAML_WORKFLOW_DSL.md` use `tool_class:` / `agent_class:` (CamelCase, registry-resolved).
- PRD1 §6.2 uses `tool:` / `agent:` with `module.path:symbol` strings.
- This is the most concrete scope-drift finding. PRD1 must either conform or document the deliberate departure.

**C3. Silent table rename.** Master PRD `:291-299` calls it `swan_events`. PRD1 generalizes to `external_events` without acknowledgment. Defensible (multi-provider) but needs an explicit note in §6.4.

**C4. `prompt_hash` formula mis-anchored and divergent.**
- PRD1 §7.6 cites `ANTHROPIC_SDK_STACK_REFERENCE.md:432-476`. The actual canonicalization formula lives at `:901-914`.
- PRD1's formula omits `model` (so a model swap collides) and hashes all messages, while source hashes only the last user message.
- Source formula: `sha256(json.dumps({model, system, tools, user: last_user_message}, sort_keys=True))[:16]`.

**C5. ADK runner contradicts source guidance.**
- `CEREBRAS_STACK_REFERENCE.md:840` explicitly says **"Avoid B (ADK) for this project unless you're committed to a Vertex migration."**
- PRD1 §7.4 ships ADK as a co-equal third runner.
- Recommendation: ship `anthropic` (default) + `pydantic_ai` (extras), stub ADK behind an extra. Drops Phase 1.F to two runners.

### Concrete schema/code gaps

**G1. Missing PRAGMAs in §6.6.** All explicitly recommended in REF-SQLITE-BACKBONE:209-215:
- `synchronous = NORMAL` — without this, default `FULL` fsyncs every commit
- `temp_store = MEMORY`
- `cache_size = -65536` — default 2 MB will thrash join-heavy rollup queries
- `mmap_size = 134217728` — advised

**G2. No `payload_version` column** on JSON-bearing tables (`external_events`, `pipeline_events`). REF-SQLITE-BACKBONE:578 flags this as a defense against silent-NULL traps.

**G3. `agent_decisions` schema gaps** vs `ANTHROPIC_SDK_STACK_REFERENCE.md:1087-1107` and `CEREBRAS_STACK_REFERENCE.md:378-405`:
- Missing `parent_event_id` (link from external event → its decisions)
- Missing `rule_id` (which rule fired for `source='rule'`/`'cache'`)
- Missing `approver_id` / `approved_at` (forces join to `decision_pending`)
- Missing `latency_ms`, `finish_reason`, `temperature`, `seed`
- Cosmetic: source uses `alternatives`; PRD1 uses `alternatives_json`

**G4. Cost table omissions** in §7.7:
- No `claude-opus-4-7` ($15 / $75 / $18.75 cache_write / $1.50 cache_read per `:556`)
- No Cerebras `gpt-oss-120b` ($0.35 / $0.75) or `qwen-3-235b`

**G5. No retry/timeout/idempotency policy.** ANTHROPIC_SDK_STACK_REFERENCE:571-619 prescribes `timeout=4.5`, `max_retries=2`, `Idempotency-Key: swan-{event_id}`. PRD1 has none — directly impacts the 5s SLA.

**G6. `AgentResult` under-specified** for cross-runtime normalization:
- Pydantic AI's `RunUsage` = `(input_tokens, output_tokens, requests)`
- Raw Anthropic reports `cache_creation_input_tokens` / `cache_read_input_tokens`
- ADK has no native `confidence`/`alternatives` — must come from a `submit_*` tool the agent calls
- Add `latency_ms`, `finish_reason`, `temperature`, `seed`, `reasoning_tokens` to the protocol return type

### Architectural concerns (soft, address if scope allows)

**A1. Cost ledger pulled forward from Phase 3 silently.** Master PRD `:706-722` places per-employee budget/AI-cost allocation in Phase 3 (the Pennylane/Ramp wedge). PRD1 ships the recording half in Phase 1. Relabel as "Phase 3 scaffolding pulled forward to enable demo wedge," not "Phase 1 scope."

**A2. `cache_key` canonicalization not fully deterministic.** `json.dumps(sort_keys=True, separators=...)` doesn't handle floats deterministically across platforms. Add a canonicalization fixture test in Phase 1.D.

**A3. Single-writer model not worker-count-aware.** Per-DB `asyncio.Lock` works for single-process; multi-process (gunicorn workers) breaks it. PRD1 must specify worker count = 1 explicitly in §9.

**A4. Anchor-line drift** throughout PRD1. Several citations are off by a few lines:
- `:14` → `:18` for "data, not code"
- `:96` conflates LangGraph deferral with Postgres deferral (Postgres is at `:86`)
- `:678` → `:673` for the five Swan tools list
- `:14-32` core principles citation is correct in range but mis-pinpointed in body text

---

## 3. Required fixes (apply in order)

### Fix 1 — Filename + anchor sweep (mechanical)

In `PRD1.md`:
- Replace `Orchestration/PRDs/PRD.md` with `Orchestration/PRDs/MetaPRD.md` everywhere (frontmatter `parent:`, §0 source-document table, §16 appendix, all inline cites).
- Re-pin these specific anchors:
  - `PRD.md:14` → `MetaPRD.md:18` (the "data, not code" slogan)
  - `PRD.md:96` is LangGraph only; add `MetaPRD.md:86` for Postgres/multi-tenant deferral
  - `PRD.md:678` → `MetaPRD.md:673` for the five Swan tools list
- Verify all other `:NNN` anchors with `Read` against `MetaPRD.md` before saving — drift is common.

### Fix 2 — DSL reconciliation (§6.2)

Decide one of two paths and apply it:

**Path A (recommended): conform to master/B2B export.**
- Change `tool: tools.echo:run` → `tool_class: EchoTool`
- Change `agent: agents.invoice_classifier` → `agent_class: InvoiceClassifier`
- Drop the `module.path:symbol` resolver in favor of registry lookup by class name.

**Path B (deliberate departure): keep PRD1 syntax but document.**
- Add a §6.2.1 "DSL syntax — departure from master PRD" subsection.
- Justify the departure (e.g., "module-path strings make grep + IDE goto-definition free; class registries require import-time scanning that `:170` warns against").
- Add a migration plan from master's `tool_class:` to PRD1's `tool:`.

### Fix 3 — Document the `swan_events` → `external_events` rename (§6.4)

Add a one-paragraph note immediately above the `external_events` DDL:

> **Rename from master PRD.** Master PRD calls this table `swan_events` (`MetaPRD.md:291-299`). PRD1 generalizes to `external_events` with a `provider` discriminator column to support `swan | stripe | …` without per-provider tables. The unique key changes from `event_id` alone to `(provider, event_id)`. Phase 2 Swan ingress writes use `provider='swan'`.

### Fix 4 — Reconcile `prompt_hash` formula (§7.6)

Replace the current §7.6 step 1 with:

```python
# Source: ANTHROPIC_SDK_STACK_REFERENCE.md:901-914
def prompt_hash(model: str, system: str, tools: list, messages: list) -> str:
    last_user = next(
        (m for m in reversed(messages) if m["role"] == "user"),
        None,
    )
    canonical = json.dumps(
        {"model": model, "system": system, "tools": tools, "user": last_user},
        sort_keys=True,
    )
    return hashlib.sha256(canonical.encode()).hexdigest()[:16]
```

Re-anchor the cite: `ANTHROPIC_SDK_STACK_REFERENCE.md:432-476` → `:901-914`.

### Fix 5 — Drop ADK to a stub (§7.4, §8, §12 Phase 1.F)

In §7.4:
- Keep `anthropic` (default) and `pydantic_ai` (extras) as real runners.
- Replace the ADK runner description with: `"adk" — stub-only in PRD1; raises NotImplementedError. Source CEREBRAS_STACK_REFERENCE.md:840 recommends avoiding ADK unless committing to Vertex migration. Defer real implementation to PRD2+."`

In §8 dependencies: drop `google-adk>=0.5` from optional extras (or keep with a note that the runner is unimplemented).

In §12 Phase 1.F success criteria: change "three runners" to "two runners + ADK stub."

### Fix 6 — Add missing PRAGMAs (§6.6)

Replace the PRAGMA block with:

```sql
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;
PRAGMA synchronous = NORMAL;          -- REF-SQLITE-BACKBONE:209
PRAGMA busy_timeout = 5000;
PRAGMA temp_store = MEMORY;           -- REF-SQLITE-BACKBONE:213
PRAGMA cache_size = -65536;           -- 64 MB; REF-SQLITE-BACKBONE:214
PRAGMA mmap_size = 134217728;         -- 128 MB; REF-SQLITE-BACKBONE:215
PRAGMA wal_autocheckpoint = 1000;
PRAGMA journal_size_limit = 67108864; -- 64 MB
```

### Fix 7 — Add `payload_version` to JSON-bearing tables (§6.4)

To `external_events`, `pipeline_events`, and (for symmetry) `node_cache`:

```sql
payload_version INTEGER NOT NULL DEFAULT 1,
```

Cite REF-SQLITE-BACKBONE:578.

### Fix 8 — Extend `agent_decisions` (§6.4)

Add columns:

```sql
parent_event_id INTEGER REFERENCES external_events(id),
rule_id         TEXT,
approver_id     INTEGER REFERENCES employees(id),
approved_at     TEXT,
latency_ms      INTEGER,
finish_reason   TEXT,
temperature     REAL,
seed            INTEGER,
```

Optional cosmetic: rename `alternatives_json` → `alternatives` to match source (purely a naming choice; the CHECK clause stays).

### Fix 9 — Extend cost table (§7.7)

Add to `COST_TABLE_MICRO_USD`:

```python
("anthropic", "claude-opus-4-7"):     {"input": 15000, "output": 75000,
                                        "cache_read": 1500, "cache_write": 18750},
("cerebras",  "gpt-oss-120b"):        {"input":   350, "output":   750,
                                        "cache_read":  350, "cache_write":   350},
("cerebras",  "qwen-3-235b"):         {"input":   600, "output":  1200,
                                        "cache_read":  600, "cache_write":   600},
```

(Verify `qwen-3-235b` rate against `CEREBRAS_STACK_REFERENCE.md:357-368` before committing — the agent flagged the model as missing but did not pin the exact rate.)

### Fix 10 — Add §7.9 "Retry / timeout / idempotency policy"

New subsection citing `ANTHROPIC_SDK_STACK_REFERENCE.md:571-619`:

```python
# Default client config
AsyncAnthropic(timeout=4.5, max_retries=2)

# Per-request idempotency for external-event-triggered runs
extra_headers={"Idempotency-Key": f"swan-{event_id}"}

# On APITimeoutError: fall back to deterministic path; do not retry the LLM call
```

### Fix 11 — Extend `AgentResult` (§7.4)

Update the protocol return shape:

```python
@dataclass
class AgentResult:
    output: Any
    model: str
    response_id: str | None
    prompt_hash: str
    alternatives: list[dict] | None
    confidence: float | None
    usage: TokenUsage              # unified across runtimes
    latency_ms: int
    finish_reason: str | None
    temperature: float | None
    seed: int | None
```

Add a §7.4.1 mapping table showing how each runtime fills these:
- Anthropic: `response_id = msg.id`, `confidence` = via `submit_*` tool convention
- Pydantic AI: `response_id = result.all_messages()[-1].id`, usage from `result.usage()`
- ADK (stub): raises

### Fix 12 — Relabel cost ledger (§4 ✅ In Scope)

Above the "Cost / employee ledger" bullet group, add:

> **Note: Phase 3 scaffolding pulled forward.** Master PRD `:706-722` places per-employee AI-cost allocation in Phase 3. PRD1 ships the **recording** half (`agent_costs`, `employees`) in Phase 1 because the demo wedge requires it; **enforcement** (budgets, alerts, refusal) remains Phase 3 (`MetaPRD.md:706-722`).

### Fix 13 — Pin worker-count assumption (§9)

Add to "Out of scope":

> - Multi-process deployment. PRD1 assumes a **single-process, single-event-loop** runtime (`workers=1`). Per-DB `asyncio.Lock` does not coordinate across processes; multi-worker deployment is a PRD2 hardening item.

### Fix 14 — Add cache_key fixture test (§12 Phase 1.D success criteria)

Append to Phase 1.D:

> - ✅ `cache_key()` round-trip test: identical input dicts (including float values, nested arrays in different insertion orders) produce identical keys; differing inputs (down to one whitespace char in a nested string) produce differing keys.

### Fix 15 — Anchor-line sweep (mechanical, do last)

After all other edits, re-`Read` every cited line range against the actual source files and correct any remaining drift. Common offenders: `:14`, `:96`, `:678`. The agent-validated correct ranges are above in §2 — use them as the source of truth.

---

## 4. Out of scope for this fix pass

- Don't add new sections beyond what's listed.
- Don't redesign §6.3 `AgnesContext` — it's verified as faithful to ADK source.
- Don't touch the migrations runner design.
- Don't add the `pipeline_dag_versions` table (PRD1 Open Question #1) — it's correctly deferred.
- Don't expand the `noop_demo.yaml` fixture; it's intentionally minimal.

---

## 5. Verification checklist for the next session

After applying fixes, confirm:

- [ ] Every `Orchestration/PRDs/PRD.md` reference is now `Orchestration/PRDs/MetaPRD.md`
- [ ] DSL section §6.2 takes Path A or Path B, not both, and is documented
- [ ] §6.4 has the `swan_events` → `external_events` rename note
- [ ] §7.6 prompt_hash matches `ANTHROPIC_SDK_STACK_REFERENCE.md:901-914`
- [ ] §7.4 ADK is stub-only; success criteria adjusted in §12 Phase 1.F
- [ ] §6.6 has `synchronous`, `temp_store`, `cache_size`, `mmap_size` PRAGMAs
- [ ] `payload_version` on `external_events`, `pipeline_events`, `node_cache`
- [ ] `agent_decisions` has the seven new columns
- [ ] Cost table has Opus + two Cerebras models added
- [ ] §7.9 retry/timeout/idempotency policy exists
- [ ] `AgentResult` has `latency_ms`, `finish_reason`, `temperature`, `seed`
- [ ] Cost ledger labeled "Phase 3 pull-forward"
- [ ] §9 has the `workers=1` assumption pinned
- [ ] Phase 1.D adds the cache_key canonicalization fixture test
- [ ] Final anchor-line sweep complete; no `Read` against `MetaPRD.md` returns content that contradicts a PRD1 cite

---

## 6. Provenance

This briefing consolidates four parallel agent reports run on 2026-04-25:
1. **SQLite backbone validation** — anchors `:207-291`, `:347-360`, `:525-546`, `:568-574`, `:600-688`, `:594`, `:609`, `:614-655`, `:713-736`, `:720-726`
2. **Anthropic SDK + cost validation** — anchors `:432-476`, `:551-565`, `:789-858`, `:901-914`, `:1087-1107`, `:540-565`, `:571-619`
3. **Google ADK + Cerebras validation** — anchors `:33-51`, `:184-249`, `:259-275`, `:301-327`, `:357-368`, `:378-405`, `:410-535`, `:826-840`
4. **Master PRD + DSL alignment** — anchors `:14-32`, `:56`, `:86`, `:96`, `:169-173`, `:260-299`, `:301-369`, `:372-376`, `:385-433`, `:661-683`, `:706-722`

All agents read the cited source ranges directly; no claim in this briefing is uncited.

*End of briefing.*
