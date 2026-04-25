# Cerebras Stack Reference — Autonomous CFO MVP

**Status:** v1.0 — 2026-04-25
**Audience:** future Claude sessions implementing the Phase 1 meta-layer foundation on top of Cerebras inference. Self-contained: do not assume prior context beyond the MetaPRD.
**Companion doc:** `ANTHROPIC_SDK_STACK_REFERENCE.md` (separate, non-overlapping reference for the Anthropic-SDK-only path).
**Scope:** This doc covers **Cerebras only**. It does not mix in or compare against Claude/Anthropic. If a session is implementing the Anthropic path, use the companion doc instead.

---

## 0. Project context (read before writing code)

The Autonomous CFO MVP (`Orchestration/PRDs/MetaPRD.md`) imposes constraints that override generic best-practice tutorials:

- **Sub-5s end-to-end** from Swan webhook to GL post + UI push.
- **Deterministic-first.** Rules, identifier matches, and cache hits before any LLM call. AI never does arithmetic, never produces journal entries directly.
- **Decision trace is non-negotiable.** Every AI write produces a row in `decision_traces` (model, prompt_hash, alternatives, confidence, approver_id) — joined to `journal_lines`. Not a JSON sidecar.
- **YAML pipeline DSL.** Pipelines are declarative DAGs; a thin async executor (`api/dag_executor.py`) runs them. Adding a new event type is YAML + one tool registry line.
- **Two SQLite databases.** `accounting.db` (domain, GL, traces) + `orchestration.db` (run history, append-only).
- **Integer cents.** No floats on money paths.
- **Per-employee budgets + AI-credit cost tracking is the demo wedge** (`memory/project_pitch_direction.md`). Every Cerebras call must attribute cost to the calling employee/agent.

Key implication: **the executor is yours, not Cerebras's, not Pydantic AI's, not ADK's.** Cerebras is the model provider. Whichever orchestration framework you pick (or skip), it lives **inside a single DAG node**, never around the DAG.

---

## 1. Why Cerebras

Cerebras Inference (`https://api.cerebras.ai/v1`) runs open-weight models at **20× the throughput of frontier APIs**. For a 5-hop tool-calling agent loop this is the difference between a 16s wall-clock and a 1s wall-clock — i.e., the difference between "doesn't fit the SLA" and "fits with 4s of headroom for SQL, retrieval, and Pydantic validation."

**Worked example — 5-hop loop, ~1,250 generated tokens total:**

| Provider | Throughput | Generation | + 5× ~80ms RTT | Wall clock |
|---|---|---|---|---|
| GPT-4 class (~80 tok/s) | 80 tok/s | ~15.7s | +0.4s | **~16s** |
| Cerebras Llama 3.3 70B | ~2,100 tok/s | ~0.6s | +0.4s | **~1.0s** |

**Practical consequence:** a counterparty/GL classifier emitting 50–150 output tokens completes in ~50–100ms on Cerebras. You can run 8–12 classifications in the time GPT-4 takes to run one. This is what makes per-row inference economically and temporally viable for batch reprocessing.

---

## 2. SDK setup

### Install

```bash
pip install --upgrade cerebras_cloud_sdk
# Optional aiohttp backend for higher concurrency:
pip install "cerebras_cloud_sdk[aiohttp]"
```

Python 3.9+ (the project's 3.10+ target is fine).

### Auth + clients

```bash
export CEREBRAS_API_KEY="csk-..."
```

```python
import os
from cerebras.cloud.sdk import Cerebras, AsyncCerebras

client  = Cerebras(api_key=os.environ["CEREBRAS_API_KEY"])
aclient = AsyncCerebras(api_key=os.environ["CEREBRAS_API_KEY"], timeout=4.0, max_retries=2)
```

For FastAPI handlers always use `AsyncCerebras`. **Set `timeout=4.0`** so a hanging call fails fast inside the 5s budget. The default 60s timeout is wrong for this project.

### OpenAI-compatible endpoint (alternative)

Cerebras exposes an OpenAI-compatible API at `https://api.cerebras.ai/v1`. Point the OpenAI Python SDK at it when you want to share code with Pydantic AI or Google ADK:

```python
from openai import AsyncOpenAI
client = AsyncOpenAI(
    base_url="https://api.cerebras.ai/v1",
    api_key=os.environ["CEREBRAS_API_KEY"],
    timeout=4.0,
)
```

**Compatibility notes:**
- Works: `chat.completions.create`, `models.list`, `temperature`, `top_p`, `max_completion_tokens`, `stream`, `tools`, `tool_choice`, `parallel_tool_calls`, `response_format`, `seed`, `stop`, `reasoning_effort`.
- Doesn't: embeddings, audio, images, fine-tuning, assistants, files, vector stores.
- Cerebras-only params (`prompt_cache_key`, `service_tier`, `clear_thinking`) go via `extra_body={...}` when using the OpenAI client.

---

## 3. Models (April 2026)

| Model ID | Class | Context (paid / free) | Output cap | TPS | Status |
|---|---|---|---|---|---|
| `llama3.3-70b` | Meta Llama 3.3 70B | 128k / 8k | ~8k | ~2,100 | **Recommended for classifiers** |
| `gpt-oss-120b` | OpenAI open-weight 120B (reasoning) | 131k / 65k | 40k / 32k | ~3,000 | **Recommended for schema-strict + writer** |
| `qwen-3-235b-a22b-instruct-2507` | Alibaba Qwen 3 235B MoE | 131k / 64k | ~32k | ~1,400 | Preview, **deprecating 2026-05-27** |
| `zai-glm-4.7` | Z.ai GLM 4.7 (~355B reasoning) | 128k / 8k | — | ~990 | Preview |
| `llama3.1-8b` | Meta Llama 3.1 8B | 128k / 8k | ~8k | ~2,170 | **Deprecating 2026-05-27** |

`client.models.list()` returns the canonical live list — call it at boot to verify availability before pinning.

**Pinning policy for this project:**
- Default: `gpt-oss-120b` for any node that uses `response_format` with `strict: true`. It's the most reliable schema follower on Cerebras.
- Speed-first classifier: `llama3.3-70b` when the node only needs short structured JSON via the submit-tool pattern.
- **Do not build on** `qwen-3-235b-a22b-instruct-2507` or `llama3.1-8b` — both deprecate 2026-05-27. The demo cycle ends before then but the post-hackathon roadmap (Phases 2–4) does not.

**Honest "what Cerebras isn't great at yet":**
- Multi-step symbolic reasoning / arithmetic. Route accruals, FX revaluation, and totals to deterministic Python.
- Long-horizon planning (>10 steps). The Phase 3 `PlannerAgent` may need Claude/GPT for novel plans; Cerebras drafts, frontier validates.
- Vision. No multimodal inference. Receipt OCR must use a different provider (out of scope for MVP per the PRD).
- Native confidence scores. Use the submit-tool pattern (§7) to elicit confidence as a schema field.

---

## 4. Inference characteristics

### Throughput (Artificial Analysis benchmarks, April 2026)

- `gpt-oss-120b` (low-effort): ~1,855 tok/s, headline ~3,000 tok/s in bursts
- `llama3.3-70b`: ~2,100 tok/s
- `qwen-3-235b`: ~1,400 tok/s
- `zai-glm-4.7`: ~990 tok/s

### Time-to-first-token

- `gpt-oss-120b` (low effort): ~0.49s
- `zai-glm-4.7`: ~0.54s

This is the headline win. Frontier APIs sit at 1–3s TTFT. For a 500-token structured response from `gpt-oss-120b`, expect ~0.7–1.0s end-to-end including TTFT.

### Where Cerebras loses

- **Long prompts** (10k+ tokens): prompt-time encoding erodes the TTFT lead. Mitigation: lean prompts; `prompt_cache_key` on stable prefixes (request enablement via support).
- **Cold queues at peak hours on free tier.**
- **Reasoning models with `reasoning_effort=high`** can spend many seconds in hidden thinking tokens — keep this off the hot path.

### Rate limits (indicative)

| Tier | RPM (gpt-oss-120b) | TPM | TPD |
|---|---|---|---|
| Free | 30 | 64k | 1M |
| PAYG | 1,000 | 1M | unlimited |
| Developer ($10+) | ~10× free | ~10× | unlimited |
| Enterprise | dedicated queues | custom | custom |

For the hackathon demo a Developer tier key is the safe default. Free tier silently caps context to 8k regardless of the model's underlying max — verify with a paid key before shipping.

---

## 5. Tool calling (function calling)

OpenAI-compatible. Officially supported with high reliability on `gpt-oss-120b` and `zai-glm-4.7`. `llama3.3-70b` accepts `tools` but is more prone to fabricating tool names — always validate `tool_calls[].function.name` against your registry before dispatch.

**Schema:** standard `{type: "function", function: {name, description, parameters, strict}}`. With `strict: true`, Cerebras applies token-level constrained decoding — argument JSON is guaranteed to match the schema.

**Strict-mode requirements:**
- `additionalProperties: false` at every object level.
- Root must be `type: "object"`.
- No regex `pattern`, no string `format` (`email`/`date-time`/`uuid`), no array `minItems`/`maxItems`, no recursive schemas, no external `$ref`.
- ≤5,000 schema chars, ≤10 nesting levels, ≤500 properties per object, ≤500 enum values.

**Critical constraint:** `tools` and `response_format` **cannot be combined in the same request**. If you need both, do it in two passes: pass 1 calls a tool to gather data, pass 2 emits the final structured object. For the CFO project, prefer the **submit-tool pattern** (§7) which folds structured output into the tool-calling loop.

**Parallel tool calls:** `parallel_tool_calls=True` by default. Disable for serialized side effects — e.g., a node that posts a journal entry and decrements a budget envelope must serialize them.

**Streaming with tools:** supported. Tool-call deltas arrive in `chunk.choices[0].delta.tool_calls[i].function.arguments`. You must accumulate `arguments` deltas by `index` until the chunk with `finish_reason="tool_calls"` arrives, then JSON-parse the assembled string.

**Example — minimal tool definition:**

```python
tools = [{
    "type": "function",
    "function": {
        "name": "post_journal_entry",
        "strict": True,
        "description": "Insert one balanced journal entry into the GL. Caller asserts SUM(debits)=SUM(credits).",
        "parameters": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "transaction_id":    {"type": "string"},
                "counterparty_id":   {"type": "integer"},
                "lines": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {
                            "account":      {"type": "string"},  # PCG code
                            "debit_cents":  {"type": "integer", "minimum": 0},
                            "credit_cents": {"type": "integer", "minimum": 0},
                            "description": {"type": "string"},
                        },
                        "required": ["account", "debit_cents", "credit_cents", "description"],
                    },
                },
            },
            "required": ["transaction_id", "counterparty_id", "lines"],
        },
    },
}]
```

The model can only emit a syntactically valid call; **semantic validation (debit/credit balance, account-code allowlist) is your job** in the tool implementation. Cerebras strict mode does not guarantee semantics.

---

## 6. Structured output

```python
response_format = {
    "type": "json_schema",
    "json_schema": {
        "name": "gl_classification",
        "strict": True,
        "schema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "gl_account":      {"type": "string"},          # validate regex client-side
                "counterparty_id": {"type": "string"},
                "confidence":      {"type": "number"},          # validate [0,1] client-side
                "rationale":       {"type": "string"},
                "alternatives":    {"type": "array",
                                    "items": {"type": "string"}},
            },
            "required": ["gl_account", "counterparty_id", "confidence", "rationale"],
        },
    },
}
```

Then validate with Pydantic to catch the things `strict` doesn't enforce:

```python
from pydantic import BaseModel, Field, ValidationError

class GLClassification(BaseModel):
    gl_account: str = Field(pattern=r"^[0-9]{6}$")
    counterparty_id: str
    confidence: float = Field(ge=0.0, le=1.0)
    rationale: str = Field(max_length=240)
    alternatives: list[str] = []
    model_config = {"extra": "forbid"}

try:
    parsed = GLClassification.model_validate_json(resp.choices[0].message.content)
except ValidationError as e:
    # Retry with corrective system message, OR fall back to deterministic rule, OR queue for review.
    ...
```

**Schema correctness ≠ semantic correctness.** Always wrap in Pydantic and treat validation failure as a normal flow (retry once, then route to `needs_review`).

**Loose JSON mode** (`response_format={"type": "json_object"}`) skips constrained decoding and is unsafe for ledger-bound data. Don't use it.

---

## 7. The submit-tool pattern (preferred for this project)

Because `tools` and `response_format` cannot coexist, and because the project needs both tool calls (DB lookups, Swan re-query) AND a structured final result with confidence — define a single mandatory `submit_*` tool and force the model to call it once at the end of its loop.

```python
submit_tool = {
    "type": "function",
    "function": {
        "name": "submit_classification",
        "strict": True,
        "description": "Final structured submission. Call exactly once.",
        "parameters": GLClassification.model_json_schema() | {"additionalProperties": False},
    },
}

resp = await aclient.chat.completions.create(
    model="gpt-oss-120b",
    messages=messages,
    tools=[swan_query_tool, counterparty_lookup_tool, submit_tool],
    tool_choice={"type": "function", "function": {"name": "submit_classification"}},
    temperature=0.0,
    max_completion_tokens=600,
)
```

**Why this works:** the model can still call other tools inside the loop, but the final turn must produce a `submit_classification` call whose arguments are constrained-decoded against the schema. This gives you tool-calling AND schema enforcement AND a `confidence` field — without two API calls.

**Confidence handling:** the model's self-reported `confidence` is an **ordinal signal**, not a probability. Use it for thresholding (`>= floor` → auto-post, `< floor` → review queue), never report it to a user as "95% safe." For borderline cases run a small temperature-ensemble (3 calls at temp 0.0/0.3/0.5) and vote — the variance itself is a stronger signal than any single confidence.

---

## 8. Streaming to FastAPI SSE

Standard SSE (`text/event-stream`), one `data: {...json...}` per chunk, terminated by `data: [DONE]`. Final chunk includes `usage` and `time_info: {queue_time, prompt_time, completion_time, total_time}`.

```python
from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse
import json

app = FastAPI()
aclient = AsyncCerebras(timeout=4.0)

@app.get("/agent/stream")
async def stream(query: str, request: Request):
    async def gen():
        stream = await aclient.chat.completions.create(
            model="gpt-oss-120b",
            messages=[{"role": "user", "content": query}],
            stream=True,
            max_completion_tokens=600,
        )
        async for chunk in stream:
            if await request.is_disconnected():
                break  # critical — do not keep generating into the void
            delta = chunk.choices[0].delta
            if delta.content:
                yield f"data: {json.dumps({'type':'text','content':delta.content})}\n\n"
            if delta.tool_calls:
                for tc in delta.tool_calls:
                    yield f"data: {json.dumps({'type':'tool_delta','index':tc.index,'name':tc.function.name,'args_delta':tc.function.arguments})}\n\n"
            if chunk.usage:
                yield f"data: {json.dumps({'type':'usage','usage':chunk.usage.model_dump()})}\n\n"
        yield "data: [DONE]\n\n"
    return StreamingResponse(gen(), media_type="text/event-stream")
```

**Always** check `request.is_disconnected()` inside the generator. A disconnected client + 5K-token generation = wasted spend and stuck workers — and on Cerebras's free tier you'll hit the daily TPD cap on the next demo.

---

## 9. Cost & token accounting

### Pricing (April 2026, $/1M tokens)

| Model | Input | Output |
|---|---|---|
| `llama3.1-8b` | $0.10 | $0.10 |
| `llama3.3-70b` | $0.60 | $0.60 |
| `gpt-oss-120b` | $0.35 | $0.75 |
| `qwen-3-235b` | $0.60 | $1.20 |
| `zai-glm-4.7` | $2.25 | $2.75 |

### Usage extraction

```python
u = resp.usage
# .prompt_tokens, .completion_tokens, .total_tokens
# .prompt_tokens_details.cached_tokens          — cached prefix (no discount on Cerebras)
# .completion_tokens_details.reasoning_tokens   — billed as completion, even when hidden
```

**Reasoning tokens are charged.** `reasoning_format: "hidden"` doesn't make them free — it just hides them from the user. Track them separately so the per-employee budget reflects the real cost.

**Streaming:** `usage` only appears in the **final chunk** — accumulate it there.

### Per-employee cost ledger (the demo wedge)

```python
def cost_usd(model: str, usage) -> float:
    rates = {
        "llama3.3-70b":   (0.60, 0.60),
        "gpt-oss-120b":   (0.35, 0.75),
        "qwen-3-235b-a22b-instruct-2507": (0.60, 1.20),
        "zai-glm-4.7":    (2.25, 2.75),
    }
    inp, out = rates[model]
    return (usage.prompt_tokens * inp + usage.completion_tokens * out) / 1_000_000
```

Insert into `decision_traces.cost_usd` (and into the `ai_credit_ledger` table for Phase 3's "AI cost as first-class supplier in GL" feature) on every call. This is what makes the per-employee-budget pitch real, not narrative.

### Prompt caching

Cerebras automatic prompt caching (Oct 2025+) caches in 128-token blocks for 5min–1h, scoped to your org. Supported on `gpt-oss-120b`, `qwen-3-235b`, `zai-glm-4.7`. **Cached tokens cost the same as fresh input tokens** — caching saves *latency*, not money. Use `prompt_cache_key` for related requests; place the system prompt and tool schemas at the message head so the prefix matches.

Because raw generation is so fast on Cerebras, even a full re-encode of a 2K-token system prompt costs ~80ms — cheaper than building elaborate caching layers. Don't over-engineer this.

---

## 10. Decision trace fields (what to capture per call)

This maps directly to the `decision_traces` table in `accounting.db`:

```sql
-- Already defined in MetaPRD §7.2; this is the Cerebras-specific population pattern.
INSERT INTO decision_traces (
    line_id, source, agent_run_id, model, prompt_hash,
    alternatives, confidence, parent_event_id
) VALUES (?, 'agent', ?, ?, ?, ?, ?, ?);
```

Per Cerebras call, capture:

| Field | Source |
|---|---|
| `model` | request `model` arg, e.g. `"gpt-oss-120b"` |
| `response_id` | `resp.id` (Cerebras returns OpenAI-style `chatcmpl-...`) |
| `prompt_hash` | `sha256(canonical_messages + tools_schema + model)` — canonicalize before hashing |
| `usage.prompt_tokens` / `completion_tokens` / `cached_tokens` | `resp.usage.*` |
| `latency_ms` | `monotonic()` around the `await client.chat.completions.create(...)` call |
| `finish_reason` | `resp.choices[0].finish_reason` (`stop` \| `tool_calls` \| `length`) |
| `cost_usd` | computed from `usage` + pricing table (§9) |
| `confidence` | from the `submit_*` tool's `confidence` field |
| `alternatives` | from the `submit_*` tool's `alternatives` field (JSON-encoded) |
| `temperature`, `seed` | request args (for replay) |

`response_id` is the auditor's primary key when reconciling against Cerebras-side logs.

---

## 11. Orchestration option A — Pydantic AI (recommended)

Pydantic AI is a Python agent framework from the Pydantic team. **It is the right fit for this project** because:
- Built-in first-class Cerebras provider (the Cerebras team explicitly recommends Pydantic AI as a partner integration).
- Type-driven structured output via Pydantic `BaseModel` as `output_type`.
- Lightweight enough to live inside one DAG node — does not duplicate the YAML executor.
- Stateless agents map cleanly to the project's "pipelines are data, not code" principle.
- `result.all_messages()` + `result.usage()` give a clean, typed input to the `decision_traces` table.

### Install

```bash
pip install "pydantic-ai-slim[cerebras]"
export CEREBRAS_API_KEY=csk-...
```

Pydantic v2 only. v1 `BaseModel` will silently misbehave or fail schema generation.

### Minimal agent with Cerebras + typed output

```python
from pydantic import BaseModel, Field
from pydantic_ai import Agent

class GLClassification(BaseModel):
    gl_account: str = Field(pattern=r"^[0-9]{6}$")
    counterparty_id: str
    confidence: float = Field(ge=0.0, le=1.0)
    rationale: str = Field(max_length=240)
    alternatives: list[str] = []

agent = Agent(
    'cerebras:gpt-oss-120b',
    output_type=GLClassification,
    model_settings={'temperature': 0.0},
    system_prompt=(
        "Classify the counterparty and assign a French PCG GL account code. "
        "Always include alternatives and a self-reported confidence in [0,1]."
    ),
)

result = await agent.run("Acme SAS, IBAN FR76..., €4,950 inbound SEPA")
result.output       # validated GLClassification
result.usage()      # RunUsage(input_tokens=..., output_tokens=..., requests=N)
result.all_messages()  # full message log — persist to decision_traces
```

### Agent with tool + DB deps

```python
from dataclasses import dataclass
import aiosqlite
from pydantic_ai import Agent, RunContext, ModelRetry

@dataclass
class CFODeps:
    db: aiosqlite.Connection
    counterparty_iban: str | None
    swan_event_id: str

agent = Agent('cerebras:llama3.3-70b', deps_type=CFODeps, output_type=GLClassification)

@agent.tool
async def lookup_counterparty(ctx: RunContext[CFODeps]) -> dict | None:
    """Return the canonical counterparty record for the current event's IBAN, if any."""
    if not ctx.deps.counterparty_iban:
        return None
    cur = await ctx.deps.db.execute(
        "SELECT id, legal_name, vat_number FROM counterparties c "
        "JOIN counterparty_identifiers ci ON ci.counterparty_id = c.id "
        "WHERE ci.identifier_type='iban' AND ci.identifier=?",
        (ctx.deps.counterparty_iban,),
    )
    row = await cur.fetchone()
    return None if row is None else {"id": row[0], "legal_name": row[1], "vat_number": row[2]}
```

### Invoking from a DAG node (the integration seam)

The MetaPRD's executor (`api/dag_executor.py`) calls async `run(ctx) -> dict` per node. A Pydantic AI agent slots in as one node:

```python
# tools/gl_account_classifier.py
from agents.gl_classifier import agent, CFODeps
import json, hashlib, time, datetime

async def run(ctx) -> dict:
    deps = CFODeps(
        db=ctx.accounting_db,
        counterparty_iban=ctx.node_outputs["fetch-transaction"]["counterparty_iban"],
        swan_event_id=ctx.trigger_payload["event_id"],
    )
    started = time.monotonic()
    result = await agent.run(
        ctx.node_outputs["fetch-transaction"]["memo"],
        deps=deps,
    )
    latency_ms = int((time.monotonic() - started) * 1000)

    # decision trace (one row per LLM call inside the agent run)
    u = result.usage()
    canonical = json.dumps([m.model_dump() for m in result.all_messages()], sort_keys=True)
    prompt_hash = hashlib.sha256(canonical.encode()).hexdigest()[:16]

    await ctx.accounting_db.execute(
        """INSERT INTO decision_traces
           (source, agent_run_id, model, prompt_hash, alternatives,
            confidence, parent_event_id)
           VALUES ('agent', ?, ?, ?, ?, ?, ?)""",
        (ctx.run_id, "cerebras:gpt-oss-120b", prompt_hash,
         json.dumps(result.output.alternatives), result.output.confidence,
         ctx.trigger_payload["event_id"]),
    )

    return {
        "gl_account": result.output.gl_account,
        "counterparty_id": result.output.counterparty_id,
        "confidence": result.output.confidence,
        "_trace": {
            "model": "cerebras:gpt-oss-120b",
            "input_tokens": u.input_tokens,
            "output_tokens": u.output_tokens,
            "requests": u.requests,
            "latency_ms": latency_ms,
        },
    }
```

### Output validators for invariant enforcement

```python
@agent.output_validator
async def must_be_known_counterparty(ctx: RunContext[CFODeps], out: GLClassification) -> GLClassification:
    if out.confidence >= 0.9 and out.counterparty_id == "UNKNOWN":
        raise ModelRetry("High confidence but counterparty_id is UNKNOWN. Reconcile.")
    return out
```

`ModelRetry` pushes a corrective message back into the agent loop. Bound retries via `@agent.tool(retries=2)` or model-level `request_limit`.

### Key Pydantic AI ↔ Cerebras caveats

1. **Streaming + structured output don't mix well on Cerebras.** Use `agent.run()` for typed results; `agent.run_stream()` for plain-text writer agents only.
2. **For schema-heavy nodes prefer `gpt-oss-120b`** — Cerebras flags it as the most reliable JSON-schema follower.
3. **Tool docstrings are part of the contract.** Cerebras tool calling is more sensitive than OpenAI's — terse docstrings produce wrong-arg tool calls.
4. **Reasoning models** (`zai-glm-4.7`) need `cerebras_disable_reasoning=True` in `CerebrasModelSettings` for non-reasoning latency.
5. **Model strings use a colon, not a slash:** `'cerebras:gpt-oss-120b'`, not `'cerebras/gpt-oss-120b'`.
6. **`deps_type` is a *type*, not an instance.** Instances go to `agent.run(..., deps=instance)`.
7. **Agents are stateless.** Pass `message_history=...` explicitly when continuing a thread. Good for audit, trips up devs from chat-app frameworks.
8. **`run_sync` inside FastAPI raises.** Always `await agent.run(...)` from async handlers.
9. **`UsageLimits(request_limit=N, total_tokens_limit=M)`** is the safety cap per request. Set it. It won't refund spent tokens, but it short-circuits further calls.
10. **`ALLOW_MODEL_REQUESTS=0`** in `conftest.py` is how you keep tests from leaking real LLM calls.

### Testing — TestModel / FunctionModel

```python
from pydantic_ai.models.test import TestModel
from pydantic_ai.models.function import FunctionModel
from pydantic_ai.messages import ModelResponse, TextPart

def test_classifier_path(agent):
    def behavior(messages, info):
        return ModelResponse(parts=[TextPart(
            '{"gl_account":"706000","counterparty_id":"42",'
            '"confidence":0.95,"rationale":"IBAN match","alternatives":[]}'
        )])
    with agent.override(model=FunctionModel(behavior)):
        result = agent.run_sync("test", deps=fake_deps)
    assert result.output.gl_account == "706000"
```

### Pydantic-graph: do not use for the YAML DAG

Pydantic AI ships `pydantic-graph` for typed graph workflows. **It is not the right tool for this project's YAML DAG** — graphs are statically-typed Python `BaseNode` subclasses with edges inferred from return-type annotations. There is no YAML loader, and shoehorning YAML into it loses the type safety that justifies using it. Keep your home-grown YAML executor; use Pydantic AI agents as leaf nodes.

---

## 12. Orchestration option B — Google ADK + LiteLLM (alternative)

**Verdict for this project: not recommended.** Documented here for completeness — read this section, then go back to option A or C.

Google ADK (`google-adk` on PyPI, v1.31.x as of April 2026) is an agent framework with first-class Gemini support and a LiteLLM bridge for non-Google models, including Cerebras. It works, but for the Autonomous CFO it has more friction than payoff:

| Pain point | Detail |
|---|---|
| Workflow engine duplicates the YAML DAG | ADK's `SequentialAgent`/`ParallelAgent`/`LoopAgent` is its own orchestration layer. You'd run the project's YAML executor *or* ADK, not both — and ADK's executor is a Python tree, not a YAML interpreter. |
| `output_schema` is broken on non-Gemini via `LiteLlm` | Issue [#217](https://github.com/google/adk-python/issues/217). Structured-output schemas don't reliably forward — you must validate Pydantic-style downstream anyway. |
| LiteLLM supply-chain history | LiteLLM 1.82.7–1.82.8 contained unauthorized code; ADK pinned to 1.82.6 in v1.28.0. You inherit this dependency-hygiene burden. |
| Bi-weekly minor releases + recent CVEs | RCE fixes in v1.30 and v1.31. Pin exact versions for the hackathon. |
| Annotated `Field(description=...)` on tool params is silently ignored | Issue [#4552](https://github.com/google/adk-python/issues/4552). Put descriptions in docstrings. |
| Streaming + multi-part responses can `UniqueViolation` on the events table | Issue [#297](https://github.com/google/adk-python/issues/297). |
| Cerebras `parallel_tool_calls` not officially documented through LiteLLM | Assume single-tool-per-step; orchestrate fan-out at your DAG level. |

### When it might be worth it

Two reasons to choose ADK over Pydantic AI:
1. You expect to migrate to Vertex/Gemini post-MVP and want the same code path.
2. You want the `adk web` dev UI as your demo surface.

For the hackathon, neither outweighs the friction.

### Setup if you go ahead

```bash
pip install google-adk litellm  # pin both versions exactly
export CEREBRAS_API_KEY=...
```

```python
from google.adk.agents import LlmAgent, SequentialAgent
from google.adk.models.lite_llm import LiteLlm
from google.adk.tools import FunctionTool

def get_remaining_budget(employee_id: str, category: str) -> dict:
    """Return remaining quarterly budget for an employee in a category.

    Args:
        employee_id: The employee's stable ID, e.g. 'E-1042'.
        category: Spend category — 'travel', 'software', or 'meals'.
    """
    return {"employee_id": employee_id, "category": category, "remaining_eur": 312.50}

cfo_agent = LlmAgent(
    model=LiteLlm(model="cerebras/gpt-oss-120b"),
    name="cfo_brain",
    instruction="Use get_remaining_budget before approving anything > 50 EUR.",
    tools=[FunctionTool(get_remaining_budget)],
)
```

### Cost capture via callback (the only non-painful integration point)

```python
from google.adk.agents.callback_context import CallbackContext
from google.adk.models import LlmResponse

CEREBRAS_PRICE_PER_1M_IN  = 0.35  # gpt-oss-120b
CEREBRAS_PRICE_PER_1M_OUT = 0.75

def log_usage(ctx: CallbackContext, llm_response: LlmResponse):
    u = llm_response.usage_metadata or {}
    p = u.get("prompt_token_count", 0)
    c = u.get("candidates_token_count", 0)
    cost = (p * CEREBRAS_PRICE_PER_1M_IN + c * CEREBRAS_PRICE_PER_1M_OUT) / 1_000_000
    # insert into decision_traces here

cfo_agent = LlmAgent(
    model=LiteLlm(model="cerebras/gpt-oss-120b"),
    name="cfo_brain",
    after_model_callback=log_usage,
    ...,
)
```

### ADK gotchas (concentrated)

- Pin `litellm==1.82.6` (whatever ADK's `setup.py` declares); rotate `CEREBRAS_API_KEY` if you ever installed 1.82.7 or 1.82.8.
- Use `sqlite+aiosqlite:///./cfo.db`, not `sqlite:///./cfo.db`, with `DatabaseSessionService`.
- `runner.run()` blocks under FastAPI; always `runner.run_async()`.
- State mutations outside a `tool_context` are silently lost when persisting to a DB.
- Don't touch ADK 2.0 alpha (incompatible storage; "should not be used in production").
- Set `GOOGLE_API_KEY=dummy` if any tutorial code reads it at import time even when you only use Cerebras.

---

## 13. Orchestration option C — raw `AsyncCerebras` + the project's own DAG executor

**This is the closest match to what the MetaPRD already designs.** The PRD §7.4 specifies a thin async DAG executor with a tool registry mapping `"ToolClass"` → `"module:function"`. Raw Cerebras client calls inside each tool — no framework — give you total control, lowest overhead, and nothing to learn.

Use this when:
- You want absolute determinism and zero hidden behavior.
- You don't need typed structured output beyond what Pydantic-validating-the-response gives you.
- You want a pluggable provider boundary so Phase 3 can fall back to Claude/GPT without rewriting nodes.

### Provider abstraction (LLMClient protocol)

Hide the provider so pipeline code never imports `cerebras_cloud_sdk` or `openai` directly:

```python
from typing import Protocol, Any
from pydantic import BaseModel
from dataclasses import dataclass

@dataclass
class LLMResult:
    content: str
    parsed: BaseModel | None
    usage: dict           # {prompt_tokens, completion_tokens, ...}
    latency_ms: int
    model: str
    response_id: str
    finish_reason: str

class LLMClient(Protocol):
    async def complete(
        self,
        messages: list[dict],
        *,
        schema: type[BaseModel] | None = None,
        tools: list[dict] | None = None,
        max_tokens: int = 600,
        temperature: float = 0.0,
    ) -> LLMResult: ...
```

### CerebrasLLM implementation

```python
import os, time, json
from openai import AsyncOpenAI
from pydantic import BaseModel, ValidationError

class CerebrasLLM:
    def __init__(self, model: str = "gpt-oss-120b", timeout: float = 4.0):
        self.client = AsyncOpenAI(
            base_url="https://api.cerebras.ai/v1",
            api_key=os.environ["CEREBRAS_API_KEY"],
            timeout=timeout,
        )
        self.model = model

    async def complete(self, messages, *, schema=None, tools=None, max_tokens=600, temperature=0.0):
        kwargs = dict(
            model=self.model,
            messages=messages,
            max_completion_tokens=max_tokens,
            temperature=temperature,
            seed=42,
        )
        if schema is not None and tools is None:
            s = schema.model_json_schema()
            s["additionalProperties"] = False
            kwargs["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": schema.__name__, "strict": True, "schema": s},
            }
        if tools is not None:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"

        started = time.monotonic()
        resp = await self.client.chat.completions.create(**kwargs)
        latency_ms = int((time.monotonic() - started) * 1000)

        content = resp.choices[0].message.content or ""
        parsed = None
        if schema is not None:
            try:
                parsed = schema.model_validate_json(content)
            except ValidationError:
                parsed = None  # caller decides whether to retry/fallback

        return LLMResult(
            content=content,
            parsed=parsed,
            usage=resp.usage.model_dump() if resp.usage else {},
            latency_ms=latency_ms,
            model=self.model,
            response_id=resp.id,
            finish_reason=resp.choices[0].finish_reason,
        )
```

### Agent loop (when tools are needed)

```python
async def run_agent_loop(
    llm: CerebrasLLM,
    messages: list[dict],
    tools: list[dict],
    tool_registry: dict[str, callable],
    max_iterations: int = 6,
    latency_budget_ms: int = 4500,
) -> dict:
    started = time.monotonic()
    for hop in range(max_iterations):
        if (time.monotonic() - started) * 1000 > latency_budget_ms:
            raise TimeoutError(f"Loop exceeded {latency_budget_ms}ms after {hop} hops")

        result = await llm.complete(messages=messages, tools=tools)
        # caller is responsible for inserting a decision_trace row from `result`

        if result.finish_reason != "tool_calls":
            return {"output": result.content, "result": result, "hops": hop + 1}

        msg = json.loads(result.content) if result.content else {}
        # In OpenAI-compatible shape, tool_calls are on the message object — re-fetch:
        # (the openai SDK exposes them as resp.choices[0].message.tool_calls)
        # For brevity: switch to using the openai client directly when tools are involved.
        raise NotImplementedError("Tool-call handling — see openai client return shape.")
```

For the full tool-call loop see the Anthropic SDK reference's §11 (the structure is identical except for message/tool-result shape: Cerebras uses OpenAI-style `{role:"tool", tool_call_id, content}` instead of Anthropic-style `tool_result` blocks).

### Multi-model fallback (Phase 3 hook)

```python
class FallbackLLM:
    def __init__(self, primary: LLMClient, backup: LLMClient, confidence_floor: float = 0.65):
        self.primary, self.backup, self.floor = primary, backup, confidence_floor

    async def complete(self, messages, *, schema=None, **kw):
        try:
            r = await self.primary.complete(messages, schema=schema, **kw)
            if schema is not None and r.parsed is None:
                return await self.backup.complete(messages, schema=schema, **kw)
            if schema is not None and getattr(r.parsed, "confidence", 1.0) < self.floor:
                return await self.backup.complete(messages, schema=schema, **kw)
            return r
        except Exception:
            return await self.backup.complete(messages, schema=schema, **kw)
```

**When NOT to fall back:** for hot-path classification of >100 transactions, falling back to Claude makes the batch take 30s instead of 3s — that defeats the wedge. Mark low-confidence rows for HITL review (which is the demo's whole point) and keep the batch on Cerebras.

---

## 14. Choosing between A, B, and C

| Criterion (high → low importance) | A: Pydantic AI | B: Google ADK + LiteLLM | C: raw `AsyncCerebras` + own DAG |
|---|---|---|---|
| Fits "deterministic-first, YAML DAG" | ✅ leaf-only | ❌ duplicates executor | ✅✅ closest match |
| Decision-trace audit ergonomics | ✅ `result.all_messages()` + `usage()` | ⚠️ OTel + callback glue | ✅ you own everything |
| Cerebras throughput / sub-5s | ✅ direct provider | ⚠️ extra hop via LiteLLM (5–20ms) | ✅ direct |
| Schema reliability on non-Gemini | ✅ Pydantic-validated, retries | ❌ `output_schema` known broken (#217) | ✅ Pydantic-validated, you own retries |
| Tool calling on Cerebras | ✅ direct | ⚠️ via LiteLLM | ✅ direct |
| SQLite session persistence | ➖ DIY | ✅ `DatabaseSessionService` (caveat: aiosqlite URL) | ➖ DIY (already in PRD §7.2) |
| Hackathon time-to-MVP | ✅ fast | ⚠️ medium (fight LiteLLM + version pins) | ✅✅ fastest |
| Risk of upstream breakage in next 2 weeks | low-med | high (bi-weekly minors, recent CVEs) | none |
| "Looks impressive in pitch" | medium | medium-high (Google brand) | low |

**Recommendation:** Use **A (Pydantic AI)** for nodes that benefit from typed output + tool decorators. Use **C (raw)** for nodes that are pure single-shot classifiers via `submit_*` tool, where adding a framework adds zero. Avoid **B (ADK)** for this project unless you're committed to a Vertex migration.

---

## 15. Anti-patterns

1. **Using Cerebras for arithmetic.** Route accruals, FX revaluation, totals to deterministic Python. LLMs classify and narrate; they do not compute.
2. **No `max_completion_tokens` cap.** A runaway hop generating 4,000 tokens turns a 200ms call into 2s and blows the budget. Cap every hop at 600–800.
3. **Holding async streams past SSE disconnect.** Always check `request.is_disconnected()` inside generators.
4. **Treating `strict: true` JSON as guaranteed valid for business rules.** Schema-correct ≠ semantically correct. Pydantic with field validators is mandatory.
5. **Sending different message orderings for same logical state.** Breaks prompt caching prefix match. Canonicalize message construction; hash canonicalized form for `prompt_hash`.
6. **Streaming + tool-calling without delta accumulation.** Tool-arg JSON arrives in fragments; naive parsing fails.
7. **`temperature=0` and assuming byte-equality replay.** Pin seed *and* archive the full raw response. KV-cache layout, batching, and GPU FP nondeterminism all break bit-exactness.
8. **Combining `tools` and `response_format` in one request.** Cerebras rejects this. Use the submit-tool pattern instead.
9. **Echoing client-side tool-call state back to the API.** API v2 (Jan 2026) hard-rejects orphan tool messages and inconsistent `tool_call_id`s with 400 errors. Always rebuild messages from your trusted server-side trace.
10. **Decision trace as a JSON sidecar.** It's a real table with FKs from Day 1 (PRD §7.2 invariant). PRs that insert `journal_lines` without a sibling `decision_traces` insert should fail review.

---

## 16. Pitfalls — Cerebras vs Claude (parity gaps)

A Claude-experienced engineer will trip over these:

| Gap | Claude | Cerebras |
|---|---|---|
| Prompt-cache billing | Cached reads 10% of input | Cached tokens cost full input price; cache saves *latency* only |
| Cache control granularity | `cache_control: {"type": "ephemeral"}` per block | Automatic prefix-only via `prompt_cache_key`; no manual breakpoints |
| System prompt field | Top-level `system` | Must be `messages[0]` with `role:"system"` (OpenAI shape) |
| Tool-result format | `{type:"tool_result", tool_use_id, content}` blocks | OpenAI-style `{role:"tool", tool_call_id, content:"string"}` |
| Refusals | `stop_reason: "refusal"` | No dedicated reason; refusal returns as content text |
| Extended thinking | First-class `thinking` blocks | None natively; emulate via separate "reasoning" call |
| Vision / multimodal | Native | Not available |
| Strict tool args | Best-effort | Constrained decoding — guaranteed shape, breaks if you malform schema |
| Multi-turn validation | Lenient | API v2 hard-rejects orphan tool messages |
| Structured-output schema limits | Generous | 5K chars, 10 levels, 500 props |
| Context | 200K+ | Llama 128K, gpt-oss 128K, Qwen 256K — but throughput drops at high fill |
| Stop sequences | `stop_sequences: [...]` | `stop` (OpenAI shape) |
| Idempotency keys | Yes (beta) | None — implement at your layer for ledger writes |
| EU data residency | Yes | **No — US-only inference as of April 2026.** Flag for HEC Paris GDPR scope |

---

## 17. Sources

- [Cerebras Inference Docs (root)](https://inference-docs.cerebras.ai/)
- [Quickstart](https://inference-docs.cerebras.ai/quickstart)
- [Chat Completions API Reference](https://inference-docs.cerebras.ai/api-reference/chat-completions)
- [Tool Calling capability](https://inference-docs.cerebras.ai/capabilities/tool-use)
- [Structured Outputs](https://inference-docs.cerebras.ai/capabilities/structured-outputs)
- [Streaming Responses](https://inference-docs.cerebras.ai/capabilities/streaming)
- [Reasoning](https://inference-docs.cerebras.ai/capabilities/reasoning)
- [Prompt Caching](https://inference-docs.cerebras.ai/capabilities/prompt-caching)
- [Rate Limits](https://inference-docs.cerebras.ai/support/rate-limits)
- [GPT-OSS-120B model page](https://inference-docs.cerebras.ai/models/openai-oss)
- [OpenAI Compatibility](https://inference-docs.cerebras.ai/openai)
- [Cerebras × Pydantic AI integration](https://inference-docs.cerebras.ai/integrations/pydantic-ai)
- [Cerebras × LiteLLM integration](https://inference-docs.cerebras.ai/integrations/litellm)
- [Cerebras pricing](https://www.cerebras.ai/pricing)
- [Python SDK (GitHub)](https://github.com/Cerebras/cerebras-cloud-sdk-python)
- [cerebras-cloud-sdk on PyPI](https://pypi.org/project/cerebras-cloud-sdk/)
- [Pydantic AI overview](https://ai.pydantic.dev/)
- [Pydantic AI — Cerebras provider](https://ai.pydantic.dev/models/cerebras/)
- [Pydantic AI — Tools](https://ai.pydantic.dev/tools-toolsets/tools/)
- [Pydantic AI — Structured output](https://ai.pydantic.dev/output/)
- [Pydantic AI — Dependencies](https://ai.pydantic.dev/dependencies/)
- [Pydantic AI — Testing](https://ai.pydantic.dev/testing/)
- [pydantic-ai on PyPI](https://pypi.org/project/pydantic-ai/)
- [google-adk on PyPI](https://pypi.org/project/google-adk/)
- [google/adk-python on GitHub](https://github.com/google/adk-python)
- [ADK docs — LiteLLM integration](https://google.github.io/adk-docs/agents/models/litellm/)
- [ADK issue #217 — output_schema not passed to LiteLlm models](https://github.com/google/adk-python/issues/217)
- [ADK issue #4552 — Annotated `Field(description=...)` ignored by FunctionTool](https://github.com/google/adk-python/issues/4552)
- [ADK issue #297 — UniqueViolation on streaming multi-part responses](https://github.com/google/adk-python/issues/297)
- [Artificial Analysis — Cerebras provider page](https://artificialanalysis.ai/providers/cerebras)
