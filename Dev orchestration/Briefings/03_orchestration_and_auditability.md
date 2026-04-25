# Briefing 03 — Meta-Orchestration, SDK Abstraction, Auditability, Frontend Visibility

> Audience: a FinTech architect deciding whether this framework can carry their compliance, audit, and explainability requirements. This briefing covers the router (the agent that picks the pipeline), how multiple LLM SDKs are abstracted, how every API call is captured, and how the frontend renders reasoning + assumptions in real time.

---

## 1. The meta-orchestrator: one router agent in front of many pipelines

There is **one** top-level agent that decides which pipeline to launch from a free-text user message: `RouterAgent` (`orchestration/agents/router_agent.py`). It runs Gemini-2.5-flash today; the model choice is a one-line change.

### How it works (`router_agent.py:105-158`)

1. **Prefilter** (`_prefilter`, lines 73-81) — cheap regex check for prompt-injection markers (`"ignore previous"`, `"system prompt"`, `\x00`, …) and minimum length. Blocked messages never reach the LLM.
2. **LLM classification** — single Gemini call with a system prompt listing the seven available pipelines and required output JSON shape (lines 27-58).
3. **Validation** — pipeline name must be in `_VALID_PIPELINES` set; confidence must be ≥ 0.2 (`lines 84-87, 143-144`).
4. **Compound intent parsing** (`_parse_secondary`, lines 90-102) — the router can return a primary pipeline plus a `secondary_intents` list. Duplicates and unknown pipelines are stripped.
5. **Returns** a dict: `{pipeline, params, confidence, reasoning, secondary_intents}`.

### Fan-out at the chat endpoint (`orchestration/api/routes/chat.py:35-89`)

```python
classification = await classify(req.message)
run_id = await execute_pipeline(pipeline_name=classification["pipeline"], ...)

secondary_runs = []
for intent in classification["secondary_intents"]:
    sec_run_id = await execute_pipeline(pipeline_name=intent["pipeline"], ...)
    secondary_runs.append(SecondaryRun(run_id=sec_run_id, ...))

return ChatResponse(run_id=run_id, secondary_runs=secondary_runs, ...)
```

Each pipeline run gets its own UUID and its own SSE stream. The frontend tracks `[primary_run_id, *secondary_run_ids]` and subscribes to all of them.

### Why this matters for FinTech

A user typing **"Freeze account 1234 and open a SAR for the underlying transactions"** triggers:

- Primary: `account_freeze` pipeline (params `{account_id: 1234}`)
- Secondary: `sar_drafting` pipeline (params `{account_id: 1234}`)

Both runs happen in parallel with full audit trails, independently completable. If the SAR drafter fails, the freeze still succeeds. The router's own decision (which pipelines, with what confidence, with what reasoning string) is itself returned in the chat response — so you can show the user *why* you launched what you launched, and you can persist that classification record alongside the run for later review.

### Three things to know before relying on the router for production FinTech

1. **It is itself an LLM call.** Confidence floor 0.2 is permissive; for high-stakes routing tighten it, and add an explicit fallback path that asks the user to confirm rather than defaulting to a pipeline.
2. **The system prompt is the routing policy.** When you add a pipeline you must also extend the prompt at `router_agent.py:27-58` and the validation set at `lines 62-70`. Forgetting either silently drops the new pipeline from routing.
3. **Bypass available.** The HTTP surface also exposes `POST /pipelines/run/{name}` (`routes/pipelines.py:32-42`) which skips the router entirely. Use this for scheduled jobs, webhook triggers, and back-office tools where the pipeline is known by name.

---

## 2. The SDK abstraction: one registry, many providers

The framework uses two LLM SDKs today and is structured to accept more:

| Provider | SDK | Used by |
|---|---|---|
| Google | `google.adk` (Agent Development Kit, via `_adk_runner.py`) | RouterAgent, ReactiveAgent, ProactiveAgent, ResearchAgent, RegulatoryDriftAgent, etc. |
| Anthropic | `anthropic` SDK direct | ProposalWriter |

### The seam: `agent_registry.py`

```python
_AGENT_REGISTRY: dict[str, str] = {
    "ReactiveAgent":  "orchestration.agents.reactive_agent:run",
    "ProposalWriter": "orchestration.agents.proposal_writer:run",
    # ...
}

def get_agent(name: str) -> Callable:
    dotted = _AGENT_REGISTRY[name]
    module_path, attr = dotted.rsplit(":", 1)
    return getattr(importlib.import_module(module_path), attr)
```

The executor only ever sees a callable named `run` that takes `AgnesContext` and returns a dict. **It does not know which SDK is behind it.** That uniform contract is the entire abstraction.

### Adding a new provider (e.g., Bedrock / OpenAI / Azure OpenAI for a FinTech that's already on AWS)

Three steps, no executor change:

1. Create `orchestration/agents/decision_memo_writer_bedrock.py`:
    ```python
    import boto3
    from orchestration.api.agnes_context import AgnesContext

    _client = boto3.client("bedrock-runtime", region_name="eu-west-1")

    async def run(ctx: AgnesContext) -> dict:
        prompt = build_prompt(ctx.trigger_payload, ctx.node_outputs)
        resp = _client.invoke_model(modelId="anthropic.claude-...", body=...)
        return {
            "narrative": resp["body"]["content"],
            "model": "claude-via-bedrock",
            "tokens_in": resp["usage"]["input_tokens"],
            "tokens_out": resp["usage"]["output_tokens"],
            "assumptions": extract_assumptions(resp),
        }
    ```
2. Register: add `"DecisionMemoWriter": "orchestration.agents.decision_memo_writer_bedrock:run"` to `_AGENT_REGISTRY`.
3. Reference in YAML: `agent_class: DecisionMemoWriter`.

For provider redundancy (which a FinTech typically needs for SLA), wrap the call inside the agent module — try Bedrock first, fall back to a cached response or a degraded-mode dict. The executor will keep going.

### Tools follow the same pattern

`_TOOL_REGISTRY` (`agent_registry.py:15-29`) maps tool names to sync `run(ctx)` functions in `orchestration/tools/`. There is no SDK at all for tools — they are pure Python that hits whatever API or DB you point them at.

---

## 3. The lifecycle of a single API call

Walk through one node — say, `verify-identity` calling an external IDV provider — from launch to audit record:

```
1. RouterAgent classifies user message → "kyc_onboarding"
   └─ chat.py calls execute_pipeline(...)
       └─ creates run_id in pipeline_runs table
           └─ asyncio.create_task(_execute(...))      # returns run_id immediately

2. _execute() loads YAML → topological_layers → loop:
   layer 0:  [pull-application]                       # sync tool
   layer 1:  [verify-identity, screen-sanctions]      # parallel, mixed sync/async
   layer 2:  [risk-score]                             # waits for both
   layer 3:  [write-decision-memo]                    # LLM agent

3. _run_node(verify-identity, ctx):
   ├─ write event {event_type: node_started, node_id: verify-identity}
   ├─ get_tool("IdvProviderTool") → import + cache callable
   ├─ run_in_executor(None, fn, ctx)                  # tool runs in thread pool
   │   └─ tool function:
   │       ├─ reads ctx.trigger_payload (applicant data)
   │       ├─ reads os.environ["IDV_API_KEY"]
   │       ├─ HTTPS POST to provider
   │       ├─ returns {status, score, document_score, biometric_score,
   │                   provider: "Onfido", checked_at: "2026-04-25T...",
   │                   confidence: 0.94}
   ├─ append _elapsed_ms
   └─ return (node_id, output, None)

4. Back in _execute, after gather() of layer 1:
   ├─ ctx.node_outputs["verify-identity"] = output
   └─ write event {
        event_type: node_completed,
        node_id: verify-identity,
        data: {node_output: {...full dict above...}, _elapsed_ms: 412},
        created_at: NOW
      }
   └─ event_bus.publish(run_id, event)               # SSE subscribers get it

5. Layer 2 starts; risk-score reads ctx.get("verify-identity") and
   ctx.get("screen-sanctions"); produces composite score.

6. Layer 3 starts; write-decision-memo (LLM agent) reads everything and
   produces narrative dict; same event-write + publish.

7. write event {event_type: pipeline_completed}
   update_run_status(run_id, "completed")
```

**Three things to notice for FinTech compliance:**

- **The full input context is implicit but recoverable.** `ctx.node_outputs` at the moment of any node's invocation = the union of all prior `node_completed` events for that run. You can reconstruct exactly what `risk-score` saw by replaying the event log up to its `node_started` timestamp.
- **API keys are pulled from environment at call time.** No global SDK initialisation. This means key rotation, per-environment keys, and graceful degradation (`if not os.environ.get(...): return {"skipped": ...}`) all work without code change.
- **There is no implicit retry.** A flaky bureau call that 503s aborts the run. If your FinTech SLA can't tolerate that, add retry inside the tool (e.g., `tenacity`) and capture each attempt in the returned dict.

---

## 4. Auditability: what is captured, where, and how to read it back

### 4.1 Two persistence layers

**`orchestration.db` (SQLite at repo root)** — the event store. Two tables:

- `pipeline_runs`: one row per pipeline execution. Columns: `id`, `pipeline_name`, `trigger_source`, `trigger_payload` (JSON), `status` (running/completed/failed), `started_at`, `completed_at`, `error`.
- `pipeline_events`: many rows per run. Columns: `run_id`, `event_type`, `node_id`, `data` (JSON), `created_at`.

Event types written by the executor:

| Event type | Emitted from | What's in `data` |
|---|---|---|
| `pipeline_started` | `_execute` | `{pipeline: name}` |
| `node_started` | `_run_node` | `{}` |
| `node_completed` | `_execute` | `{node_output: <full dict>}` — this is the audit payload |
| `node_skipped` | `_execute` | `{}` (when guard returned false) |
| `node_failed` | `_execute` | `{error: <traceback>}` |
| `pipeline_completed` | `_execute` | `{pipeline: name}` |
| `pipeline_failed` | `_execute` | `{error: ...}` |

**`db_enriched.sqlite` (domain DB)** — application data. For FinTech this would be your customer / transaction / decision tables. The framework does not require this; it's just where domain tools read from and where you'd persist final decisions.

### 4.2 Reading the trail back

A single SQL query reproduces any decision:

```sql
SELECT
  e.created_at, e.event_type, e.node_id,
  json_extract(e.data, '$.node_output') AS output,
  json_extract(e.data, '$._elapsed_ms') AS ms
FROM pipeline_events e
WHERE e.run_id = '<uuid>'
ORDER BY e.created_at;
```

This returns the full ordered trace: every node, when it ran, what it returned, how long it took. For a regulator asking "why did you decline customer X?", point at this query.

### 4.3 What's *not* captured today (and what FinTech needs to add)

The framework gives you the skeleton. To meet typical FinTech audit requirements you should extend node outputs (and therefore the event payloads) with:

| Field | Why |
|---|---|
| `model_version` (per agent) | DORA / model-risk-management — which model version produced the call |
| `prompt_hash` | Reproducibility — without storing PII-laden prompts in the clear |
| `tokens_in` / `tokens_out` / `cost_usd` | Cost tracking + LLM usage governance |
| `assumptions: [...]` | Make implicit reasoning explicit. *Required* for committee review (see §5) |
| `data_sources: [{name, url, accessed_at, hash}]` | Provenance of every fact the agent relied on |
| `confidence: 0.0-1.0` | Already present in shipped tools; standardise the scale across all nodes |
| `pii_redacted: true/false` | Whether the persisted output is safe for broad access |

Adding these is purely a matter of returning richer dicts. Nothing in the executor changes. The frontend already renders arbitrary keys from `node_output`, so they appear automatically.

### 4.4 Tamper-evidence (the bit that's missing)

Plain SQLite is not tamper-evident. For SOX / DORA / MiFID II audit requirements, mirror every event write to an append-only store. The cleanest place is `db.write_event` (called from `dag_executor.py:59, 116, 120, 123`). Wrap it once, and every event flows to both the live SQLite (for queries) and an append-only log (for audit). Options:

- **AWS QLDB** — purpose-built ledger DB, cryptographic verification.
- **S3 Object Lock + hash chain** — write each event as an object, include the previous event's SHA-256 in the body. Cheap, works anywhere.
- **External SIEM** — push to Splunk / Datadog with an HMAC signature.

Either way, the change is local to one function. The framework doesn't fight you on this.

---

## 5. Frontend visibility: live DAG, reasoning, assumptions

The shipped frontend (`orchestration/ui/`, a Lovable-generated React + Vite app) renders pipelines live. The data flow:

```
React store (Zustand) ─── subscribes ───▶ GET /runs/{id}/stream  (SSE)
       │                                          │
       │                                          │ replays historical events from
       │                                          │ pipeline_events table, then
       │                                          │ tails event_bus subscriptions
       │                                          ▼
       │                                  publishes each node_started /
       │                                  node_completed / node_skipped event
       │                                          │
       ▼                                          │
DagGraphView.tsx ◀── store updates per event ─────┘
   - node colour per status (pending/running/completed/failed/skipped)
   - output panel renders node_output JSON (humanised keys, collapsible arrays)
   - `when:` condition labels visible on each node card
   - "+N more" pill for compound-intent secondary runs
```

### What the user sees, per node

For each node card the UI surfaces:

- **Node ID + class** (humanised, e.g., `IdvProviderTool` → "Identity Verification")
- **Status + elapsed time** (`completed in 412 ms`)
- **`when:` guard** if present (so a reviewer sees *why* a node was or wasn't run)
- **Full output dict** in a collapsible panel — every key from `node_output`. Long arrays collapse; `narrative` / `proposal_text` / `alert_narrative` keys render as prose.

This is why the recommendation in §4.3 to put **assumptions** and **data_sources** into node outputs matters: they show up in the UI automatically. The frontend has no special knowledge of those keys — it just renders whatever the node returned.

### For FinTech — the concrete pattern

Every agent that produces a decision-influencing narrative should return a dict shaped like:

```python
return {
    "narrative": "Recommended decision: APPROVE with enhanced monitoring. ...",
    "decision_recommendation": "approve_with_monitoring",
    "assumptions": [
        "Address verified against utility bill dated 2026-03-12",
        "PEP screen returned no hits at 2026-04-25T13:21Z",
        "Source-of-funds documentation is incomplete (gap: 2025-11 to 2026-01)",
    ],
    "data_sources": [
        {"type": "credit_bureau", "provider": "Equifax", "score": 712,
         "pulled_at": "2026-04-25T13:21:04Z", "ref": "EQ-2026-04-25-9c8a"},
        {"type": "sanctions", "provider": "Refinitiv WC",
         "checked_at": "2026-04-25T13:21:08Z", "lists": ["OFAC SDN", "UN", "EU"]},
    ],
    "confidence": 0.81,
    "model": "claude-sonnet-4-6",
    "tokens_in": 4218, "tokens_out": 612,
}
```

When the reviewer opens this run in the UI:

- They see the narrative as prose.
- They see every assumption as a bulleted list (the frontend already collapses arrays into expandable lists).
- They see every data source with provenance.
- They see the model version and token cost.
- They can click "Replay" / pull `/runs/{id}` to get the exact JSON for an audit packet.

Nothing extra is required from the framework. The discipline is at the agent author's level: **return dicts that contain the assumptions, sources, and confidence**, and the auditability comes for free.

### Endpoints the frontend uses (`orchestration/api/routes/pipelines.py`)

| Endpoint | Purpose |
|---|---|
| `POST /pipelines/run/{name}` | Trigger by name (bypass router) |
| `GET /pipelines` | List available pipelines |
| `GET /pipelines/{name}/graph` | Pipeline structure: nodes, layers, deps, conditions — used to render the DAG before any run starts |
| `GET /runs` | Recent runs (paged) |
| `GET /runs/{id}` | Final state + all events |
| `GET /runs/{id}/stream` | SSE: replay historical events, then live tail until `pipeline_completed` / `pipeline_failed` |
| `GET /proposals` | Successful runs that produced a decision narrative |

The SSE stream replays history first, then tails — meaning if a user opens the UI 10 minutes after a run started, they see every event up to "now" instantly, then continue receiving live updates. There is no replay/live divergence.

---

## 6. Putting it together: a FinTech run, end to end

Imagine a relationship manager types **"Open KYC for ACME Holdings — they've moved 4M EUR through us this quarter"**.

1. **Router** (Gemini, ~400ms) classifies → `pipeline: kyc_periodic_review`, `params: {customer: "ACME Holdings"}`, `secondary_intents: [{pipeline: "transaction_pattern_review", params: {customer: "ACME Holdings", quarter: "Q1-2026"}}]`. Reasoning string: *"User requests KYC refresh; large transaction volume warrants concurrent transaction pattern review."* — this string is returned in the chat response and shown to the user.
2. **Two pipelines launch in parallel**, each with its own run UUID. The chat endpoint returns `{run_id, secondary_runs: [{run_id, pipeline}], reasoning, ...}` in <100ms.
3. **Frontend** subscribes to both `/runs/{id}/stream` endpoints. The DAG canvases render side-by-side; nodes light up as each layer completes.
4. **Per node**, the frontend shows the full output dict the moment the executor writes it: KYC data pulled, sanctions checked (no hit, list snapshot), credit data, transaction patterns, an LLM-drafted memo with explicit assumptions and data sources.
5. **`pipeline_completed`** events fire; the UI shows final status. Decision recommendations are written into `db_enriched.sqlite` by the relevant tools; a human reviewer is notified.
6. **Six months later**, an auditor asks "why did we accept ACME's renewed KYC?". One SQL query against `pipeline_events` reproduces the full sequence: every API called, every score returned, every assumption the LLM made, every source it cited, the model version, token cost, elapsed time per node. If the append-only mirror (§4.4) is in place, integrity is provable.

That is the whole framework, applied to FinTech. The shape doesn't change; only the YAML and the tools/agents inside it do.

---

## 7. Summary: what to take away

- **One router agent** in front. It classifies free-text into a known pipeline and supports compound intents. For FinTech, pin the confidence floor higher and add a "confirm before launch" path for ambiguous cases.
- **Many pipelines**, each a YAML DAG of tool nodes (deterministic) and agent nodes (LLM). Tools own decisions; agents own narratives.
- **One executor** that runs layers in parallel and persists every node invocation as an event. No retry, no branching primitive other than `when:` guards — that's intentional, and keeps the audit trail flat.
- **One registry** abstracting LLM SDKs. Adding a provider is a file + one registry line. No executor change.
- **One event store + one event bus.** SQLite for query, in-memory queues for SSE. Frontend renders the live DAG and every node's full output dict.
- **Auditability is in your hands at the dict level.** Whatever an agent returns is what regulators see. Make assumptions, sources, model versions, and confidence explicit in the returned dict and they appear in the UI and the audit log automatically.
- **Add tamper-evidence** by wrapping `db.write_event` to mirror to an append-only log. Add **provider redundancy** inside agent modules. Add **AuthN/AuthZ** at the FastAPI surface. The framework leaves these as clean extension points and does not assume them.
