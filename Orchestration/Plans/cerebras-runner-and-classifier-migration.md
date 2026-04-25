# Feature: Cerebras runner wiring + classifier migration

The following plan should be complete, but it's important that you validate
documentation and codebase patterns and task sanity before you start
implementing.

Pay special attention to naming of existing utils types and models. Import
from the right files etc.

## Feature Description

Wire Cerebras inference into the Agnes Autonomous CFO agent stack via the
already-stubbed `PydanticAiRunner`, then migrate three high-ROI classifier
agents (anomaly flagging, GL account assignment, counterparty
classification) from Anthropic Sonnet to Cerebras `gpt-oss-120b`. The
migration must preserve the existing decision-trace + per-employee cost
attribution pipeline (`agent_decisions` + `agent_costs` rows written
atomically per call by `audit.propose_checkpoint_commit`).

The `PydanticAiRunner` keeps its registry name for backwards compat with
the executor's `pydantic_ai → cerebras` provider mapping, but is
implemented as raw `AsyncOpenAI` against Cerebras' OpenAI-compatible
endpoint (`https://api.cerebras.ai/v1`). This avoids pulling Pydantic AI
into the request path when our agents are already pure single-shot
`submit_*` classifiers — a framework would add code without earning its
keep (per `Orchestration/research/CEREBRAS_STACK_REFERENCE.md` §13–14).

Document extraction (vision) and report rendering stay on the existing
Anthropic runner — Cerebras has no multimodal models as of April 2026,
and report narration is a separate net-new feature, not a runner swap.

## User Story

As an Agnes operator running period-close + Swan-driven booking pipelines
I want classifier agents to run on Cerebras' open-weight models at ~2,000 tok/s
So that classification latency drops 10–20× (Sonnet ~250ms → Cerebras ~25ms),
   per-call cost drops 5–50× (Sonnet $3/$15 per 1M → gpt-oss-120b $0.35/$0.75
   per 1M), and the per-employee AI-credit ledger still attributes every
   token to the right `employees.id` for the demo wedge.

## Problem Statement

Today every LLM-backed classifier in the stack runs on `claude-sonnet-4-6`
via `AnthropicRunner`. Three concrete consequences:

1. **Hot-path latency is tight.** The `transaction_booked` pipeline has a
   <5s SLA from Swan webhook to posted JE. When the deterministic
   counterparty/GL cascades miss (~20% of txs per RealMetaPRD §7.4),
   two Sonnet calls eat ~500–600ms — half the budget — leaving thin
   headroom for SQL, retrieval, and Pydantic validation.
2. **Per-call cost is dominated by classification volume.** Anomaly
   flagging runs every period close × every reporting cycle (period
   close, VAT return, year-end). At HEC-Paris demo scale this is small;
   at any realistic production cadence the bill is dominated by tasks
   Cerebras does for ~1/50th the price.
3. **The runner seam is already designed but idle.** `pydantic_ai_runner.py:27`
   raises `NotImplementedError`. The registry routes `pydantic_ai` to
   it, the executor maps it to `provider='cerebras'` for cost
   attribution, the audit schema has Cerebras-specific columns
   (`response_id`, `reasoning_tokens`, `finish_reason`, `prompt_hash`).
   Everything around the seam is wired; only the seam itself is empty.

## Solution Statement

Implement `PydanticAiRunner._run_impl()` as a raw `AsyncOpenAI` client
pointed at Cerebras' OpenAI-compatible endpoint, translating between the
Anthropic-shaped tool dicts the agents already build (`{"name", "description",
"input_schema"}`) and the OpenAI-shaped tool dicts Cerebras expects
(`{"type":"function", "function":{"name", "description", "parameters",
"strict":true}}`). Auto-inject `additionalProperties: false` recursively
into translated schemas so Cerebras' constrained decoding will accept
them. Mirror the response-extraction logic from `AnthropicRunner` so the
returned dict slots straight into the existing `run()` wrapper.

Add a one-line `_default_runner()` helper in `registries.py` that reads
`AGNES_LLM_PROVIDER` (default `anthropic`) and returns `"anthropic"` or
`"pydantic_ai"`. The three target classifier agents replace their
hard-coded `get_runner("anthropic")` with `get_runner(_default_runner())`.
The vision-bound `document_extractor.py` keeps its hard-coded `"anthropic"`.

Stage the rollout: Phase 1 lands the runner + the off-SLA anomaly agent
behind the flag (lowest blast radius). Phase 2 migrates the SLA-critical
counterparty + GL classifiers once the runner has a clean test record.

## Feature Metadata

**Feature Type**: New Capability (provider runner) + Migration (3 classifiers)
**Estimated Complexity**: Medium
**Primary Systems Affected**:
  - `backend/orchestration/runners/` (new live runner impl)
  - `backend/orchestration/registries.py` (default-runner helper)
  - `backend/orchestration/agents/` (3 agents migrated)
  - `backend/orchestration/cost.py` (rate-table verification only)
  - `pyproject.toml` + `.env.example` + `CLAUDE.md` + `README.md`
**Dependencies**:
  - `openai>=1.30` (new direct dep — already transitively pulled by
    `pydantic-ai-slim[cerebras]` but we want it explicit)
  - Cerebras account + API key (env var `CEREBRAS_API_KEY`)

---

## CONTEXT REFERENCES

### Relevant Codebase Files — IMPORTANT: YOU MUST READ THESE BEFORE IMPLEMENTING

- `Orchestration/research/CEREBRAS_STACK_REFERENCE.md` (full doc) — Why:
  the contract this feature implements against. §2 (timeout=4.0), §5 (tool
  calling + strict-mode requirements), §6 (structured output), §7 (submit-
  tool pattern), §9 (pricing + reasoning tokens), §10 (decision-trace
  fields), §13 (raw AsyncCerebras + LLMClient protocol — the architectural
  precedent for our implementation), §15 anti-patterns, §16 Claude-vs-
  Cerebras parity gaps.
- `backend/orchestration/runners/pydantic_ai_runner.py` (lines 1–70) —
  Why: the stub to fill in. The wrapper `run()` method (lines 31–70) is
  already complete; only `_run_impl()` (lines 24–29) is the gap.
  **Critical:** `run()` already (a) hashes the prompt, (b) builds
  `AgentResult` from `_run_impl()`'s dict return, (c) computes
  `latency_ms`. Do not duplicate any of this in `_run_impl()`.
- `backend/orchestration/runners/anthropic_runner.py` (lines 52–199) —
  Why: the response-shape mirror. The `_extract_submit_tool_input()`
  helper (lines 52–76), `_usage_from_anthropic()` (lines 79–94), and the
  request-build pattern in `run()` (lines 116–199) are what we recreate
  for Cerebras. Note: AnthropicRunner returns the `AgentResult` directly
  from `run()` — our `_run_impl()` returns a plain dict that the wrapper
  in `pydantic_ai_runner.py:31–70` converts to `AgentResult`.
- `backend/orchestration/runners/base.py` — Why: defines `AgentResult`
  + `TokenUsage` dataclasses. `_run_impl()` must return a dict whose
  shape matches what the wrapper expects (line 51–69 of
  `pydantic_ai_runner.py` — keys: `output`, `model`, `response_id`,
  `alternatives`, `confidence`, `usage` (with `input_tokens`,
  `output_tokens`, `cache_read_tokens`, `cache_write_tokens`,
  `reasoning_tokens`), `finish_reason`).
- `backend/orchestration/cost.py` (lines 13–41) — Why: rate table
  already includes `("cerebras", "gpt-oss-120b")` at $0.35/$0.75 per 1M
  (line 22–23) and `("cerebras", "llama3.3-70b")` at $0.60/$0.60 (line
  20–21). **Verify these match `CEREBRAS_STACK_REFERENCE.md` §9 before
  shipping.** No new entries needed for Phase 1.
- `backend/orchestration/registries.py` (lines 22–26, 59–67) — Why:
  the runner registry. `get_runner(key)` already returns a fresh instance
  on each call. Add `_default_runner()` helper here.
- `backend/orchestration/audit.py` (lines 23–76) — Why: confirms
  `propose_checkpoint_commit` is provider-agnostic. It pulls
  `result.usage.*` and writes one `agent_decisions` + one `agent_costs`
  row in a single `write_tx`. **Do not modify.**
- `backend/orchestration/agents/anomaly_flag_agent.py` (lines 58–123) —
  Why: Phase-1 migration target. Tool schema at lines 67–93 is
  Anthropic-shape (`input_schema` not `parameters`). Confirm
  `additionalProperties: false` is auto-injected by the runner — do
  NOT edit the agent's tool dict.
- `backend/orchestration/agents/gl_account_classifier_agent.py`
  (lines 79–107, 126–135) — Why: Phase-2 migration target. Schema has a
  closed-list `enum` for `gl_account` (line 90 — sourced from
  `chart_of_accounts` at request time, ~50–100 codes — well under
  Cerebras' 500-enum limit). The cache writeback at lines 50–73 must
  keep working unchanged.
- `backend/orchestration/agents/counterparty_classifier.py` (lines
  33–51, 162–206) — Why: Phase-2 migration target. Schema is the
  smallest of the three (`counterparty_id` int|null, `confidence`
  number, optional `alternatives` array). Cache writeback at lines
  108–159 must keep working.
- `backend/orchestration/agents/document_extractor.py` (line 124,
  `deadline_s=15.0`) — Why: confirm exclusion. Vision-bound. Stays on
  Anthropic. Read-only.
- `backend/orchestration/executor.py` (lines 251–285) — Why: confirms
  `pydantic_ai → "cerebras"` provider mapping is already wired. Read-
  only for Phase 1.
- `backend/orchestration/store/migrations/audit/audit.sql` (lines 16–55) —
  Why: confirms `agent_decisions` + `agent_costs` schema accommodates
  Cerebras observability fields (no migration needed).
- `backend/tests/test_anomaly_flag_agent.py` — Why: existing test
  pattern to mirror for the Cerebras-runner integration test.
- `Orchestration/PRDs/RealMetaPRD.md` §6.4, §7.4, §7.7, §7.10 — Why:
  the contract. Cite specific § lines in the PR description per repo
  convention.
- `memory/project_pitch_direction.md` — Why: the per-employee AI-credit
  ledger is the demo wedge — every Cerebras call must attribute cost to
  the calling employee. The audit pipeline already does this; do not
  break it.
- `CLAUDE.md` (Hard rules + How to run tests sections) — Why: pytest
  must run with the 15s timeout in `pytest.ini`; full-suite invocations
  must run in Bash background. New env vars need a CLAUDE.md bullet.

### New Files to Create

- `backend/orchestration/runners/cerebras_impl.py` — Pure-function helpers
  for the Cerebras runner: schema translation
  (Anthropic → OpenAI-shape), `additionalProperties: false` auto-
  injection, response parsing (`tool_calls[0].function.arguments` →
  `output` dict). Splitting these out of `pydantic_ai_runner.py` keeps
  the runner module thin and makes the helpers unit-testable without
  network. **One file, ~120 lines.**
- `backend/tests/test_cerebras_runner.py` — Unit tests for the helpers
  + a runner test using a mocked `AsyncOpenAI` client. Covers: schema
  translation correctness, additionalProperties injection at every
  level, tool-call argument JSON parsing, finish_reason mapping,
  reasoning_token capture, cost.py integration smoke (`micro_usd` must
  return non-zero for a Cerebras usage payload).
- `backend/tests/test_default_runner_helper.py` — Unit test for
  `_default_runner()` env-var dispatch.

No new migrations. No new pipeline YAML. No frontend changes.

### Relevant Documentation — YOU SHOULD READ THESE BEFORE IMPLEMENTING

- [Cerebras Inference — OpenAI compatibility](https://inference-docs.cerebras.ai/openai)
  - Specific section: tools + tool_choice + response_format
  - Why: confirms the wire shape we translate to. Note: `tools` and
    `response_format` cannot coexist; we use the submit-tool pattern only.
- [Cerebras — Tool Calling capability](https://inference-docs.cerebras.ai/capabilities/tool-use)
  - Specific section: Strict-mode requirements
  - Why: validates our `additionalProperties: false` auto-injection +
    why we forbid regex `pattern`, `format`, `minItems/maxItems`.
- [Cerebras — Rate limits](https://inference-docs.cerebras.ai/support/rate-limits)
  - Specific section: PAYG / Developer tier RPM and TPM
  - Why: confirms a Developer-tier key handles a hackathon demo
    (1,000 RPM on gpt-oss-120b). Free tier silently caps context to 8k
    — never run with the free tier.
- [Cerebras — Pricing](https://www.cerebras.ai/pricing)
  - Specific section: gpt-oss-120b row
  - Why: verify `cost.py:22–23` rates haven't drifted before shipping.
- [OpenAI Python SDK](https://github.com/openai/openai-python) — `AsyncOpenAI`
  - Specific section: `chat.completions.create` with `tools=` and
    `tool_choice={"type":"function","function":{"name":...}}`
  - Why: this is the API our runner calls.

### Patterns to Follow

**Naming Conventions:**
- Modules: `snake_case.py`. Class names: `CamelCase`. Private helpers:
  `_leading_underscore`. Match `anthropic_runner.py` exactly: free
  functions for translation/extraction, single class with `run()` and
  `_run_impl()`.

**Error Handling (mirror anthropic_runner.py:151–181):**
- Wrap the SDK call in `try/except`. On timeout (`asyncio.TimeoutError`,
  `APITimeoutError`), return an `AgentResult`-shaped dict with
  `output=None`, `finish_reason="timeout"`, `usage` zero-filled. On other
  exception, `finish_reason=f"error:{type(exc).__name__}"`. **Never raise
  from `_run_impl` for transport errors** — the deterministic fallback
  in the pipeline relies on a clean None result (RealMetaPRD §7.9).
- Tool-name fabrication (Cerebras returns a `tool_call` whose `name` is
  not the `submit_*` we sent): treat as parse failure, return
  `output=None`, `finish_reason="tool_name_mismatch"`. Caller's
  `confidence_gate` will route to `needs_review`.
- Schema-strict failures: if `arguments` JSON parses but Pydantic
  validation downstream fails, that's the *agent's* concern — the
  runner returns the parsed dict regardless. `submit_*` tools today are
  `dict[str,Any]` so there's no Pydantic validation in-runner.

**Logging Pattern:**
- The codebase does not use a structured logger in `runners/*.py` today
  (grep returns no results in `anthropic_runner.py`). Match it: no
  logging in the runner. The audit row is the log.

**Other Relevant Patterns:**
- **Singleton client.** `anthropic_runner.py:32–49` keeps a module-level
  `_client` and lazy-initializes on first call. Mirror this exactly: a
  module-level `_client: AsyncOpenAI | None = None`, `_get_client()`
  that constructs with `base_url="https://api.cerebras.ai/v1"`,
  `api_key=os.environ.get("CEREBRAS_API_KEY")`, `timeout=4.0`. Tests
  monkey-patch `_client` directly to inject a mock.
- **Lazy-tolerant import.** `anthropic_runner.py:24–27` wraps `import
  anthropic` in try/except so tests that don't need a real key still
  pass. Do the same for `from openai import AsyncOpenAI`.
- **Per-call deadline override.** `anthropic_runner.py:152–159` uses
  `asyncio.wait_for(..., timeout=deadline_s + 1.0)` to honor the
  `deadline_s` kwarg. Mirror this — the wrapper `run()` in
  `pydantic_ai_runner.py:31–43` already passes `deadline_s` through to
  `_run_impl` (note: it doesn't yet — the current stub signature on
  line 24–26 omits `deadline_s`. Add it.).
- **`tool_choice` forcing for `submit_*`.** Mirror
  `anthropic_runner.py:144–149`. Cerebras shape:
  `{"type": "function", "function": {"name": submit_tool_name}}`.
- **`max_tokens` parameter name.** Cerebras (OpenAI-shape) uses
  `max_completion_tokens`, not `max_tokens`. Translate.

---

## IMPLEMENTATION PLAN

### Phase 1: Foundation — environment + schema-translation helpers (no behavior change)

**Tasks:**
- Add `openai` to `pyproject.toml` dependencies.
- Add `CEREBRAS_API_KEY` to `.env.example`.
- Verify `cost.py` Cerebras rate rows match the reference doc; flag the
  reasoning-token cost basis question for resolution.
- Build `cerebras_impl.py`: pure-function helpers for tool-schema
  translation (Anthropic → OpenAI), `additionalProperties:false`
  recursive injection, and response parsing.
- Unit tests for the helpers — no network, no API key needed.

**Validation gate (Phase 1 → 2):** unit tests in
`backend/tests/test_cerebras_runner.py` pass for schema translation +
extraction, with a fixture set covering all three target agents'
schemas. `uv sync --extra dev` completes cleanly.

### Phase 2: Live runner — implement `PydanticAiRunner._run_impl()`

**Tasks:**
- Implement `_run_impl()` in `pydantic_ai_runner.py` using `AsyncOpenAI`
  pointed at the Cerebras OpenAI-compat endpoint.
- Add `deadline_s` to the `_run_impl()` signature so the wrapper can
  pass through the per-call deadline (the current stub omits it).
- Add the singleton client + lazy-tolerant `from openai import AsyncOpenAI`
  guard at the top of the module.
- Surface timeouts/errors as `output=None` results, mirroring
  `anthropic_runner.py:151–181`.

**Validation gate (Phase 2 → 3):** mocked-client test exercises the full
runner round-trip (`run()` builds the AgentResult correctly from a
`_run_impl()` that returns a canned Cerebras response); cost.py's
`micro_usd` produces non-zero for the canned usage payload; latency_ms is
captured.

### Phase 3: Default-runner helper + first agent migration (anomaly_flag, off SLA)

**Tasks:**
- Add `_default_runner()` to `registries.py` — reads
  `AGNES_LLM_PROVIDER` env, returns `"anthropic"` (default) or
  `"pydantic_ai"` (when env=`cerebras`).
- Migrate `anomaly_flag_agent.py:114` to call
  `get_runner(_default_runner())` and update the model string to
  `gpt-oss-120b` when the runner is Cerebras (use a small per-agent
  helper or pass the model name through the helper — see Tasks below).
- Reduce `max_tokens=1024 → 800` (anomaly schema is compact; saves
  ~200ms tail latency per Cerebras §4 throughput).
- Run `pytest backend/tests/test_anomaly_flag_agent.py` against both
  providers (set the env var explicitly per run).

**Validation gate (Phase 3 → 4):** anomaly agent passes its existing
test suite under both `AGNES_LLM_PROVIDER=anthropic` and
`AGNES_LLM_PROVIDER=cerebras`; the second mode requires a live
`CEREBRAS_API_KEY` (skip with `pytest.mark.skipif` if not present).
A manual period-close trigger via the existing
`backend/scripts/replay_swan_seed.py` produces an `agent_costs` row
with `provider='cerebras'` and a non-zero `cost_micro_usd`.

### Phase 4: Hot-path migration (counterparty + GL classifier)

**Tasks:**
- Migrate `gl_account_classifier_agent.py:126–135` and
  `counterparty_classifier.py:181–190` the same way as anomaly. Drop
  `max_tokens=512 → 256` for both.
- Run the Swan-replay end-to-end seed to confirm <5s budget holds with
  Cerebras on the Swan hot path (target: agent step ≤300ms each, vs
  Sonnet ~250–600ms).
- **Confidence-gate regression check:** snapshot the distribution of
  `confidence` values across 50 replay-seed transactions before and after
  the swap. If the median drops by >0.1, recalibrate the
  `confidence_gate` floor (currently 0.50) or run a 3-call temperature
  ensemble per Cerebras §7.

**Validation gate (Phase 4 → 5):** full Swan replay seed completes; no
new entries in the review queue beyond the pre-migration baseline (±10%
tolerance).

### Phase 5: Documentation + flag-on default decision

**Tasks:**
- Update `README.md` "what works today" with Cerebras runner.
- Update `CLAUDE.md` "Hard rules" + env-var section.
- Update `.env.example`.
- Decide whether to flip the default to `cerebras` or keep `anthropic`
  (recommend: keep `anthropic` for the demo until Phase 4 validation is
  green; flip in a follow-up PR).

**Validation gate (Phase 5 → ship):** `pytest backend/tests/` full suite
green in background mode (per CLAUDE.md "How to run tests"); README +
CLAUDE.md reflect the live state.

---

## STEP-BY-STEP TASKS

IMPORTANT: Execute every task in order, top to bottom. Each task is
atomic and independently testable.

### 1. UPDATE `pyproject.toml` — add `openai` to base dependencies

- **IMPLEMENT**: Add `openai>=1.30,<2` to `dependencies = [...]` (around
  lines 11–26). Keep `anthropic>=0.40,<2` unchanged.
- **PATTERN**: Existing `anthropic` line is the model — same version
  pin style.
- **IMPORTS**: N/A (build config).
- **GOTCHA**: `openai` is already pulled transitively by
  `pydantic-ai-slim[cerebras]` in the optional `pydantic_ai` extra.
  Making it a direct dep means we don't need the optional extra
  installed for the runner to import. **Do not remove the optional
  extra** — it's a separate (currently unused) Pydantic AI integration
  path that may matter post-MVP.
- **VALIDATE**: `uv sync --extra dev && uv pip show openai` prints
  version ≥1.30.

### 2. UPDATE `.env.example` — add `CEREBRAS_API_KEY`

- **IMPLEMENT**: Append two lines:
  ```
  # Cerebras inference (gpt-oss-120b classifier path; required when
  # AGNES_LLM_PROVIDER=cerebras). Get one at https://cloud.cerebras.ai
  CEREBRAS_API_KEY=
  AGNES_LLM_PROVIDER=anthropic
  ```
- **PATTERN**: Match the existing `ANTHROPIC_API_KEY=` placeholder
  style in the same file.
- **IMPORTS**: N/A.
- **GOTCHA**: The literal API key shared in the conversation that
  triggered this plan was exposed in chat. Rotate before relying on it.
- **VALIDATE**: `grep -c CEREBRAS_API_KEY .env.example` returns ≥1.

### 3. VERIFY `backend/orchestration/cost.py` — Cerebras rates match the reference doc

- **IMPLEMENT**: Read `cost.py:13–26` and compare against
  `CEREBRAS_STACK_REFERENCE.md` §9 pricing table. Currently:
  - `("cerebras", "gpt-oss-120b"): $0.35 in / $0.75 out` ✓ matches doc
  - `("cerebras", "llama3.3-70b"): $0.60 in / $0.60 out` ✓ matches doc
  - `("cerebras", "qwen-3-235b"): $0.60 in / $1.20 out` ✓ matches doc
  No code changes if all three match.
- **PATTERN**: Existing `COST_TABLE_MICRO_USD` dict is a
  `(provider, model) -> {input,output,cache_read,cache_write}` shape.
- **IMPORTS**: N/A.
- **GOTCHA**: Cerebras `cache_read` cost is **not discounted** (ref doc
  §9: "cached tokens cost the same as fresh input tokens — caching saves
  *latency*, not money"). The current cost.py uses the same value for
  `cache_read` as `input` for Cerebras rows — this is correct. **Do not
  change it.** Per ref doc §9, `reasoning_tokens` are billed *as
  completion tokens*. Our `cost.py:36–41` formula does not include
  `reasoning_tokens` in the cost basis. **Add a fifth term:**
  `+ usage.reasoning_tokens * r["output"]`. This is a one-line fix and
  is correct for both Cerebras (charges them) and Anthropic (always 0,
  per `anthropic_runner.py:93`).
- **VALIDATE**: `python -c "from backend.orchestration.cost import
  micro_usd; from backend.orchestration.runners.base import TokenUsage;
  print(micro_usd(TokenUsage(input_tokens=1_000_000,
  output_tokens=1_000_000, reasoning_tokens=500_000), 'cerebras',
  'gpt-oss-120b'))"` → expect `1475` (350 + 750 + 375).

### 4. CREATE `backend/orchestration/runners/cerebras_impl.py` — pure helpers

- **IMPLEMENT**: Module with three pure functions (no I/O, no async):
  ```python
  def translate_tool_schema(anthropic_tool: dict) -> dict:
      """Convert {name, description, input_schema} -> OpenAI {type, function}.

      - Wraps in {"type": "function", "function": {...}}.
      - Renames `input_schema` -> `parameters`.
      - Sets `strict: True` on the function.
      - Recursively injects `additionalProperties: false` into every
        object-typed schema node (top-level, every nested `items`,
        every `properties.<x>` whose `type == "object"`).
      - Leaves `enum`, `type`, `description`, `required`, `items`
        untouched otherwise.
      """

  def translate_tool_choice(submit_tool_name: str) -> dict:
      """{"type":"function","function":{"name": submit_tool_name}}"""

  def parse_response(resp) -> dict:
      """Extract our standard dict shape from a Cerebras chat-completion
      response.

      Returns:
        {
          "output": dict | None,        # JSON-parsed tool args
          "model": str,
          "response_id": str,
          "alternatives": list | None,  # from output.alternatives
          "confidence": float | None,   # from output.confidence
          "usage": {                    # zero-filled if usage missing
            "input_tokens": int,
            "output_tokens": int,
            "cache_read_tokens": int,   # cached_tokens
            "cache_write_tokens": 0,    # Cerebras has no manual cache
            "reasoning_tokens": int,
          },
          "finish_reason": str,         # "stop" | "tool_calls" | "length"
                                        # | "tool_name_mismatch"
        }

      Tool-name mismatch handling: if the message has tool_calls, but
      `tool_calls[0].function.name` does not start with "submit",
      return output=None, finish_reason="tool_name_mismatch".

      JSON-parse failure on `tool_calls[0].function.arguments` is the
      same: output=None, finish_reason="tool_call_parse_error".
      """
  ```
- **PATTERN**: Mirror `anthropic_runner.py:52–94` for parse_response
  (helper-style, returns None on miss). Use
  `getattr(obj, "field", default)` defensively — OpenAI SDK objects
  use Pydantic underneath.
- **IMPORTS**:
  ```python
  from __future__ import annotations
  import json
  from typing import Any
  ```
- **GOTCHA**:
  - `additionalProperties` injection must be **recursive** through
    `properties[*]`, `items` (for arrays), and any nested `type:object`.
    Do not inject into `enum` arrays or scalar schemas.
  - Cerebras `usage.prompt_tokens_details.cached_tokens` is the cache-
    read field; default 0 if missing. `usage.completion_tokens_details
    .reasoning_tokens` ditto. Both `*_details` sub-objects may be absent
    on non-reasoning models.
  - `output.alternatives` may be absent on counterparty/GL submits when
    the model didn't include one — `.get("alternatives")` returning
    None is fine; the wrapper handles it.
  - **Do not** validate the parsed `output` against any Pydantic model
    here. The wrapper's contract with the agent is "return whatever JSON
    the model emitted under the submit tool"; agent-side code already
    handles dict-shape guards (e.g. `gl_account_classifier_agent.py:139`).
- **VALIDATE**: After creating tests in step 5, run `pytest
  backend/tests/test_cerebras_runner.py::test_translate_tool_schema -x`.

### 5. CREATE `backend/tests/test_cerebras_runner.py` — helper unit tests

- **IMPLEMENT**: pytest module with at least these tests:
  - `test_translate_tool_schema_anomaly`: feeds the anomaly agent's
    tool dict (literal copy from
    `anomaly_flag_agent.py:59–94`), asserts the result has
    `type=="function"`, `function.strict==True`,
    `function.parameters.additionalProperties==False`,
    `function.parameters.properties.anomalies.items.additionalProperties==False`.
  - `test_translate_tool_schema_gl`: same but for the GL agent's tool
    dict (build a fixture with a 3-element `enum` for `gl_account`).
    Assert the `enum` survives intact and `additionalProperties:false`
    is injected.
  - `test_translate_tool_schema_counterparty`: same for
    `counterparty_classifier.py:33–51`.
  - `test_translate_tool_choice`: assert
    `translate_tool_choice("submit_anomalies") ==
    {"type":"function","function":{"name":"submit_anomalies"}}`.
  - `test_parse_response_happy_path`: build a fake response object
    (use `types.SimpleNamespace` or a Pydantic-mock) with a single
    tool_call to `submit_anomalies`, valid JSON arguments. Assert
    `output` is the parsed dict, `finish_reason=="tool_calls"`,
    `usage.input_tokens` and `usage.output_tokens` correctly captured.
  - `test_parse_response_tool_name_mismatch`: response has
    `tool_calls[0].function.name == "fabricated_tool"`. Assert
    `output is None`, `finish_reason == "tool_name_mismatch"`.
  - `test_parse_response_arg_json_error`: arguments is `"{not json"`.
    Assert `output is None`, `finish_reason ==
    "tool_call_parse_error"`.
  - `test_parse_response_reasoning_tokens`: usage object includes
    `completion_tokens_details.reasoning_tokens=42`. Assert captured.
  - `test_cost_micro_usd_for_cerebras_usage`: build a `TokenUsage`
    with `input_tokens=1_000_000, output_tokens=500_000,
    reasoning_tokens=100_000`. Assert
    `micro_usd(usage, 'cerebras', 'gpt-oss-120b') == 350 + 375 + 75 ==
    800`. **This test will fail until step 3's reasoning-token line
    lands.**
- **PATTERN**: Mirror an existing simple agent test like
  `backend/tests/test_noop_agent.py` (if present) or
  `test_anomaly_flag_agent.py` for fixture style.
- **IMPORTS**:
  ```python
  import json
  import pytest
  from types import SimpleNamespace
  from backend.orchestration.runners.cerebras_impl import (
      translate_tool_schema, translate_tool_choice, parse_response,
  )
  from backend.orchestration.cost import micro_usd
  from backend.orchestration.runners.base import TokenUsage
  ```
- **GOTCHA**: The Cerebras response object's `usage` field is a
  Pydantic model on the real SDK; tests must use a duck-typed fake
  with `getattr` access. Do not import the real Cerebras response
  classes into the test (couples test to SDK internal layout).
- **VALIDATE**: `pytest backend/tests/test_cerebras_runner.py -x
  --timeout=15`.

### 6. UPDATE `backend/orchestration/runners/pydantic_ai_runner.py` — implement `_run_impl()`

- **IMPLEMENT**: Replace the `_run_impl` stub (lines 24–29) with a
  live implementation. Add the singleton client and lazy import at
  module top. Pseudocode:

  ```python
  # at module top, alongside existing imports
  import asyncio, os
  try:
      from openai import AsyncOpenAI
  except ImportError:  # pragma: no cover
      AsyncOpenAI = None  # type: ignore[assignment]

  from .cerebras_impl import (
      translate_tool_schema, translate_tool_choice, parse_response,
  )

  _client: Any = None

  def _get_client() -> Any:
      global _client
      if _client is not None:
          return _client
      if AsyncOpenAI is None:
          raise RuntimeError(
              "openai SDK not installed; install with `pip install openai`"
          )
      _client = AsyncOpenAI(
          base_url="https://api.cerebras.ai/v1",
          api_key=os.environ.get("CEREBRAS_API_KEY"),
          timeout=4.0,
      )
      return _client
  ```

  Then `_run_impl` becomes:

  ```python
  async def _run_impl(
      self, *, system: str, tools: list[dict], messages: list[dict],
      model: str, temperature: float, max_tokens: int, seed: int | None,
      deadline_s: float = 4.5,
  ) -> dict[str, Any]:
      client = _get_client()
      api_messages = ([{"role": "system", "content": system}] if system else []) + messages

      api_tools = [translate_tool_schema(t) for t in tools] if tools else None
      submit_name = next(
          (t["name"] for t in tools if t.get("name", "").startswith("submit")),
          None,
      )
      tool_choice = translate_tool_choice(submit_name) if submit_name else "auto"

      kwargs: dict[str, Any] = {
          "model": model,
          "messages": api_messages,
          "max_completion_tokens": max_tokens,  # NB: not max_tokens
          "temperature": temperature,
          "parallel_tool_calls": False,         # ref §5: serialize submit_*
      }
      if api_tools:
          kwargs["tools"] = api_tools
          kwargs["tool_choice"] = tool_choice
      if seed is not None:
          kwargs["seed"] = seed

      try:
          resp = await asyncio.wait_for(
              client.chat.completions.create(**kwargs),
              timeout=deadline_s + 1.0,
          )
      except Exception as exc:
          timeout_marker = (
              isinstance(exc, asyncio.TimeoutError)
              or type(exc).__name__ in ("APITimeoutError", "TimeoutError")
          )
          return {
              "output": None,
              "model": model,
              "response_id": None,
              "alternatives": None,
              "confidence": None,
              "usage": {},
              "finish_reason": "timeout" if timeout_marker
                               else f"error:{type(exc).__name__}",
          }
      return parse_response(resp)
  ```
- **PATTERN**: Mirror `anthropic_runner.py:113–199` exactly (singleton
  client, lazy-tolerant SDK import, try/except envelope, deadline kwarg
  passed through asyncio.wait_for).
- **IMPORTS**: see pseudocode above. **Update the wrapper** `run()` (the
  existing method on lines 31–70) to pass `deadline_s` to `_run_impl`:
  the wrapper today omits it. Add `deadline_s=deadline_s` to the
  `_run_impl(...)` call on line 46–49.
- **GOTCHA**:
  - `max_completion_tokens` not `max_tokens` (OpenAI shape — ref §2).
  - `parallel_tool_calls=False` for `submit_*` flows (ref §5: a forced
    tool-choice + `parallel_tool_calls=False` is the canonical submit-
    tool pattern).
  - **Do not** pass `system` as a top-level kwarg (Anthropic does this);
    Cerebras/OpenAI shape requires `system` as the first message with
    `role:"system"`. Build `api_messages` accordingly.
  - Cerebras uses `seed` like OpenAI; it's not deterministic-by-bytes
    (ref §15 anti-pattern #7) but reduces variance. Pass through when
    the agent provides one.
  - **Do not** use `response_format` — Cerebras forbids combining it
    with `tools` (ref §5). The submit-tool pattern is what gives us
    schema enforcement.
- **VALIDATE**: After step 7's runner integration test lands:
  `pytest backend/tests/test_cerebras_runner.py::test_runner_round_trip
  -x --timeout=15`.

### 7. UPDATE `backend/tests/test_cerebras_runner.py` — runner round-trip with mocked client

- **IMPLEMENT**: Add `test_runner_round_trip` that:
  - Builds a fake `AsyncOpenAI` client whose
    `chat.completions.create(...)` returns a hand-rolled Cerebras-shape
    response (mock with `SimpleNamespace`).
  - Monkey-patches
    `backend.orchestration.runners.pydantic_ai_runner._client` to the
    fake.
  - Calls `PydanticAiRunner().run(...)` with the anomaly agent's tool
    dict.
  - Asserts the returned `AgentResult` has correct `output`,
    `confidence`, `usage.input_tokens`, `latency_ms > 0`,
    `prompt_hash` non-empty.
  - Adds `test_runner_timeout`: fake client raises
    `asyncio.TimeoutError` from `chat.completions.create`. Assert
    `result.output is None`, `result.finish_reason == "timeout"`.
- **PATTERN**: Mirror Anthropic-runner test patterns (look for any
  test in `backend/tests/` that already monkey-patches `_client`).
- **IMPORTS**: see step 5.
- **GOTCHA**: Use `pytest.fixture(autouse=False)` to reset
  `_client` to `None` between tests so they don't pollute each other.
- **VALIDATE**: `pytest backend/tests/test_cerebras_runner.py -x
  --timeout=15`.

### 8. UPDATE `backend/orchestration/registries.py` — add `_default_runner()`

- **IMPLEMENT**: Add at module bottom (after the `_REGISTRIES` dict):
  ```python
  import os

  def default_runner() -> str:
      """Read AGNES_LLM_PROVIDER and return a runner-registry key.

      'anthropic' (default), 'cerebras' -> 'pydantic_ai',
      'adk' -> 'adk' (kept for completeness; no live impl yet).
      """
      provider = os.environ.get("AGNES_LLM_PROVIDER", "anthropic").lower()
      if provider == "cerebras":
          return "pydantic_ai"
      if provider == "adk":
          return "adk"
      return "anthropic"
  ```
- **PATTERN**: Match the existing module's snake_case + `from __future__
  import annotations` style. Use a public name (`default_runner`, not
  `_default_runner`) so agents can import it without the lint warning.
- **IMPORTS**: `import os` at top, alongside the existing `import
  importlib`.
- **GOTCHA**: Read the env at call time, not at import time, so test
  fixtures using `monkeypatch.setenv` work without re-importing the
  module. `lru_cache`-ing this would defeat that — keep it
  uncached.
- **VALIDATE**: `pytest
  backend/tests/test_default_runner_helper.py -x --timeout=15` (test
  file from step 9).

### 9. CREATE `backend/tests/test_default_runner_helper.py`

- **IMPLEMENT**:
  ```python
  import pytest
  from backend.orchestration.registries import default_runner

  def test_defaults_to_anthropic_when_unset(monkeypatch):
      monkeypatch.delenv("AGNES_LLM_PROVIDER", raising=False)
      assert default_runner() == "anthropic"

  def test_cerebras_maps_to_pydantic_ai(monkeypatch):
      monkeypatch.setenv("AGNES_LLM_PROVIDER", "cerebras")
      assert default_runner() == "pydantic_ai"

  def test_anthropic_explicit(monkeypatch):
      monkeypatch.setenv("AGNES_LLM_PROVIDER", "anthropic")
      assert default_runner() == "anthropic"

  def test_unknown_value_falls_back_to_anthropic(monkeypatch):
      monkeypatch.setenv("AGNES_LLM_PROVIDER", "foobar")
      assert default_runner() == "anthropic"
  ```
- **PATTERN**: pytest + monkeypatch fixture, no fixtures shared with
  other tests.
- **IMPORTS**: see snippet.
- **GOTCHA**: None.
- **VALIDATE**: `pytest backend/tests/test_default_runner_helper.py
  -x --timeout=15`.

### 10. UPDATE `backend/orchestration/agents/anomaly_flag_agent.py` — first migration

- **IMPLEMENT**: Replace lines 114–123. Two changes only:
  ```python
  from ..registries import get_runner, default_runner

  ...

      runner_key = default_runner()
      model = "gpt-oss-120b" if runner_key == "pydantic_ai" else "claude-sonnet-4-6"
      runner = get_runner(runner_key)
      return await runner.run(
          ctx=ctx,
          system=system,
          tools=[tool],
          messages=messages,
          model=model,
          max_tokens=800,        # was 1024 — anomaly schema is compact
          temperature=0.0,
      )
  ```
- **PATTERN**: Keep the agent's tool-dict shape unchanged (`input_schema`
  not `parameters`). The runner translates. **Do not** add
  `additionalProperties: false` to the agent's tool dict — the runner
  injects it. Keeping the agent shape Anthropic-style means it still
  works under the AnthropicRunner when the flag is not set.
- **IMPORTS**: add `default_runner` to the existing
  `from ..registries import get_runner` line.
- **GOTCHA**: Do not move the `runner_key` resolution outside the `run`
  function — it must read env at request time so a single process can
  serve both providers (e.g., tests that flip the flag).
- **VALIDATE**:
  - `AGNES_LLM_PROVIDER=anthropic pytest
    backend/tests/test_anomaly_flag_agent.py -x --timeout=15`
  - With a real key set: `AGNES_LLM_PROVIDER=cerebras
    CEREBRAS_API_KEY=$CEREBRAS_API_KEY pytest
    backend/tests/test_anomaly_flag_agent.py -x --timeout=15`
  - Manual: `AGNES_LLM_PROVIDER=cerebras python
    backend/scripts/replay_swan_seed.py` (run period_close trigger),
    then SQL: `sqlite3 data/audit.db "SELECT provider, model,
    cost_micro_usd FROM agent_costs ORDER BY id DESC LIMIT 1"` →
    expect `cerebras|gpt-oss-120b|<positive int>`.

### 11. PHASE-2 GATE — confirm Phase-1 health before touching SLA path

Before proceeding to step 12, ensure:

- [ ] All step-10 validation commands green.
- [ ] An audit row with `provider='cerebras'` exists in `data/audit.db`.
- [ ] No regressions in `pytest backend/tests/ --timeout=15`
  (background-mode invocation per CLAUDE.md).
- [ ] Confidence-floor inspection: across 10+ Cerebras anomaly runs,
  the `confidence` field stays in [0, 1] and roughly tracks the
  Sonnet-baseline distribution (no systematic 0.0 or 1.0 collapse).

### 12. UPDATE `backend/orchestration/agents/gl_account_classifier_agent.py`

- **IMPLEMENT**: Apply the same pattern as step 10. Replace lines
  126–135:
  ```python
  from ..registries import get_runner, default_runner

  ...

      runner_key = default_runner()
      model = "gpt-oss-120b" if runner_key == "pydantic_ai" else "claude-sonnet-4-6"
      runner = get_runner(runner_key)
      result = await runner.run(
          ctx=ctx,
          system=system,
          tools=[tool],
          messages=messages,
          model=model,
          max_tokens=256,        # was 512 — single-enum pick is compact
          temperature=0.0,
      )
  ```
- **PATTERN**: Identical to anomaly migration (step 10).
- **IMPORTS**: add `default_runner`.
- **GOTCHA**:
  - The dynamic enum from `chart_of_accounts` (line 90 of agent file)
    must remain ≤500 entries (Cerebras strict-mode limit per ref §5).
    At HEC Paris demo scale this is ~80 codes — fine. **Add a runtime
    assert** in `_load_chart_codes`: `assert len(rows) <= 500` (raise
    a clear error if not).
  - The cache writeback to `account_rules` (lines 50–73) is unaffected
    by the runner swap.
- **VALIDATE**:
  - `AGNES_LLM_PROVIDER=anthropic pytest -k gl_account_classifier
    --timeout=15`
  - `AGNES_LLM_PROVIDER=cerebras pytest -k gl_account_classifier
    --timeout=15` (with `CEREBRAS_API_KEY` set; otherwise `pytest.mark
    .skipif`).

### 13. UPDATE `backend/orchestration/agents/counterparty_classifier.py`

- **IMPLEMENT**: Same pattern. Replace lines 181–190:
  ```python
  from ..registries import get_runner, default_runner

  ...

      runner_key = default_runner()
      model = "gpt-oss-120b" if runner_key == "pydantic_ai" else "claude-sonnet-4-6"
      runner = get_runner(runner_key)
      result = await runner.run(
          ctx=ctx,
          system=_SYSTEM_PROMPT,
          tools=[_SUBMIT_TOOL],
          messages=[{"role": "user", "content": user_content}],
          model=model,
          max_tokens=256,
          temperature=0.0,
      )
  ```
- **PATTERN**: Identical to anomaly + GL migrations.
- **IMPORTS**: add `default_runner`.
- **GOTCHA**:
  - `_SUBMIT_TOOL` (lines 33–51) declares `counterparty_id` as
    `["integer", "null"]` — verify this passes Cerebras strict-mode
    tool translation. Strict mode allows `["integer", "null"]` union
    types per OpenAI compat. If it fails: split into two passes
    (rare, only on a real failure).
  - Cache writeback at lines 108–159 (`_writeback_ai_pick`) writes to
    `counterparty_identifiers` with `source='ai'`. Unaffected by
    the runner swap.
- **VALIDATE**:
  - `AGNES_LLM_PROVIDER=anthropic pytest -k counterparty_classifier
    --timeout=15`
  - `AGNES_LLM_PROVIDER=cerebras` integration: a Swan-replay run
    from `backend/scripts/replay_swan_seed.py`. Confirm the
    `counterparty_identifiers` table grows by at least one
    `source='ai'` row, AND `data/audit.db` has the corresponding
    `agent_costs` row with `provider='cerebras'`.

### 14. UPDATE `backend/orchestration/cost.py` — reasoning-token cost basis

- **IMPLEMENT**: Edit `micro_usd` (lines 29–41) to charge reasoning
  tokens at the output rate:
  ```python
  return (
      usage.input_tokens         * r["input"]
      + usage.output_tokens      * r["output"]
      + usage.cache_read_tokens  * r["cache_read"]
      + usage.cache_write_tokens * r["cache_write"]
      + usage.reasoning_tokens   * r["output"]   # ref §9
  ) // 1_000_000
  ```
- **PATTERN**: Match existing single-expression return.
- **IMPORTS**: N/A.
- **GOTCHA**: Keep this in step 14 (not 3) so the cost test added in
  step 5 *fails first* on an unfixed `cost.py` (proving the test
  catches the bug), then passes after step 14. This is a deliberate
  red-green sequence — implement the test first, watch it fail,
  then fix.
- **VALIDATE**:
  `pytest backend/tests/test_cerebras_runner.py::test_cost_micro_usd_for_cerebras_usage
  -x --timeout=15`. Should now pass.

  Also: re-run the full anomaly + GL + counterparty test set under
  Anthropic mode. Reasoning-token cost basis is additive and will
  only affect Cerebras rows (Anthropic always reports
  `reasoning_tokens=0`).

### 15. UPDATE `README.md` — add Cerebras to the live-state list

- **IMPLEMENT**: Find the section that enumerates working agents/runners
  and add a bullet:
  > Cerebras runner (`PydanticAiRunner`, raw OpenAI-compat against
  > `https://api.cerebras.ai/v1`) live for the three classifier agents
  > (anomaly, GL, counterparty) when `AGNES_LLM_PROVIDER=cerebras`.
  > `claude-sonnet-4-6` remains the default until Phase-2 validation
  > closes; `document_extractor` stays on Anthropic (vision-only).
- **PATTERN**: Match the surrounding bullet style and the project's
  habit of citing PRD/CLAUDE.md sections.
- **IMPORTS**: N/A.
- **GOTCHA**: Per CLAUDE.md "Maintenance flag", README + CLAUDE.md must
  stay in sync. Update both in the same commit.
- **VALIDATE**: `grep -i cerebras README.md | wc -l` ≥1.

### 16. UPDATE `CLAUDE.md` — env vars + runner rule

- **IMPLEMENT**: Add to the "Hard rules carried over from RealMetaPRD"
  section:
  > `AGNES_LLM_PROVIDER=anthropic|cerebras` (default `anthropic`)
  > picks the classifier runner. Setting `cerebras` requires
  > `CEREBRAS_API_KEY` and routes anomaly_flag, gl_account_classifier,
  > and counterparty_classifier through `PydanticAiRunner`. Vision
  > paths (document_extractor) always use Anthropic.

  And under "Repository layout" → `backend/orchestration/runners/`,
  note that `pydantic_ai_runner.py` is now live (not stub).
- **PATTERN**: Match the existing "Hard rules" bullet style — short
  prescriptive sentences.
- **IMPORTS**: N/A.
- **GOTCHA**: None.
- **VALIDATE**: `grep -i AGNES_LLM_PROVIDER CLAUDE.md` returns the new
  bullet.

### 17. RUN full test suite + manual smoke

- **IMPLEMENT**: One Bash invocation in background mode (per CLAUDE.md):
  ```bash
  uv run pytest backend/tests/ --timeout=15 -q
  ```
  Poll for completion. With `AGNES_LLM_PROVIDER=anthropic` (default)
  no Cerebras key is needed and no live calls fire.

  Then with a real key set:
  ```bash
  AGNES_LLM_PROVIDER=cerebras CEREBRAS_API_KEY=$CEREBRAS_API_KEY \
      uv run python backend/scripts/replay_swan_seed.py
  ```
  After the replay, query `data/audit.db`:
  ```sql
  SELECT provider, model, COUNT(*) AS calls, SUM(cost_micro_usd) AS total_micro
  FROM agent_costs GROUP BY provider, model;
  ```
  Expect at least one `cerebras|gpt-oss-120b` row with non-zero total.
- **PATTERN**: Background pytest invocation per CLAUDE.md "How to run
  tests" — never block the foreground tool call on a full suite.
- **IMPORTS**: N/A.
- **GOTCHA**: SSE / asyncio.wait_for tests in the suite hang without
  the 15s pytest-timeout — leave the `pytest.ini` setting alone.
- **VALIDATE**: All tests green; audit DB shows the expected new rows.

---

## TESTING STRATEGY

### Unit Tests

Scope:
- `cerebras_impl.py` helpers (schema translation,
  additionalProperties recursive injection, response parsing,
  finish_reason mapping, reasoning-token capture). Pure — no network.
- `cost.py` `micro_usd` for Cerebras pairs incl. reasoning-token
  basis.
- `default_runner` env-var dispatch.
- `PydanticAiRunner.run()` round-trip via mocked `AsyncOpenAI`
  client (the singleton-pattern test).

All in `backend/tests/test_cerebras_runner.py` and
`test_default_runner_helper.py`. Use existing project pattern
(pytest, `monkeypatch`, `SimpleNamespace` for SDK fakes).

### Integration Tests

Scope:
- Existing `test_anomaly_flag_agent.py`, `test_counterparty_classifier.py`,
  `test_gl_account_classifier_agent.py` (whichever exist) run under
  both `AGNES_LLM_PROVIDER=anthropic` and `=cerebras`. The Cerebras
  runs `pytest.mark.skipif(not os.environ.get("CEREBRAS_API_KEY"))`.
- `backend/scripts/replay_swan_seed.py` end-to-end with the flag set
  to `cerebras` — confirms Swan webhook → Cerebras classifiers →
  posted JE → audit row chain.

### Edge Cases

- Cerebras API timeout (>4s). `_run_impl` returns
  `output=None, finish_reason="timeout"`; agent receives an
  `AgentResult` with `confidence=None`; `confidence_gate` routes to
  `needs_review`. Test by mocking `AsyncOpenAI.chat.completions
  .create` to raise `asyncio.TimeoutError`.
- Tool-name fabrication. Cerebras emits a `tool_call` whose `function
  .name` is not `submit_*`. `parse_response` returns `output=None,
  finish_reason="tool_name_mismatch"`. Test with a fake response.
- Tool-arg JSON parse error. `function.arguments` is malformed.
  Returns `finish_reason="tool_call_parse_error"`. Test.
- `seed=None` (the default). The kwargs dict must omit `seed`
  entirely (passing `seed=None` to OpenAI errors). Test.
- Empty `tools` list. `kwargs["tools"]` and `kwargs["tool_choice"]`
  are omitted entirely; the call should still succeed for free-text
  output. Unlikely in our pipeline (every classifier uses submit
  tools) but the runner must handle it for forward-compat. Test.
- `chart_of_accounts` has >500 entries → step-12 assert raises a clear
  error. Test by seeding the DB with 501 codes and confirming the
  agent raises rather than silently failing strict mode in Cerebras.
- Confidence calibration: same `submit_anomalies` payload run 10×
  through Sonnet vs Cerebras — assert both produce values in [0,1]
  (no schema regression). Track median + variance; if Cerebras
  systematically under-confidences, document that the floor (0.50)
  may need a one-line tweak in `conditions/gating.py`.

---

## VALIDATION COMMANDS

Execute every command to ensure zero regressions and 100% feature
correctness.

### Level 1: Syntax & Style

```bash
uv run python -c "import backend.orchestration.runners.pydantic_ai_runner; \
                  import backend.orchestration.runners.cerebras_impl; \
                  import backend.orchestration.registries; \
                  print('imports OK')"
```

(The repo does not enforce ruff/black/mypy in CI today per
`pyproject.toml` — if it grows them, add them here.)

### Level 2: Unit Tests

```bash
uv run pytest backend/tests/test_cerebras_runner.py -x --timeout=15
uv run pytest backend/tests/test_default_runner_helper.py -x --timeout=15
```

### Level 3: Integration Tests

```bash
# Default-provider mode — no Cerebras key needed.
uv run pytest backend/tests/test_anomaly_flag_agent.py \
              backend/tests/test_counterparty_classifier.py \
              backend/tests/test_gl_account_classifier_agent.py \
              -x --timeout=15

# Cerebras mode — requires a live key.
AGNES_LLM_PROVIDER=cerebras CEREBRAS_API_KEY=$CEREBRAS_API_KEY \
    uv run pytest -k "cerebras or anomaly or counterparty or gl_account" \
                  --timeout=15
```

Per CLAUDE.md "How to run tests": full-suite invocations
(`uv run pytest backend/tests/ --timeout=15`) must run in **Bash
background** and be polled — never blocking the foreground tool call.

### Level 4: Manual Validation

```bash
# Phase-1 smoke: replay the Swan seed under Cerebras.
AGNES_LLM_PROVIDER=cerebras CEREBRAS_API_KEY=$CEREBRAS_API_KEY \
    uv run python backend/scripts/replay_swan_seed.py

# Confirm provider attribution.
sqlite3 data/audit.db <<'SQL'
  SELECT provider, model, COUNT(*) AS calls,
         SUM(cost_micro_usd) AS micro_usd_total
  FROM agent_costs
  GROUP BY provider, model
  ORDER BY calls DESC;
SQL

# Confirm decision-trace shape: response_id present, latency_ms set.
sqlite3 data/audit.db <<'SQL'
  SELECT runner, model, response_id, latency_ms, finish_reason
  FROM agent_decisions
  WHERE runner = 'pydantic_ai'
  ORDER BY id DESC LIMIT 5;
SQL

# Per-employee rollup must still work end-to-end (the demo wedge).
sqlite3 data/audit.db <<'SQL'
  SELECT employee_id, provider,
         SUM(cost_micro_usd) AS micro_usd
  FROM agent_costs
  WHERE created_at >= date('now', '-30 days')
  GROUP BY employee_id, provider;
SQL
```

### Level 5: Additional Validation (Optional)

- Compare wall-clock latency: time `replay_swan_seed.py` under both
  providers. Cerebras should land at <1.5s per agent step on
  gpt-oss-120b (ref §4); Sonnet at ~250–600ms. If Cerebras is slower,
  investigate (likely first-hit cold queue on free tier — check key
  tier).

---

## ACCEPTANCE CRITERIA

- [ ] `PydanticAiRunner._run_impl()` is live, no longer raises
  `NotImplementedError`.
- [ ] `cerebras_impl.py` exposes three pure helpers
  (`translate_tool_schema`, `translate_tool_choice`,
  `parse_response`) with unit tests covering schema translation,
  `additionalProperties:false` recursive injection, response
  extraction, tool-name-mismatch handling, JSON-arg parse failure,
  reasoning-token capture.
- [ ] `cost.py` `micro_usd` charges `reasoning_tokens` at the output
  rate; existing Anthropic rows unaffected (always 0 reasoning).
- [ ] `default_runner()` exists in `registries.py` and reads
  `AGNES_LLM_PROVIDER` at call time.
- [ ] `anomaly_flag_agent.py`, `gl_account_classifier_agent.py`, and
  `counterparty_classifier.py` route via `default_runner()` with the
  appropriate per-provider model string.
- [ ] `document_extractor.py` is unchanged (still hard-coded
  `"anthropic"`).
- [ ] An end-to-end Swan replay under `AGNES_LLM_PROVIDER=cerebras`
  produces `agent_decisions` rows with `runner='pydantic_ai'`,
  `model='gpt-oss-120b'`, `response_id` non-null, `latency_ms` >0,
  AND matching `agent_costs` rows with `provider='cerebras'`,
  `cost_micro_usd >0`.
- [ ] Per-employee cost rollup query (Level 4 above) returns one row
  per `(employee_id, provider)` pair with non-zero `micro_usd` for
  the Cerebras pair.
- [ ] Full pytest suite green under default provider; targeted suite
  green under `AGNES_LLM_PROVIDER=cerebras` with a live key.
- [ ] `README.md` and `CLAUDE.md` reflect the live runner state and
  the new env vars.
- [ ] `.env.example` includes `CEREBRAS_API_KEY` and
  `AGNES_LLM_PROVIDER`.

---

## COMPLETION CHECKLIST

- [ ] Tasks 1–17 completed in order.
- [ ] Each task validation command passed immediately after that
  task — no batched validation.
- [ ] Phase-1 gate (step 11) passed before step 12 began.
- [ ] Full test suite passes (background mode, per CLAUDE.md).
- [ ] No new linting / type errors (project does not yet enforce —
  no-op for now, but check whatever local tools are configured).
- [ ] Manual replay-seed validation confirms `provider='cerebras'`
  rows in `agent_costs`.
- [ ] README + CLAUDE.md updated in the same commit as code.
- [ ] PR description cites RealMetaPRD §6.4, §7.4, §7.7, §7.10 and
  CEREBRAS_STACK_REFERENCE §5, §7, §9, §13 (per repo convention).

---

## NOTES

### Design decisions + rationale

1. **Raw `AsyncOpenAI` over Pydantic AI for `_run_impl()`.**
   Pydantic AI is a sound framework but our agents already use the
   `submit_*` pattern with hand-built tool dicts (not Pydantic
   `BaseModel` `output_type`s). Wrapping a Pydantic AI `Agent` would
   require either (a) rewriting the agents to declare typed outputs,
   or (b) shoehorning dict tools through `@agent.tool` decorators —
   both add code that earns nothing the OpenAI-compat path doesn't
   already provide. Cerebras §13 documents the raw path as
   architecturally equivalent and lower-overhead. The `pydantic_ai`
   registry name is a misnomer post-implementation; we keep it for
   compat with the executor's provider mapping (`pydantic_ai →
   cerebras`). A renaming PR is a follow-up if anyone cares.

2. **Lazy `_get_client()` over boot-time `models.list()` check.**
   A boot-time check breaks the test suite for anyone without a
   Cerebras key, which would block cold-start dev. Lazy first-call
   construction surfaces auth failures clearly (the OpenAI SDK
   raises `AuthenticationError` with model name + endpoint), and
   the runner's exception envelope catches it as
   `finish_reason="error:AuthenticationError"`. Operators see this
   in the audit row.

3. **Keep `pydantic_ai` registry name; do not add a `cerebras` key.**
   Adding a `cerebras` runner key alongside `pydantic_ai` would
   require also touching the executor's provider mapping
   (`executor.py:278–285`) and every place the registry name leaks
   (audit rows, tests, docs). Net: more diff, no semantic gain.
   Document the misnomer in a one-line module-docstring comment.

4. **Migrate `anomaly_flag_agent` first (off the SLA path).**
   Anomaly flagging runs in batch reporting pipelines, not on the
   <5s Swan webhook. Mistakes here surface in slower-feedback paths
   and roll back without UX impact. Counterparty + GL classifiers,
   in contrast, are on the hot path; bugs there delay JE posts. The
   staging is conservative-first by design.

5. **Reasoning-token cost basis fix in step 14, not step 3.**
   This is a deliberate red-green sequence: step 5's test for
   `cost.py` is written assuming reasoning tokens count at the
   output rate. Step 3 leaves `cost.py` unchanged. Step 14 adds the
   fix. The test fails between step 5 and step 14, then goes green —
   proving the test catches the bug.

### Risks + mitigations

- **Cerebras free-tier silently caps context to 8k regardless of
  model max.** The api key shared in the conversation triggers this
  unless it's a paid tier. Mitigation: confirm tier with `curl
  https://api.cerebras.ai/v1/models -H "Authorization: Bearer
  $CEREBRAS_API_KEY"` and inspect rate-limit headers; if
  free-tier, upgrade before Phase-2 SLA work (~$10 Developer tier
  per Cerebras §4).
- **Tool-arg constrained decoding may reject our schemas if any
  branch has a forbidden form** (regex `pattern`, `format`,
  recursive `$ref`, `minItems`/`maxItems`). Mitigation: the helper
  tests (step 5) exercise all three target agents' schemas
  directly. If Cerebras rejects at runtime, the runner returns
  `finish_reason="error:..."` and `cerebras_impl.py` becomes the
  one place to add a sanitizer.
- **Confidence calibration drift** between Sonnet (typically
  reports 0.85–0.97 on closed-list picks) and gpt-oss-120b
  (unknown distribution on this codebase). Mitigation: step-11
  Phase-1 gate snapshots confidences from 10+ Cerebras runs before
  step 12 begins; if the median drops below the 0.50 floor too
  often, recalibrate the gate before migrating SLA-path agents.
- **Cerebras EU data-residency: US-only inference as of April 2026
  (ref §16).** HEC Paris is in-scope for GDPR. Mitigation: this
  PR is for the demo wedge; the production rollout decision is
  out of scope and should be flagged in the PR description.
- **Free-tier rate limits silently cap throughput at peak hours.**
  Mitigation: a Developer-tier key for any demo. Add the tier
  expectation to CLAUDE.md.

### Out of scope (deliberate, do not expand)

- Cerebras-backed report narration in `report_renderer.py`.
- Migration of `document_extractor.py` (vision; no Cerebras
  multimodal model).
- A full ADK runner implementation (separate `adk` registry key
  exists; that's a different effort).
- Renaming the `pydantic_ai` runner key to `cerebras`.
- Multi-model fallback layering (`FallbackLLM` from ref §13). Useful
  but adds a moving part the demo doesn't need.
- Streaming support in the runner (`agent.run_stream`). Our agents
  are single-shot `submit_*` calls; streaming is for net-new writer
  agents.
