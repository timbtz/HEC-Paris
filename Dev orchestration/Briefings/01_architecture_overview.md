# Briefing 01 — Architecture Overview

> Audience: a FinTech team adopting the Agnes orchestration framework for a regulated workflow (KYC/AML, transaction monitoring, credit decisioning, dispute resolution, etc.). This document is **meta** — it describes how the framework executes work, not the supply-chain domain it currently ships with.

---

## 1. The 30-second model

Agnes is a **deterministic DAG executor** that runs **pipelines** declared in YAML. Each pipeline is a directed acyclic graph of **nodes**. A node is either:

- a **tool** — a synchronous, deterministic Python function (rules, SQL, API call, calculation), or
- an **agent** — an asynchronous LLM call (Anthropic Claude or Google Gemini, depending on the agent).

A single **router agent** sits in front of the executor and decides *which* pipeline to launch from a natural-language message. Every node invocation is persisted as an event and broadcast over Server-Sent Events (SSE), so a frontend can render the DAG live and a regulator can replay any decision later.

```
                  ┌────────────────────────────────────────┐
   user message → │ RouterAgent (Gemini)                   │
                  │ → primary pipeline + secondary intents │
                  └──────────────────┬─────────────────────┘
                                     │ execute_pipeline(name, payload)
                                     ▼
                  ┌────────────────────────────────────────┐
                  │ DAG Executor                           │
                  │  - load YAML → Pipeline dataclass      │
                  │  - topological_layers (Kahn)           │
                  │  - asyncio.gather() per layer          │
                  └──────┬───────────────────────┬─────────┘
                         │                       │
                  ┌──────▼─────┐           ┌─────▼──────┐
                  │ Tool node  │           │ Agent node │
                  │ (sync fn)  │           │ (LLM call) │
                  └──────┬─────┘           └─────┬──────┘
                         │                       │
                         └───────────┬───────────┘
                                     ▼
                  ┌────────────────────────────────────────┐
                  │ orchestration.db (SQLite)              │
                  │  pipeline_runs + pipeline_events       │
                  └──────────────────┬─────────────────────┘
                                     │ publish()
                                     ▼
                  ┌────────────────────────────────────────┐
                  │ Event bus (in-memory queues)           │
                  │ → SSE → React frontend (live DAG view) │
                  └────────────────────────────────────────┘
```

Five files are load-bearing. If you read only these, you understand the framework:

| File | Role |
|---|---|
| `orchestration/api/dag_executor.py` | Topological scheduling + parallel execution + event persistence |
| `orchestration/api/pipeline_loader.py` | YAML → `Pipeline` / `PipelineNode` dataclasses |
| `orchestration/api/agent_registry.py` | String-name → callable registry (the SDK abstraction seam) |
| `orchestration/api/agnes_context.py` | The `AgnesContext` dataclass threaded through every node |
| `orchestration/api/routes/pipelines.py` | HTTP surface: trigger run, list runs, stream SSE, fetch graph |

---

## 2. Why this shape fits FinTech

| FinTech requirement | How Agnes satisfies it |
|---|---|
| Every decision must be **auditable** and **replayable** | Every node invocation is persisted as a `pipeline_event` with input context, output dict, elapsed_ms, timestamp. Replays are a SQL query. |
| Reasoning, assumptions, and tool outputs must be **visible** to humans | Outputs are JSON dicts written to `pipeline_events.data`. The frontend subscribes to `/runs/{id}/stream` (SSE) and renders each node's full output as it arrives. |
| Workflows must be **deterministic where possible**, LLM-driven only where judgment is needed | Two node kinds in one DAG. Hard rules (sanction screen, KYC field validation, limit checks) are tools. Narrative / explanation / triage are agents. |
| Workflow logic must be **changeable without code review** | Pipelines are YAML. A compliance officer can re-order, gate, or branch nodes without touching Python. |
| **No vendor lock-in** on LLM provider | Agents talk to providers behind the registry. Swapping Claude for Bedrock or OpenAI = adding one file + one registry line. No executor change. |
| **Graceful degradation** when an upstream is down | Each agent checks for its API key at runtime and returns `{"skipped": "..."}` instead of throwing. Pipelines keep running with partial results. |
| **Compound user intent** ("freeze this account AND open a SAR") | RouterAgent emits `secondary_intents`; the chat endpoint fans out additional pipeline runs in parallel and the frontend tracks all run IDs. |

---

## 3. Mapping the supply-chain example to a FinTech pipeline

The shipped repo runs `supplier_fallout.yaml`. A FinTech analogue might be `transaction_dispute.yaml`. The mechanics are identical — only the node names change:

| Supply-chain node | Plays the role of (FinTech) |
|---|---|
| `find-alternatives` (tool) | `pull-transaction-history` (tool: SQL on ledger) |
| `verify-entity` (GLEIF tool) | `verify-counterparty` (tool: KYC provider lookup) |
| `gate-compliance` (rule engine tool) | `screen-sanctions` (tool: OFAC/UN/EU list check) |
| `bom-impact` (tool) | `assess-exposure` (tool: position + limit calc) |
| `web-research` (LLM agent) | `external-context` (LLM agent: news/litigation lookup) |
| `format-rfqs` (tool) | `draft-customer-comms` (tool: template fill) |
| `write-proposal` (LLM agent) | `write-decision-memo` (LLM agent: narrative for committee) |

The DAG executor doesn't care about the domain. It just runs the layers, persists the events, and streams them out.

---

## 4. What's *not* in the framework (and you'll need to add)

This is a thin orchestration layer, intentionally. The following are **your responsibility** when adapting to FinTech:

- **AuthN / AuthZ** — the FastAPI surface in `orchestration/api/routes/` is open. Wrap with your IdP / mTLS / RBAC.
- **Data residency & PII handling** — `pipeline_events.data` stores full node output as JSON. For PII, either redact before write or move to an encrypted column / external store and keep only a reference in the event.
- **Tamper-evident audit log** — `orchestration.db` is plain SQLite. For SOX / DORA / MiFID II audit needs, mirror events to an append-only store (S3 Object Lock, QLDB, or hash-chained log).
- **Provider redundancy** — agents have one provider each today. For SLA-bound workflows, wrap the SDK call with a fallback chain (Claude → Bedrock-Claude → cached response).
- **Cost & rate-limit tracking** — token usage is not currently captured per event. Add a `tokens_in`/`tokens_out`/`cost_usd` field to `pipeline_events.data` inside each agent's `run()`.
- **Human-in-the-loop pause** — pipelines run end-to-end. For four-eyes / maker-checker patterns, add a `wait_for_approval` node type that parks the run state and resumes on a webhook.

These are clean extension points, not rewrites. The framework leaves room for them.

---

## 5. How to read the rest of this briefing pack

- **Briefing 02** — `02_executor_and_yaml.md` — DAG executor internals, YAML schema, deterministic vs LLM nodes, the `AgnesContext` data plane, conditions / gating, parallelism guarantees.
- **Briefing 03** — `03_orchestration_and_auditability.md` — RouterAgent (the meta-orchestrator), agent SDK abstraction, per-call lifecycle, persistence model, frontend streaming, what a FinTech audit trail needs on top.

Read in order. Each one assumes the previous.
