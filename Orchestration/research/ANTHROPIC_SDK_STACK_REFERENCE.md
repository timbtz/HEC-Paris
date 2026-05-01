# Anthropic SDK Stack Reference — Autonomous CFO MVP

**Status:** v1.0 — 2026-04-25
**Audience:** future Claude sessions implementing the Phase 1 meta-layer foundation on top of the Anthropic Python SDK. Self-contained: do not assume prior context beyond the MetaPRD.
**Companion doc:** `CEREBRAS_STACK_REFERENCE.md` (separate, non-overlapping reference for the Cerebras-only path).
**Scope:** This doc covers **Anthropic SDK only** (Claude Sonnet 4.6). It does not mix in or compare against Cerebras. If a session is implementing the Cerebras path, use the companion doc instead.

---

## 0. Project context (read before writing code)

The Autonomous CFO MVP (`Orchestration/PRDs/MetaPRD.md`) imposes constraints that override generic best-practice tutorials:

- **Sub-5s end-to-end** from Swan webhook to GL post + UI push.
- **Deterministic-first.** Rules, identifier matches, and cache hits before any LLM call. AI never does arithmetic, never produces journal entries directly, never bypasses the confidence gate.
- **Decision trace is non-negotiable.** Every AI write produces a row in `decision_traces` (model, prompt_hash, alternatives, confidence, approver_id) — joined to `journal_lines`. Not a JSON sidecar.
- **YAML pipeline DSL.** Pipelines are declarative DAGs; a thin async executor (`api/dag_executor.py`) runs them. Adding a new event type is YAML + one tool registry line.
- **No LangGraph for MVP** (PRD §4 explicit out-of-scope). The Anthropic SDK + named-condition gates is the lock-in. Re-evaluate at Phase 3 only.
- **Two SQLite databases.** `accounting.db` (domain, GL, traces) + `orchestration.db` (run history, append-only).
- **Integer cents.** No floats on money paths.
- **Per-employee budgets + AI-credit cost tracking is the demo wedge** (`memory/project_pitch_direction.md`). Every Claude call must attribute cost to the calling employee/agent.

Pinned model: **`claude-sonnet-4-6`** as the default. `claude-haiku-4-5` for fast classifiers; `claude-opus-4-7` only for Phase 3+ planner escalations. Pin per agent.

Key implication: **the executor is yours, not Anthropic's.** Anthropic SDK calls live inside individual DAG nodes. The agent loop is a Python `while True` you control — no hidden state, no framework magic.

---

## 1. SDK setup

### Install + auth

```bash
pip install anthropic       # version >= 1.0.0
export ANTHROPIC_API_KEY="sk-ant-..."
```

Python 3.9+ (project's 3.10+ is fine).

### Client init

```python
import os, anthropic

# Sync — for scripts, tests
client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

# Async — for FastAPI handlers and DAG nodes
aclient = anthropic.AsyncAnthropic(
    api_key=os.environ["ANTHROPIC_API_KEY"],
    timeout=4.5,        # tighter than the 5s SLA so we fail before the executor's deadline
    max_retries=2,
)
```

The SDK auto-handles `x-api-key`, `anthropic-version: 2023-06-01`, content-type, connection pooling (httpx, ~100 concurrent per client), and retries (default 3 with exponential backoff on 429/5xx/connection errors).

For the project: **share one `AsyncAnthropic` instance across the FastAPI app**. Construct it at startup; pass it via `FingentContext` or import as a module-level singleton.

### Beta features

Beta features (e.g., 1-hour cache, idempotency) are accessed via `client.beta.*` and require an explicit `betas=["feature-name"]` on the request. Don't enable betas the demo doesn't need — they churn.

---

## 2. Messages API basics

### Request shape

```python
response = client.messages.create(
    model="claude-sonnet-4-6",
    max_tokens=2048,                # required
    temperature=0.0,                # deterministic for classifiers
    system="You are an autonomous CFO ...",
    tools=[...],                    # optional
    tool_choice={"type": "auto"},   # optional
    messages=[
        {"role": "user", "content": "Classify this Acme SAS €4,950 inbound SEPA"}
    ],
)
```

### Response shape

```python
response.id                # 'msg_...'
response.model             # 'claude-sonnet-4-6'
response.stop_reason       # 'end_turn' | 'max_tokens' | 'tool_use' | 'stop_sequence' | 'refusal'
response.content           # list of blocks: text | tool_use | thinking | refusal
response.usage             # Usage(input_tokens, output_tokens,
                           #       cache_creation_input_tokens, cache_read_input_tokens)
```

### Required and common parameters

| Parameter | Required | Notes |
|---|---|---|
| `model` | yes | Pin per agent. Default `claude-sonnet-4-6`. |
| `max_tokens` | yes | No default — request errors without it. Cap aggressively per node. |
| `messages` | yes | Alternating `user`/`assistant`. `tool_result` blocks must be in a `user` message and come **first** in `content`. |
| `system` | no | Separate field, not a message. Cache-friendly. |
| `temperature` | no | 0.0 for classifiers, 0.2 for nuanced approvals, 0.4–0.8 for writers. |
| `top_p` | no | Default 1.0. Don't tune unless you know why. |
| `stop_sequences` | no | List of strings. Triggers `stop_reason: "stop_sequence"`. |
| `tool_choice` | no | `{"type": "auto"}` (default), `{"type": "any"}`, `{"type": "tool", "name": "..."}`, or `{"type": "none"}`. |

### Content block types (in responses)

- `text` — natural language: `block.text`
- `tool_use` — model invoked a tool: `block.id`, `block.name`, `block.input`
- `thinking` — extended-thinking output: `block.thinking` (only when enabled)
- `refusal` — Claude declined to answer: `block.text` with `stop_reason="refusal"`

### Message ordering rules (commonly tripped over)

1. `messages` must alternate `user` / `assistant` / `user` / ...
2. A `user` message that responds to tool calls must put `tool_result` blocks **first** in its `content`, before any text.
3. `tool_result.tool_use_id` must match a `tool_use.id` from the immediately preceding `assistant` message.
4. Don't insert text blocks between a `tool_use` and its `tool_result`.

---

## 3. Tool use (the agent loop)

### Tool schema (JSON Schema)

```python
from pydantic import BaseModel, Field
import json

class CounterpartyResolveInput(BaseModel):
    iban: str | None = Field(default=None, description="IBAN if known")
    legal_name: str | None = Field(default=None, description="Free-text counterparty name")
    vat_number: str | None = Field(default=None, description="EU VAT number")

tools = [
    {
        "name": "resolve_counterparty",
        "description": (
            "Look up a counterparty in the canonical table by IBAN, VAT, or fuzzy name match. "
            "Returns {counterparty_id, legal_name, confidence, source} or null if not found. "
            "This is deterministic; prefer it before classifying."
        ),
        "input_schema": CounterpartyResolveInput.model_json_schema(),
    },
    {
        "name": "submit_classification",
        "description": "Submit final structured classification with confidence and alternatives.",
        "input_schema": {
            "type": "object",
            "properties": {
                "gl_account":      {"type": "string"},
                "counterparty_id": {"type": "integer"},
                "confidence":      {"type": "number"},
                "rationale":       {"type": "string"},
                "alternatives":    {"type": "array", "items": {"type": "string"}},
            },
            "required": ["gl_account", "counterparty_id", "confidence", "rationale"],
        },
    },
]
```

**Use Pydantic to author the schema** (`Model.model_json_schema()`) so the same type validates the model's output server-side. Single source of truth.

### `tool_choice`

```python
tool_choice={"type": "auto"}                                  # Claude decides
tool_choice={"type": "any"}                                   # must use one tool
tool_choice={"type": "tool", "name": "submit_classification"} # force a specific tool
tool_choice={"type": "none"}                                  # disable tools
```

**Caveat:** `"any"` and specific-tool forcing are **not compatible with extended thinking**. With `thinking` enabled, only `"auto"` and `"none"` work.

### Idiomatic agent loop

```python
import json, time, asyncio
from typing import Awaitable, Callable

ToolFn = Callable[[dict], Awaitable[dict]]   # async (input) -> output

async def run_agent_loop(
    aclient,
    *,
    system: str | list,
    tools: list[dict],
    tool_registry: dict[str, ToolFn],
    initial_user: str,
    model: str = "claude-sonnet-4-6",
    max_tokens: int = 1024,
    temperature: float = 0.0,
    max_iterations: int = 6,
    deadline_s: float = 4.5,
) -> dict:
    """Canonical loop. Caller is responsible for inserting decision_traces
    rows after each model call."""
    started = time.monotonic()
    messages: list[dict] = [{"role": "user", "content": initial_user}]
    iter_traces: list[dict] = []

    for hop in range(max_iterations):
        elapsed = time.monotonic() - started
        if elapsed >= deadline_s:
            raise TimeoutError(f"loop deadline exceeded at hop {hop}")

        response = await aclient.messages.create(
            model=model,
            max_tokens=max_tokens,
            temperature=temperature,
            system=system,
            tools=tools,
            messages=messages,
            timeout=deadline_s - elapsed,
        )
        iter_traces.append({
            "hop": hop,
            "response_id": response.id,
            "stop_reason": response.stop_reason,
            "input_tokens": response.usage.input_tokens,
            "output_tokens": response.usage.output_tokens,
            "cache_read_tokens": response.usage.cache_read_input_tokens,
            "cache_write_tokens": response.usage.cache_creation_input_tokens,
        })

        if response.stop_reason == "end_turn":
            return {"response": response, "messages": messages, "trace": iter_traces}

        if response.stop_reason != "tool_use":
            # max_tokens, refusal, stop_sequence — caller decides how to handle
            return {"response": response, "messages": messages, "trace": iter_traces}

        # Append the assistant turn (verbatim — content blocks are immutable across turns)
        messages.append({"role": "assistant", "content": response.content})

        tool_calls = [b for b in response.content if getattr(b, "type", None) == "tool_use"]
        tool_results = []
        for tc in tool_calls:
            fn = tool_registry.get(tc.name)
            if fn is None:
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": tc.id,
                    "content": json.dumps({"error": f"unknown tool: {tc.name}"}),
                    "is_error": True,
                })
                continue
            try:
                output = await fn(tc.input)
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": tc.id,
                    "content": json.dumps(output),
                })
            except Exception as e:
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": tc.id,
                    "content": f"{type(e).__name__}: {e}",
                    "is_error": True,
                })

        messages.append({"role": "user", "content": tool_results})

    raise RuntimeError("max_iterations exceeded")
```

**Key invariants:**
- `tool_result.tool_use_id` must match its `tool_use.id`.
- `tool_result` blocks come **first** in the `user` message's `content` list, before any text.
- Append assistant content **verbatim** — do not modify or re-wrap blocks. They are immutable across turns.
- `is_error: True` is the structured way to tell Claude a tool failed; it will adapt rather than crash.
- Bound `max_iterations`. Six is generous for the project's 5s budget.

### Parallel tool calls

Claude can emit multiple `tool_use` blocks in one assistant turn. Run them concurrently:

```python
import asyncio

async def run_tools_parallel(tool_calls, tool_registry):
    async def one(tc):
        try:
            output = await tool_registry[tc.name](tc.input)
            return {"type": "tool_result", "tool_use_id": tc.id, "content": json.dumps(output)}
        except Exception as e:
            return {"type": "tool_result", "tool_use_id": tc.id,
                    "content": f"{type(e).__name__}: {e}", "is_error": True}
    return await asyncio.gather(*[one(tc) for tc in tool_calls])
```

Disable parallelism (serialize) for nodes with ordering constraints — e.g., `post-entry` followed by `decrement-envelope` must serialize.

---

## 4. Structured output (the submit-tool pattern)

The Claude API has no native `response_format: json_schema` like OpenAI. The idiomatic pattern is to **define a single mandatory `submit_*` tool and force the model to call it**.

```python
submit_tool = {
    "name": "submit_classification",
    "description": "Final structured submission. Call exactly once.",
    "input_schema": GLClassification.model_json_schema(),
}

response = await aclient.messages.create(
    model="claude-sonnet-4-6",
    max_tokens=1024,
    temperature=0.0,
    system=SYSTEM_PROMPT,
    tools=[*helper_tools, submit_tool],
    tool_choice={"type": "tool", "name": "submit_classification"},
    messages=[{"role": "user", "content": user_prompt}],
)

decision_block = next(b for b in response.content if getattr(b, "type", None) == "tool_use")
parsed = GLClassification.model_validate(decision_block.input)
```

**Why this beats prompted JSON:**
- The model emits `input` as a structured dict, not a string requiring `json.loads`.
- Schema is enforced server-side at the parameter level (Claude validates against `input_schema`).
- One round-trip even when forced; combines naturally with helper tool calls inside the loop.
- `tool_choice` forcing means you cannot get an `end_turn` without the structured submission.

**Pydantic still required.** Schema-correct ≠ semantically correct. Validate the parsed dict and treat validation failure as a normal flow (retry once with a corrective message, then queue for review).

### Pattern 2 — prompted JSON + retry (only as fallback)

```python
import json
from pydantic import BaseModel, ValidationError
from anthropic import Anthropic

class Approval(BaseModel):
    approved: bool
    reason: str
    confidence: float

system = (
    'Respond with VALID JSON only. No markdown, no prose, no comments. '
    'Schema: {"approved": bool, "reason": str, "confidence": float in [0,1]}'
)

for attempt in range(2):
    resp = await aclient.messages.create(
        model="claude-sonnet-4-6", max_tokens=300, temperature=0.0,
        system=system, messages=messages,
    )
    text = resp.content[0].text
    try:
        return Approval.model_validate_json(text)
    except (json.JSONDecodeError, ValidationError) as e:
        messages.append({"role": "assistant", "content": resp.content})
        messages.append({"role": "user",
                         "content": f"That was invalid: {e}. Re-emit valid JSON only."})
```

Use this only when adding a forced tool isn't appropriate (e.g., simple inline validation in test fixtures).

---

## 5. Streaming

### Text-only convenience

```python
with aclient.messages.stream(
    model="claude-sonnet-4-6", max_tokens=2048,
    messages=[{"role": "user", "content": "..."}],
) as stream:
    async for text in stream.text_stream:
        ...  # yield to SSE
```

### Event-level

```python
async with aclient.messages.stream(...) as stream:
    async for event in stream:
        if event.type == "message_start":
            run_msg_id = event.message.id
        elif event.type == "content_block_delta":
            if event.delta.type == "text_delta":
                yield event.delta.text
            elif event.delta.type == "input_json_delta":
                # Tool args arrive in fragments — accumulate by content_block index
                ...
        elif event.type == "message_stop":
            final = stream.get_final_message()  # has full usage, stop_reason, content
```

### FastAPI SSE endpoint

```python
from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse
import json

app = FastAPI()

@app.get("/runs/{run_id}/stream")
async def run_stream(run_id: int, request: Request):
    async def gen():
        async with aclient.messages.stream(
            model="claude-sonnet-4-6", max_tokens=2048, system=SYSTEM, messages=msgs,
        ) as stream:
            async for text in stream.text_stream:
                if await request.is_disconnected():
                    break
                yield f"data: {json.dumps({'type':'text','t':text})}\n\n"
            final = stream.get_final_message()
            yield f"data: {json.dumps({'type':'done','usage':{'in':final.usage.input_tokens,'out':final.usage.output_tokens}})}\n\n"
    return StreamingResponse(gen(), media_type="text/event-stream")
```

Always check `request.is_disconnected()` inside the generator. Holding the upstream stream after the client drops wastes tokens and burns rate limit.

### Tool use over streaming

`tool_use` blocks split across multiple deltas. Each chunk's `delta.input_json_delta` is a **string fragment** of the JSON args. Accumulate by `content_block_start.index` until the matching `content_block_stop`, then `json.loads` the assembled string.

For the project's pipeline executor, prefer **non-streaming** for nodes that produce structured output (the submit-tool pattern) and use streaming only for `WriterAgent` narratives where partial text is useful in the UI.

---

## 6. Prompt caching

5-minute TTL by default (`{"type": "ephemeral"}`); 1-hour available via beta. Costs: **1.25× write**, **0.1× read**. Minimum cacheable: ~2048 tokens for Sonnet 4.6.

### Where to cache for the project

The agent loop runs the same system prompt + tool definitions across many transactions. Cache:

```python
system = [
    {
        "type": "text",
        "text": COUNTERPARTY_CLASSIFIER_SYSTEM_PROMPT,   # ~1500 tokens, stable
        "cache_control": {"type": "ephemeral"},
    },
    {
        "type": "text",
        "text": json.dumps(TOOL_SCHEMAS),                # ~1200 tokens, stable
        "cache_control": {"type": "ephemeral"},
    },
    {
        "type": "text",
        "text": f"Today: {date.today().isoformat()}. PCG version: 2026-Q2.",  # daily, uncached
    },
]
```

The dynamic block at the end is uncached; everything above is cached as one prefix. Up to **4 cache breakpoints** per request.

**Cache invalidation triggers:**
- Changing tool definitions, system prompt wording, model name.
- Adding/removing/reordering content blocks above the breakpoint.
- The 5-minute TTL expiring without traffic.

**Verify cache hit:**

```python
u = response.usage
print(u.input_tokens, u.cache_creation_input_tokens, u.cache_read_input_tokens)
# A second identical request within 5 min should show:
# cache_creation_input_tokens: 0
# cache_read_input_tokens: ~2700 (the cached prefix)
# input_tokens: just the dynamic suffix + user message
```

### Best practice for the agent loop

Keep `(system, tools)` stable for the duration of a hot demo run. Never reorder tools mid-run. The prompt-cache hit rate is your single biggest knob for staying inside the 5s SLA when running 10–20 classifications in a batch.

---

## 7. Extended thinking (Sonnet 4.6)

```python
response = client.messages.create(
    model="claude-sonnet-4-6",
    max_tokens=8000,
    thinking={"type": "enabled", "budget_tokens": 5000},
    messages=[...],
)

for block in response.content:
    if block.type == "thinking":
        print(block.thinking)
    elif block.type == "text":
        print(block.text)
```

**Constraints:**
- `budget_tokens` must be < `max_tokens` (typically by a wide margin).
- Tool use restrictions: only `tool_choice: {"type": "auto"}` or `{"type": "none"}` are compatible.
- Thinking blocks must be preserved in continuation messages — do not strip them when echoing the assistant turn back in a multi-turn flow.
- Adds 2–3s of latency. Full thinking tokens are charged.

**When to use in this project:**
- ❌ Hot-path classifiers (counterparty, GL account). Latency cost dominates the value.
- ❌ Routine approvals at the confidence gate.
- ✅ Phase 3 `PlannerAgent` decomposing a goal ("save €15k for the CNC machine") into a campaign of envelope adjustments.
- ✅ Phase 4 `ReportPlannerAgent` decomposing a DD-pack template into sub-reports.

For Phase 1–2: leave it off.

---

## 8. Token counting & cost

### Pre-flight

```python
ct = client.messages.count_tokens(
    model="claude-sonnet-4-6",
    system=system, tools=tools, messages=messages,
)
ct.input_tokens   # exact input tokens for this request
```

Use this to gate large prompts before sending — saves a wasted call when you're near the context window.

### Response usage

```python
u = response.usage
u.input_tokens                    # billable at $3/M (or $0.30/M if read from cache)
u.output_tokens                   # billable at $15/M
u.cache_creation_input_tokens     # billable at $3.75/M (1.25× input)
u.cache_read_input_tokens         # billable at $0.30/M (0.1× input)
```

### Sonnet 4.6 pricing (April 2026, $/1M tokens)

| Component | Cost |
|---|---|
| Input | $3.00 |
| Output | $15.00 |
| Cache write (5-min TTL) | $3.75 (1.25×) |
| Cache read | $0.30 (0.1×) |

### Per-call cost helper

```python
def cost_usd(usage, *, model: str = "claude-sonnet-4-6") -> float:
    rates = {  # ($/1M) input, output, cache_write, cache_read
        "claude-sonnet-4-6": (3.00, 15.00, 3.75, 0.30),
        "claude-haiku-4-5":  (0.80,  4.00, 1.00, 0.08),
        "claude-opus-4-7":   (15.00, 75.00, 18.75, 1.50),
    }
    inp, out, cw, cr = rates[model]
    return (
        usage.input_tokens                  * inp
      + usage.output_tokens                 * out
      + usage.cache_creation_input_tokens   * cw
      + usage.cache_read_input_tokens       * cr
    ) / 1_000_000
```

Insert into `decision_traces.cost_usd` (and into the Phase 3 `ai_credit_ledger` table) on every call. This is what makes the per-employee-budget pitch real.

---

## 9. Retries & error handling

### Built-in

The SDK retries `RateLimitError` (429), `APIConnectionError`, and 5xx `APIStatusError` automatically — default 3 retries with exponential backoff. Tune via constructor:

```python
aclient = anthropic.AsyncAnthropic(max_retries=2, timeout=4.5)
```

### Per-request override

```python
response = await aclient.with_options(timeout=2.0, max_retries=0).messages.create(...)
```

For the project: set the **client-level** timeout to 4.5s (just under the executor's 5s budget) so a stuck call frees up the loop in time to fall back to the deterministic path.

### Manual handling

```python
from anthropic import RateLimitError, APIStatusError, APIConnectionError, APITimeoutError

try:
    response = await asyncio.wait_for(aclient.messages.create(...), timeout=4.5)
except APITimeoutError:
    return _fall_back_to_deterministic_classifier(...)
except RateLimitError:
    # backoff and retry once, or escalate
    ...
except APIStatusError as e:
    if e.status_code >= 500:
        # transient; SDK already retried — escalate
        ...
    else:
        raise
```

### Idempotency (beta)

```python
response = await aclient.beta.messages.create(
    model="claude-sonnet-4-6", max_tokens=2048,
    extra_headers={"Idempotency-Key": f"swan-{event_id}"},
    messages=[...],
)
```

A repeated request with the same key returns the same response without re-billing. Useful when the upstream Swan webhook redelivers — though the project's primary idempotency is `INSERT OR IGNORE` on `swan_events.event_id`, this is belt-and-suspenders.

---

## 10. Async + concurrency in FastAPI

### Single shared client

```python
# api/main.py
import anthropic
from fastapi import FastAPI

app = FastAPI()
aclient = anthropic.AsyncAnthropic(timeout=4.5, max_retries=2)
```

The httpx connection pool inside `AsyncAnthropic` handles up to ~100 concurrent connections per client. For the hackathon's load profile (single host, demo-scale), one shared client is correct.

### Concurrent classification

```python
import asyncio

async def classify_batch(transactions: list[dict]) -> list[Classification]:
    async def one(tx):
        try:
            return await classify_with_agent_loop(tx)
        except Exception as e:
            return {"error": str(e), "tx_id": tx["id"]}
    return await asyncio.gather(*[one(t) for t in transactions])
```

For batch reprocessing, gate the concurrency with a semaphore so you don't pile a hundred concurrent calls onto Anthropic's rate limit:

```python
_sem = asyncio.Semaphore(8)

async def classify_one(tx):
    async with _sem:
        return await classify_with_agent_loop(tx)
```

---

## 11. The sub-agent pattern

When a DAG node delegates to a specialized sub-agent (e.g., the Phase 2 `CounterpartyClassifierAgent` calling a `LegalEntitySearchAgent` sub-step), pass **only a scoped slice of context**, never the whole DAG state.

### Trace attribution

Sub-agents do **not share message history** with their parent. Each sub-agent gets a fresh conversation. Cost and trace rows are attributed to the parent run via a sub-run ID convention:

```python
sub_run_id = f"{parent_run_id}::{agent_name}::{uuid4()}"
```

This lets the auditor query the trace with `WHERE pipeline_run_id LIKE ?` to reconstruct the parent + all sub-agents.

### Sub-agent invocation helper

```python
from dataclasses import dataclass
from pydantic import BaseModel

@dataclass
class SubAgentResult:
    output: BaseModel
    cost_usd: float
    input_tokens: int
    output_tokens: int
    sub_run_id: str

async def invoke_sub_agent(
    *,
    parent_run_id: str,
    agent_name: str,
    system: str,
    submit_schema: type[BaseModel],
    scoped_input: str,
    db,
    deadline_s: float = 2.0,
) -> SubAgentResult:
    sub_run_id = f"{parent_run_id}::{agent_name}::{uuid4()}"
    submit_tool = {
        "name": f"submit_{agent_name}",
        "description": f"Final structured output of {agent_name}.",
        "input_schema": submit_schema.model_json_schema(),
    }
    response = await asyncio.wait_for(
        aclient.messages.create(
            model="claude-sonnet-4-6", max_tokens=1024, temperature=0.0,
            system=system,
            tools=[submit_tool],
            tool_choice={"type": "tool", "name": f"submit_{agent_name}"},
            messages=[{"role": "user", "content": scoped_input}],
        ),
        timeout=deadline_s,
    )
    submit_block = next(b for b in response.content if getattr(b, "type", None) == "tool_use")
    parsed = submit_schema.model_validate(submit_block.input)
    cost = cost_usd(response.usage)

    await db.execute(
        """INSERT INTO decision_traces
           (source, agent_run_id, model, prompt_hash, alternatives, confidence, parent_event_id)
           VALUES ('agent', ?, 'claude-sonnet-4-6', ?, ?, ?, ?)""",
        (sub_run_id, hash_request(system, submit_tool, scoped_input),
         json.dumps(getattr(parsed, "alternatives", [])),
         getattr(parsed, "confidence", None),
         parent_run_id.split("::")[0]),
    )
    return SubAgentResult(parsed, cost,
                          response.usage.input_tokens, response.usage.output_tokens,
                          sub_run_id)
```

---

## 12. Multi-agent / planner-worker patterns

For Phase 3+ when a `PlannerAgent` decomposes a goal into a sequence of tool calls, two patterns exist. **Prefer the second.**

### Pattern A — planner-as-tool-orchestrator (avoid for this project)

The planner emits `tool_use` blocks; the agent loop dispatches them as it goes. Looks elegant, but trace attribution is messy and the audit story is "look at the message log."

### Pattern B — planner-as-list-emitter (recommended)

The planner returns a **typed `list[Step]`** Pydantic model. A deterministic Python executor runs each step. Each step's invocation produces its own `decision_traces` row, cleanly linked to the parent plan.

```python
from pydantic import BaseModel

class Step(BaseModel):
    step_number: int
    action: str          # "classify" | "approve" | "log_decision" | ...
    input: dict

class ExecutionPlan(BaseModel):
    steps: list[Step]
    confidence: float
    rationale: str

async def planner_agent(context: dict, db) -> ExecutionPlan:
    sub = await invoke_sub_agent(
        parent_run_id=context["run_id"],
        agent_name="planner",
        system=PLANNER_SYSTEM,
        submit_schema=ExecutionPlan,
        scoped_input=json.dumps(context["goal"]),
        db=db,
        deadline_s=3.0,
    )
    return sub.output

async def execute_plan(plan: ExecutionPlan, ctx, db):
    for step in plan.steps:
        # Each step is a deterministic dispatch — easy to trace, easy to test
        if step.action == "classify":
            await classify_node(ctx, db, step.input)
        elif step.action == "approve":
            await approval_node(ctx, db, step.input)
        # ...
```

**Trade-off:** Pattern B is less flexible (the plan is fixed once emitted) but has a vastly easier audit story. The PRD's deterministic-first stance favors it.

---

## 13. HITL (human-in-the-loop) via condition gates

The PRD §14 risk #4 calls out HITL as a `when:` condition reading from a tool that polls a `decision_pending` table — not native pause/resume. This is the right pattern for the hackathon: simple, replayable, no async channels needed.

### Schema

```sql
-- in accounting.db (or orchestration.db; keep with the run history)
CREATE TABLE decision_pending (
    id              INTEGER PRIMARY KEY,
    pipeline_run_id INTEGER NOT NULL,    -- FK to orchestration.pipeline_runs.id (logical)
    node_id         TEXT NOT NULL,
    decision_type   TEXT NOT NULL,       -- 'classification_review' | 'spend_approval' | ...
    context_json    TEXT NOT NULL,
    confidence      REAL,
    created_at      TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    reviewed_at     TIMESTAMP,
    approved        INTEGER,             -- 0/1; NULL until reviewed
    approver_id     TEXT,
    notes           TEXT
);
CREATE INDEX idx_decision_pending_unreviewed ON decision_pending(reviewed_at) WHERE reviewed_at IS NULL;
```

### Confidence-gate node

```python
async def confidence_gate(ctx, db) -> dict:
    classification = ctx.node_outputs["classify-gl-account"]
    cf = classification["confidence"]
    if cf >= 0.95:
        return {"posted": True,  "needs_review": False, "confidence": cf}
    if cf < 0.70:
        return {"posted": False, "needs_review": False, "confidence": cf, "reject_reason": "low_confidence"}

    pending = await db.execute(
        """INSERT INTO decision_pending (pipeline_run_id, node_id, decision_type,
                                          context_json, confidence)
           VALUES (?, ?, 'classification_review', ?, ?)""",
        (ctx.run_id, "gate-confidence",
         json.dumps({"classification": classification}), cf),
    )
    return {"posted": False, "needs_review": True, "confidence": cf,
            "pending_id": pending.lastrowid}
```

### Polling tool (the HITL block)

```python
async def poll_decision_gate(pending_id: int, db, *,
                              timeout_s: float = 120.0,
                              interval_s: float = 1.0) -> dict:
    started = time.monotonic()
    while True:
        if time.monotonic() - started > timeout_s:
            await db.execute(
                """UPDATE decision_pending
                   SET reviewed_at = CURRENT_TIMESTAMP, approved = 0,
                       notes = 'auto-rejected: timeout'
                   WHERE id = ? AND reviewed_at IS NULL""",
                (pending_id,),
            )
            return {"approved": False, "reason": "timeout"}
        row = await (await db.execute(
            "SELECT approved, approver_id, notes FROM decision_pending "
            "WHERE id = ? AND reviewed_at IS NOT NULL", (pending_id,))).fetchone()
        if row is not None:
            return {"approved": bool(row[0]), "approver_id": row[1], "notes": row[2]}
        await asyncio.sleep(interval_s)
```

The DAG executor runs this in the `queue-review` node. The 120s human window does not block the 5s SLA — the executor releases to the next webhook immediately; this run sits in `pipeline_runs.status='running'` until the human acts.

---

## 14. Determinism patterns

### Temperature by archetype

```python
AGENT_TEMPERATURE = {
    "counterparty_classifier":  0.0,   # deterministic
    "gl_account_classifier":    0.0,
    "confidence_gate":          0.0,
    "review_writer":            0.4,   # mild variation in prose
    "copilot_qa":               0.3,
    "planner":                  0.2,   # exploration but mostly stable
}
```

### Prompt versioning in the trace

Add a column to `decision_traces`:

```sql
ALTER TABLE decision_traces ADD COLUMN prompt_version TEXT;
```

Populate from a constant per agent:

```python
# agents/counterparty_classifier.py
PROMPT_VERSION = "v3"   # bumped when the system prompt is edited
SYSTEM = "..."

# When inserting the trace:
await db.execute("UPDATE decision_traces SET prompt_version = ? WHERE id = ?",
                 (PROMPT_VERSION, trace_id))
```

This is what lets you reproduce a decision after a prompt edit: query traces by `prompt_version`, replay through the executor, verify the new prompt isn't regressing.

### prompt_hash (for cache key + replay)

```python
import hashlib, json

def hash_request(system, tools, last_user_message: str, *, model: str) -> str:
    canonical = json.dumps({
        "model": model,
        "system": system if isinstance(system, str) else [b for b in system],
        "tools": tools,
        "user": last_user_message,
    }, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()[:16]
```

Insert into `decision_traces.prompt_hash`. Two identical hashes within the cache TTL should produce a cache hit; the `cache_read_input_tokens` field is your verification.

### Confidence calibration warning

**LLM `confidence` is an ordinal signal, not a probability.** Use it for thresholding (post / queue / reject) but never report it to a user as "this is 95% safe." For borderline cases, run a small temperature ensemble (3 calls at temp 0.0/0.3/0.5) and vote — the *variance* across attempts is a stronger signal than any single confidence. Cost: 3× tokens; only do this for transactions that hit the gray zone (e.g., `0.62–0.78`).

---

## 15. Cost & latency budgeting per node

The 5s end-to-end budget breaks down roughly as:

```
Total: ~5000 ms

  fetch-transaction (Swan GraphQL):    ~ 200 ms
  resolve-counterparty (DB cache hit): ~  10 ms   — 70%+ of transactions land here
    [if miss]
    ai-counterparty-fallback:          ~ 800 ms
  classify-gl-account (rule hit):      ~  10 ms
    [if novel merchant]
    ai-classifier:                     ~ 800 ms
  build-entry (deterministic):         ~  10 ms
  gate-confidence:                     ~   5 ms
  post-entry (SQL):                    ~  20 ms
  assert-balance (Swan re-query):      ~ 300 ms
  decrement-envelope:                  ~  10 ms
  SSE flush + UI:                      ~  50 ms
  ─────────────────────────────────────────────
  Hot path (rule cache hit):           ~ 615 ms
  AI-fallback path:                    ~ 2215 ms (one fallback) — fits 5s with 2.7s of headroom
  Both fallback (rare):                ~ 3015 ms — still fits
```

### Per-node caps (defensive)

```python
PER_NODE_BUDGET = {
    "ai-counterparty-fallback": {
        "model": "claude-haiku-4-5",
        "max_tokens": 512,
        "temperature": 0.0,
        "deadline_s": 1.5,
        "max_iterations": 3,
        "tool_choice": {"type": "tool", "name": "submit_counterparty"},
    },
    "ai-classifier": {
        "model": "claude-sonnet-4-6",
        "max_tokens": 768,
        "temperature": 0.0,
        "deadline_s": 1.5,
        "max_iterations": 3,
        "tool_choice": {"type": "tool", "name": "submit_classification"},
    },
    "review-writer": {
        "model": "claude-sonnet-4-6",
        "max_tokens": 1024,
        "temperature": 0.4,
        "deadline_s": 2.0,
        "max_iterations": 1,    # one shot, no tools
    },
}
```

### Model-selection heuristic

| Role | Model | Rationale |
|---|---|---|
| Counterparty / GL classifier (fallback) | `claude-haiku-4-5` | 4× cheaper input, ~2× faster for short structured outputs; accuracy parity for narrow-domain classification with a strong system prompt. |
| Confidence-gate writer / review-queue narratives | `claude-sonnet-4-6` | Better at reasoning about ambiguity. |
| Phase 3 planner (goal → campaign) | `claude-sonnet-4-6` initially; escalate to `claude-opus-4-7` on novelty signal | Opus only when the planner emits low confidence on its own plan. |

Pin per agent. Don't sprinkle the model name across the codebase.

---

## 16. Streaming agent loop output to SSE

The PRD's `/runs/{run_id}/stream` SSE endpoint is the demo's headline. Stream pipeline events (not raw tokens) plus an inner stream of model deltas during the writer-agent step.

### Event shape

```python
# emitted by the executor and pushed to all SSE subscribers
{"type": "node_started",   "node_id": "ai-classifier"}
{"type": "tool_started",   "node_id": "ai-classifier", "tool": "resolve_counterparty"}
{"type": "tool_completed", "node_id": "ai-classifier", "tool": "resolve_counterparty",
 "output_summary": {"counterparty_id": 42, "confidence": 1.0}}
{"type": "node_completed", "node_id": "ai-classifier", "elapsed_ms": 812}
{"type": "content_delta",  "node_id": "review-writer",
 "delta": "The transaction was classified as ..."}    # only for streaming nodes
{"type": "pipeline_completed", "run_id": 17}
```

Persist every event to `pipeline_events` (PRD §7.2) **before** flushing to SSE. The DB is the source of truth; the SSE stream is best-effort.

---

## 17. Testing the agent loop deterministically

### Record-and-replay

```python
class RecordedAnthropic:
    def __init__(self, fixture_path: Path, mode: str = "replay"):
        self.fixture = fixture_path
        self.mode = mode
        self.cache = json.loads(fixture_path.read_text()) if fixture_path.exists() else {}

    def _key(self, **kwargs):
        # Strip volatile bits, hash the rest
        canonical = json.dumps(kwargs, sort_keys=True, default=str)
        return hashlib.sha256(canonical.encode()).hexdigest()[:16]

    async def messages_create(self, real_client=None, **kw):
        k = self._key(**kw)
        if self.mode == "replay" and k in self.cache:
            return _Reconstruct.from_dict(self.cache[k])
        if real_client is None:
            raise RuntimeError(f"no recording for key {k}; rerun in record mode")
        resp = await real_client.messages.create(**kw)
        self.cache[k] = resp.model_dump()
        self.fixture.write_text(json.dumps(self.cache, indent=2))
        return resp
```

In tests, inject the recorder; in CI, ensure the cache file exists and assert no live calls happen.

### Assert on traces, not prose

```python
@pytest.mark.asyncio
async def test_known_merchant_skips_llm(db, recorded_client):
    # Given a counterparty already in the table
    await seed_counterparty(db, iban="FR76...", id=42)
    # When a Transaction.Booked event arrives
    await execute_pipeline("transaction_booked", trigger_payload={...}, db=db)
    # Then no AI call should appear in the trace
    rows = await db.execute(
        "SELECT source FROM decision_traces WHERE parent_event_id = ?",
        (event_id,),
    )
    sources = [r[0] for r in await rows.fetchall()]
    assert "agent" not in sources    # rule cache hit; no LLM
    assert "rule" in sources
```

This is the demo-validation mechanism the PRD §11 calls out: "second occurrence of a previously-AI-classified merchant skips the LLM (verified in event log: no `agent_started` for that node on second run)."

---

## 18. Anti-patterns

1. **LangGraph for MVP.** PRD §4 explicit out-of-scope. Anthropic SDK + named-condition gates is the lock-in.
2. **Letting the model do arithmetic.** AI never produces journal entries directly, never sums, never multiplies VAT. Route to deterministic Python.
3. **JSON-mode-via-prose-parsing without retry.** Use the submit-tool pattern (§4). If you must use prose JSON, wrap in Pydantic + retry once with a corrective message.
4. **Treating tool exceptions as model errors.** Catch in the tool dispatcher, return `{"type":"tool_result","is_error":True,"content":...}` so the model can adapt.
5. **Storing decision trace as a JSON sidecar.** First-class table with FKs from Day 1 (PRD §14.7). Lint rule: PR fails if `journal_lines` insert without a sibling `decision_traces` insert in the same code path.
6. **Sharing message history across sub-agents.** Each sub-agent gets a fresh conversation. Mixing histories contaminates context and makes traces unreadable.
7. **Forgetting `max_tokens`.** No default. Cap aggressively per node (§15).
8. **Cache breakpoints in dynamic positions.** Place `cache_control` on the **last block that's identical** across calls. Anything changing below it invalidates the cache.
9. **Reordering tools mid-run.** Cache invalidation. Pin tool order at module load time.
10. **Mutating returned content blocks.** They're immutable across turns. Append `response.content` verbatim to `messages`.
11. **Per-request client construction.** One `AsyncAnthropic` per process; share it. Constructing per request burns connection setup time and trashes the pool.
12. **Treating confidence as a probability.** Ordinal only. Use for thresholding; never report as a percentage.
13. **No timeout on the loop.** Per-node `deadline_s` cap is mandatory. Without it, a runaway tool-use cycle blows the 5s budget and silently kills the demo.
14. **Mixing `tool_choice: any` or specific-tool with `thinking`.** Not compatible. Use `auto` or disable thinking.
15. **`amend` an in-flight assistant message.** Append a new `user` turn instead — content blocks are immutable.

---

## 19. Decision-trace capture (the audit row)

Every Anthropic call inserts exactly one row in `decision_traces` (per PRD §7.2). The fields:

| Column | Source |
|---|---|
| `line_id` | FK to `journal_lines.id` if this trace produced a posting; NULL otherwise |
| `source` | `'agent'` for AI, `'rule'` for cache hit, `'human'` for HITL approval |
| `agent_run_id` | logical FK to `orchestration.pipeline_runs.id` (or sub_run_id for delegations) |
| `model` | `'claude-sonnet-4-6'` etc. |
| `prompt_hash` | `sha256(model + system + tools_schema + last_user_message)` (§14) |
| `alternatives` | JSON array from the `submit_*` tool's `alternatives` field |
| `rule_id` | populated only when `source='rule'`; NULL for AI |
| `confidence` | from the `submit_*` tool's `confidence` field |
| `approver_id` | populated by HITL polling tool when human acts |
| `approved_at` | populated by HITL polling tool |
| `parent_event_id` | the Swan `event_id` that triggered this run |
| `created_at` | auto |

Add a complementary `cost_usd` column (not in PRD §7.2 but trivial to add) populated from `cost_usd(response.usage)` (§8). Required for the per-employee AI-credit ledger.

---

## 20. Sources

- [Messages API](https://docs.claude.com/en/api/messages)
- [Tool use overview](https://docs.claude.com/en/docs/build-with-claude/tool-use)
- [Define tools](https://docs.claude.com/en/docs/agents-and-tools/tool-use/define-tools)
- [Handle tool calls](https://docs.claude.com/en/docs/agents-and-tools/tool-use/handle-tool-calls)
- [Prompt caching](https://docs.claude.com/en/docs/build-with-claude/prompt-caching)
- [Streaming](https://docs.claude.com/en/docs/build-with-claude/streaming)
- [Extended thinking](https://docs.claude.com/en/docs/build-with-claude/extended-thinking)
- [Token counting](https://docs.claude.com/en/api/messages-count-tokens)
- [Client SDKs](https://docs.claude.com/en/api/client-sdks)
- [Building effective agents (engineering blog)](https://www.anthropic.com/engineering/building-effective-agents)
- [Anthropic Python SDK on GitHub](https://github.com/anthropics/anthropic-sdk-python)
