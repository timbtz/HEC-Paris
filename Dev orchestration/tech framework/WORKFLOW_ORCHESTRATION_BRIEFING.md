# Archon Workflow Orchestration: Architecture Briefing

> A meta-level guide to how Archon builds, runs, visualizes, and persists agentic workflows —
> and how the same pattern can be applied to other agent frameworks such as Google ADK.

---

## Table of Contents

1. [The Big Picture — What This Actually Solves](#1-the-big-picture)
2. [System Architecture at a Glance](#2-system-architecture-at-a-glance)
3. [The DAG Model — How Graphs Replace Scripts](#3-the-dag-model)
4. [Node Types and Their Roles](#4-node-types-and-their-roles)
5. [Execution Lifecycle](#5-execution-lifecycle)
6. [SQLite as the System Backbone](#6-sqlite-as-the-system-backbone)
7. [Real-Time Streaming — From Executor to Browser](#7-real-time-streaming)
8. [Frontend Visualization Stack](#8-frontend-visualization-stack)
9. [What Workflows Enable — The User Value Story](#9-what-workflows-enable)
10. [Human-in-the-Loop Patterns](#10-human-in-the-loop-patterns)
11. [The Default Workflow Library](#11-the-default-workflow-library)
12. [Google ADK — Honest Comparison and Integration Path](#12-google-adk)
13. [Applying This Pattern Elsewhere](#13-applying-this-pattern-elsewhere)

---

## 1. The Big Picture

### The Problem with Plain AI Agents

A raw AI agent is powerful but brittle in production:

- It runs in one unbroken context until it succeeds or crashes
- There is no way to resume halfway through a long task
- Steps that could run in parallel run sequentially
- Human review happens only at the start or end, never in the middle
- You cannot see *which step* is running or why something failed

Archon's workflow engine solves all of this by treating multi-step AI work as a **directed acyclic graph (DAG)** — the same model used by production data pipelines (Airflow, Prefect, Dagster), but applied to AI agent orchestration.

### The Meta Insight

> **Workflows are not prompts. They are programs where each node is an AI call, and the edges are data contracts.**

When you decompose a complex task into a DAG:
- Steps become observable (you can see status per node)
- Failures become resumable (re-run from the failed node, not from scratch)
- Independent steps become parallel (no artificial sequencing)
- Data flows explicitly between steps via `$nodeId.output` substitution
- Human judgment can be injected at any gate via approval or interactive loop nodes

This is the same conceptual leap that happened when data engineers moved from bash scripts to DAG orchestrators — except here the "tasks" are AI agents, not SQL queries.

---

## 2. System Architecture at a Glance

```
┌──────────────────────────────────────────────────────────────────────────────┐
│                           ARCHON SYSTEM LAYERS                               │
├──────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│  DEFINITION LAYER                                                            │
│  .archon/workflows/*.yaml                                                    │
│  Zod schema validation + Kahn's cycle detection at load time                 │
│                                                                              │
│                               │                                              │
│                               ▼                                              │
│  ORCHESTRATION LAYER          packages/workflows/src/                        │
│  executor.ts → dag-executor.ts                                               │
│    buildTopologicalLayers()  →  Promise.allSettled() per layer               │
│    $nodeId.output substitution  ·  when:/trigger_rule evaluation             │
│    retry with exponential backoff  ·  cancel/heartbeat checks                │
│                                                                              │
│          ┌────────────────────┼────────────────────┐                        │
│          ▼                    ▼                     ▼                        │
│  AI CLIENT LAYER       EVENT LAYER          PERSISTENCE LAYER               │
│  Claude Agent SDK      In-memory            ┌────────────────────────────┐  │
│  Codex SDK             EventEmitter         │   ~/.archon/archon.db      │  │
│  IAssistantClient      (zero latency)       │   SQLite  (default)        │  │
│                            │                │   ─────────────────────    │  │
│                            │  parallel      │   workflow_runs            │  │
│                            │  dual write    │   workflow_events          │  │
│                            │  ↓ also ──────►   conversations            │  │
│                            │                │   sessions                 │  │
│                            ▼                │   isolation_environments   │  │
│                        SSE bridge           │   codebases                │  │
│                        → SSETransport       │   codebase_env_vars        │  │
│                        → buffer / replay    │   messages                 │  │
│                            │                └────────────┬───────────────┘  │
│                            │                             │ read (REST)      │
│                            ▼                             ▼                  │
│  VISUALIZATION LAYER       packages/web/src/                                │
│  React Flow v12 (graph canvas)    Dagre (auto-layout)                       │
│  Zustand (live state from SSE)    TanStack Query (REST polling + history)   │
│  ExecutionDagNode (status colours)  WorkflowDagViewer (minimap, badges)     │
│                                                                              │
└──────────────────────────────────────────────────────────────────────────────┘

SQLite serves two roles simultaneously:
  DURABLE STORE  — every node transition written as an event row (resume, history, audit)
  READ SOURCE    — REST endpoints query it to serve the frontend on load and poll
The in-memory EventEmitter bypasses it only for latency — SQLite is the ground truth.
```

---

## 3. The DAG Model

### What a DAG Is

A **directed acyclic graph** is a set of nodes connected by directed edges (A → B means "B depends on A") with no cycles. Execution proceeds in **topological order**: a node starts only after all its declared dependencies have completed.

### How Archon Builds the Graph

1. **Parse YAML** — each node declares `depends_on: [nodeA, nodeB]`
2. **Kahn's algorithm** groups nodes into layers — each layer contains nodes whose dependencies are all in prior layers
3. Each layer is executed with `Promise.allSettled()` — all nodes in a layer run concurrently
4. Outputs flow forward: `$nodeId.output` is substituted into downstream prompts before execution

```
Example 4-node workflow:

  [fetch-issue]           Layer 0 — runs first
       │
  [classify]              Layer 1 — runs after fetch-issue
       │
  ┌────┴────┐
[investigate] [document]  Layer 2 — runs in PARALLEL
  └────┬────┘
  [create-pr]             Layer 3 — waits for BOTH to finish
```

### Conditional Branches — `when:` and `trigger_rule`

```
  [classify]  ──── output: { type: "bug" } ────►  [investigate]
       │                                            when: "$classify.output.type == 'bug'"
       └───────────────────────────────────────►  [plan-feature]
                                                    when: "$classify.output.type == 'feature'"
```

- `when:` evaluates a condition against upstream outputs; node is **skipped** if false
- `trigger_rule` controls what counts as "ready" when multiple dependencies exist:
  - `all_success` — all dependencies must succeed (default)
  - `all_done` — all must settle (any status), useful for cleanup/always-run nodes
  - `one_success` — at least one dependency succeeded
  - `none_failed_min_one_success` — at least one success, zero failures

---

## 4. Node Types and Their Roles

| Type | What It Does | Output Available As |
|------|-------------|-------------------|
| `prompt` | Inline AI prompt, streams response | `$nodeId.output` |
| `command` | Loads prompt from `.archon/commands/` file | `$nodeId.output` |
| `bash` | Runs a shell script, no AI | `$nodeId.output` (stdout) |
| `script` | Runs TypeScript (bun) or Python (uv) | `$nodeId.output` (stdout) |
| `loop` | Iterative AI execution until a signal string appears | `$nodeId.output` (final iteration) |
| `approval` | Pauses workflow for human review/input | `$nodeId.output` (user's comment, if `capture_response: true`) |
| `cancel` | Terminates the workflow with a reason message | — |

### Key Node Capabilities (AI nodes only)

- `output_format` — structured JSON output schema; AI is instructed to conform; output is parsed and available for field-level access (`$nodeId.output.field`)
- `context: fresh | shared` — whether this node continues the prior conversation session or starts fresh
- `allowed_tools / denied_tools` — per-node tool restrictions
- `model / provider` — override the workflow-level AI model per node
- `retry` — up to 5 retries with exponential backoff and error classification (transient vs fatal)

---

## 5. Execution Lifecycle

```
User triggers workflow (Slack / Telegram / GitHub / Web UI / CLI)
        │
        ▼
Platform adapter
  └─► DB: upsert remote_agent_conversations
  └─► DB: INSERT remote_agent_sessions
        │
        ▼
executor.ts: executeWorkflow()
  ├─ DB: SELECT workflow_runs WHERE working_path = ? AND status = 'running'
  │       → prevent duplicate run at same path
  │
  ├─ DB: SELECT workflow_runs WHERE workflow_name = ? AND working_path = ?
  │         AND status IN ('failed','paused')
  │       → detect resumable prior run
  │       If found:
  │         DB: SELECT workflow_events WHERE event_type = 'node_completed'
  │             → Map<nodeId, output> pre-loaded into nodeOutputs
  │
  ├─ DB: INSERT remote_agent_workflow_runs  (status: 'running')
  │       → runId assigned; used for every subsequent write
  │
  ├─ Filesystem: mkdir $ARTIFACTS_DIR
  │
  └─ dag-executor.ts: executeDagWorkflow()
        │
        ├─ buildTopologicalLayers()  [Kahn's algorithm, pure in-memory]
        │
        └─ For each topological layer:
              │
              ├─ Evaluate trigger_rule (skip node if dependencies not satisfied)
              ├─ Evaluate when: condition  (skip node if expression is false)
              │     └─► DB: INSERT workflow_events (node_skipped) if skipped
              │
              ├─ nodeOutputs.has(node.id)?
              │     └─► DB: INSERT workflow_events (node_skipped_prior_success)
              │           skip — already done in prior run
              │
              └─ Execute all nodes in layer via Promise.allSettled():
                    │
                    ├─► DB: INSERT workflow_events (node_started)
                    │   SSE: WorkflowEventEmitter.emit(node_started)  ← browser sees it
                    │
                    │   [AI streaming begins]
                    │     every 60s: DB UPDATE workflow_runs SET last_activity_at = now()
                    │     every 10s: DB SELECT status FROM workflow_runs
                    │                   → abort stream if not 'running'
                    │
                    ├─► DB: INSERT workflow_events (node_completed | node_failed)
                    │   SSE: WorkflowEventEmitter.emit(node_completed | node_failed)
                    │
                    └─ nodeOutputs.set(nodeId, output)  [in-memory for substitution]
        │
        └─ DB: UPDATE workflow_runs SET status = 'completed' | 'failed', completed_at = now()
              SSE: WorkflowEventEmitter.emit(workflow_completed | workflow_failed)
```

---

## 6. SQLite as the System Backbone

SQLite is not a convenience default — it is the foundational state store that makes every other capability possible: resumable runs, observable node status, the frontend DAG visualization, and the SSE real-time stream. Understanding it precisely explains why the rest of the system works the way it does.

### The Database File

SQLite lives at a single file: `~/.archon/archon.db`. The connection is a **process-level singleton** — one `Database` instance shared by the entire server process. Auto-detection happens at first access:

```
DATABASE_URL env var set?  →  PostgreSQL (production / Docker)
No DATABASE_URL?           →  SQLite at ~/.archon/archon.db (default, zero setup)
```

The `SqliteAdapter` constructor opens the file and immediately sets three PRAGMAs that determine its behaviour under load:

```
PRAGMA journal_mode = WAL    → Write-Ahead Logging: concurrent readers never block writers
PRAGMA busy_timeout = 5000   → If the DB is locked, retry for up to 5 seconds before error
PRAGMA foreign_keys = ON     → Enforce FK constraints (cascade deletes, etc.)
```

WAL mode matters here because the executor and the SSE route can be reading the DB simultaneously during a live workflow run. Without WAL, a write from the executor would block the server's read for the REST endpoint.

After the PRAGMAs, the adapter runs a single `CREATE TABLE IF NOT EXISTS` block for all 8 tables, plus `ALTER TABLE` migrations for any columns added after the initial schema. The result: the schema self-initialises on every startup with no migration runner or version tracking needed.

### The Abstraction Stack

Every part of the system reaches SQLite through the same chain. Nothing calls `bun:sqlite` directly except the adapter:

```
┌──────────────────────────────────────────────────────────────────────┐
│  Application layer                                                   │
│  executor · orchestrator · platform adapters · Hono route handlers  │
│  — never writes raw SQL                                              │
└──────────────────────────┬───────────────────────────────────────────┘
                           │ e.g. createWorkflowEvent(), getWorkflowRun()
┌──────────────────────────▼───────────────────────────────────────────┐
│  DB module functions   packages/core/src/db/                         │
│  workflows.ts  workflow-events.ts  conversations.ts  sessions.ts     │
│  — own all SQL strings; callers never see a query                    │
└──────────────────────────┬───────────────────────────────────────────┘
                           │ pool.query("SELECT ...", [$1, $2, ...])
┌──────────────────────────▼───────────────────────────────────────────┐
│  IDatabase / pool   packages/core/src/db/connection.ts               │
│  — process singleton; auto-selects adapter on first call             │
└──────────────────────────┬───────────────────────────────────────────┘
                           │ convertPlaceholders($1 → ?) + bun:sqlite
┌──────────────────────────▼───────────────────────────────────────────┐
│  SqliteAdapter   packages/core/src/db/adapters/sqlite.ts             │
│  — bun:sqlite Database · WAL · busy_timeout · stmt.all/stmt.run      │
└──────────────────────────┬───────────────────────────────────────────┘
                           │
                    ~/.archon/archon.db
```

One deliberate gap in this chain: the executor package (`@archon/workflows`) has no direct dependency on `@archon/core`. Instead it receives an `IWorkflowStore` interface via dependency injection. `createWorkflowStore()` in `@archon/core` bridges the two, forwarding each interface method to the appropriate DB module function. This means the workflow engine is fully portable — the same executor code runs against SQLite locally and PostgreSQL in production without a single change.

### The 8 Tables and Who Uses Each One

All table names carry the `remote_agent_` prefix.

```
┌──────────────────────────────────────┬──────────────────────────────────────────────┐
│ Table                                │ Role and primary consumers                   │
├──────────────────────────────────────┼──────────────────────────────────────────────┤
│ codebases                            │ Repository registry. Executor reads cwd,     │
│                                      │ branch, and env vars per project.            │
├──────────────────────────────────────┼──────────────────────────────────────────────┤
│ codebase_env_vars                    │ Per-project env vars injected into the AI    │
│                                      │ subprocess. Managed via Web UI.              │
├──────────────────────────────────────┼──────────────────────────────────────────────┤
│ conversations                        │ Platform conversation index. One row per     │
│                                      │ Slack thread / Telegram chat / GitHub issue  │
│                                      │ / Web UI session. Soft-deleted (deleted_at). │
├──────────────────────────────────────┼──────────────────────────────────────────────┤
│ sessions                             │ AI SDK session tracking with full audit      │
│                                      │ chain: parent_session_id + transition_reason │
│                                      │ per transition. Orchestrator reads this to   │
│                                      │ resume a prior context window.               │
├──────────────────────────────────────┼──────────────────────────────────────────────┤
│ isolation_environments               │ Git worktree tracking. One row per live      │
│                                      │ worktree, with working_path and branch_name. │
│                                      │ Partial unique index enforces one active     │
│                                      │ worktree per workflow+codebase pair.         │
├──────────────────────────────────────┼──────────────────────────────────────────────┤
│ workflow_runs                        │ One row per workflow execution. Holds status,│
│                                      │ working_path (resume key), last_activity_at  │
│                                      │ (heartbeat for orphan detection), and        │
│                                      │ metadata JSON (approval context, cost).      │
├──────────────────────────────────────┼──────────────────────────────────────────────┤
│ workflow_events                      │ Append-only event log. One row per step      │
│                                      │ transition. Powers the frontend DAG status   │
│                                      │ view and the resume mechanism.               │
├──────────────────────────────────────┼──────────────────────────────────────────────┤
│ messages                             │ Conversation message history for the Web UI  │
│                                      │ chat panel. Tool call metadata stored as     │
│                                      │ JSON in the metadata column.                 │
└──────────────────────────────────────┴──────────────────────────────────────────────┘
```

### The workflow_runs and workflow_events Tables in Detail

These two tables are the core of the orchestration system. Everything else exists to support them.

```
remote_agent_workflow_runs
─────────────────────────────────────────────────────────────
id                TEXT PK        — UUID, generated at INSERT
workflow_name     TEXT NOT NULL  — name from the YAML file
conversation_id   TEXT FK        — links to the triggering conversation
codebase_id       TEXT FK        — links to the repo (for env vars, cwd)
user_message      TEXT           — the message that triggered the run
status            TEXT           — pending|running|completed|failed|paused|cancelled
working_path      TEXT           — filesystem path; the resume lookup key
metadata          TEXT           — JSON blob (approval context, total_cost_usd, etc.)
started_at        TEXT           — ISO timestamp
completed_at      TEXT           — set when terminal status reached
last_activity_at  TEXT           — updated every 60s during execution (heartbeat)
parent_conversation_id TEXT FK   — set for background worker runs in the Web UI

Indexes: status, conversation_id, parent_conversation_id, working_path
Partial index: last_activity_at WHERE status = 'running'  ← fast orphan detection
```

```
remote_agent_workflow_events
─────────────────────────────────────────────────────────────
id               TEXT PK        — UUID
workflow_run_id  TEXT FK        — ON DELETE CASCADE
event_type       TEXT           — see event types below
step_name        TEXT           — DAG node ID (null for workflow-level events)
step_index       INTEGER        — legacy; unused in DAG workflows
data             TEXT           — JSON blob, event-specific payload
created_at       TEXT           — ISO timestamp; determines event order

Indexes: workflow_run_id, event_type
```

**JSON in SQLite:** Both `metadata` and `data` are stored as `TEXT` containing JSON. PostgreSQL stores the same fields as `JSONB` (parsed). The adapter normalises this at read time — `typeof row.data === 'string' ? JSON.parse(row.data) : row.data` — so the rest of the application always receives a JavaScript object, never a raw string.

### Event Types Written During Execution

| Event Type | When Written | Key `data` Fields |
|---|---|---|
| `node_started` | Node begins executing | `{ command, provider }` |
| `node_completed` | Node succeeds | `{ duration_ms, node_output, cost_usd, stop_reason, num_turns }` |
| `node_failed` | Node errors | `{ error, duration_ms }` |
| `node_skipped` | Condition false or trigger rule | `{ reason: "when_condition" \| "trigger_rule" \| "prior_success" }` |
| `loop_iteration_started` | Loop iteration begins | `{ iteration, max_iterations }` |
| `loop_iteration_completed` | Iteration succeeds | `{ duration_ms, iteration }` |
| `loop_iteration_failed` | Iteration errors | `{ error, iteration }` |
| `tool_called` | AI invokes a tool | `{ tool_name, tool_input }` |
| `tool_completed` | Tool execution finishes | `{ tool_name, duration_ms }` |
| `approval_requested` | Approval gate pauses run | `{ message, iteration? }` |
| `workflow_completed` | Entire run succeeds | `{ duration_ms }` |
| `workflow_cancelled` | User cancels | `{ reason }` |

### Exact Write Sequence for a Two-Node Run

This is the literal sequence of SQL statements the DB sees for a workflow with nodes `fetch-issue → implement`:

```
1. User message received
   INSERT into remote_agent_conversations (upsert)
   INSERT into remote_agent_sessions

2. Executor starts
   INSERT into remote_agent_workflow_runs
     (workflow_name='fix-issue', status='pending', working_path='/path/worktree')
   → returns the runId used for every subsequent write

3. Node "fetch-issue" starts
   INSERT into remote_agent_workflow_events
     (event_type='node_started', step_name='fetch-issue',
      data='{"command":null,"provider":"claude"}')

4. During AI streaming — every 60 seconds:
   UPDATE remote_agent_workflow_runs
     SET last_activity_at = datetime('now') WHERE id = ?

5. During AI streaming — every 10 seconds:
   SELECT status FROM remote_agent_workflow_runs WHERE id = ?
   (if not 'running' → abort the stream)

6. Node "fetch-issue" completes
   INSERT into remote_agent_workflow_events
     (event_type='node_completed', step_name='fetch-issue',
      data='{"duration_ms":4200,"node_output":"Issue body...","cost_usd":0.03}')

7. Node "implement" starts, streams, completes
   (same INSERT pattern — node_started, then node_completed)

8. Workflow finishes
   UPDATE remote_agent_workflow_runs
     SET status='completed', completed_at=datetime('now') WHERE id = ?
```

Every INSERT into `workflow_events` is **fire-and-forget**: if the write fails, the error is logged but the executor continues. Workflow execution must never fail because of an event logging failure.

### Dual-Layer Persistence Strategy

The DB stores the minimal set of facts needed for the UI and resume. Verbose content never enters it:

```
                 ┌──────────────────────────────────────────┐
  AI responses   │  JSONL log file per run                  │
  Tool payloads  │  ~/.archon/logs/{runId}.jsonl            │  ← Full detail, large
  Stream chunks  │  (filesystem only, never in DB)          │
                 └──────────────────────────────────────────┘

                 ┌──────────────────────────────────────────┐
  Status changes │  remote_agent_workflow_events            │
  Node outputs   │  (lean, ~7–15 rows per node)             │  ← What the UI reads
  Durations      │                                          │
  Errors         └──────────────────────────────────────────┘
```

The `node_output` field in each `node_completed` event contains the AI's final response text for that node — concise enough to store in the DB, and exactly what downstream nodes need for `$nodeId.output` substitution.

### How the Frontend Reads from SQLite

The Web UI never touches the database directly. It sends HTTP requests to the Hono server, which queries the DB modules:

```
Browser: GET /api/workflows/runs/{runId}
  │
  ▼ Hono route handler
  │
  ▼ workflowDb.getWorkflowRun(runId)
    SELECT * FROM remote_agent_workflow_runs WHERE id = ?
  │
  ▼ workflowEventDb.listWorkflowEvents(runId)
    SELECT * FROM remote_agent_workflow_events
    WHERE workflow_run_id = ? ORDER BY created_at ASC
  │
  ▼ JSON response → frontend maps event_type to node status
    → React Flow nodes rendered with completed/failed/skipped colours
```

On page load, the full event history reconstructs the DAG state. From then on, **live updates arrive via SSE** — SQLite is not polled for in-progress status changes.

### The SSE Dual-Write: Speed and Durability Together

Every node state change triggers two parallel writes that serve different purposes:

```
Node completes
       │
       ├──► pool.query("INSERT INTO remote_agent_workflow_events ...")
       │    [async, fire-and-forget → SQLite]
       │    Purpose: durable record, powers resume and history
       │
       └──► WorkflowEventEmitter.emit({ type: 'node_completed', ... })
            [synchronous in-memory dispatch]
            Purpose: zero-latency path to the browser
                 │
                 ▼
           WorkflowEventBridge maps to SSE-friendly JSON
                 │
                 ▼
           SSETransport
           ┌──────────────────────────────────────────────┐
           │  Client connected?  → res.write() in <10ms   │
           │  Client offline?    → buffer in memory        │
           │                       max 500 events, 60s TTL │
           │                       replayed on reconnect   │
           └──────────────────────────────────────────────┘
```

SQLite is the durable backbone. The in-memory emitter is the speed path. If the browser disconnects mid-run, events buffer in memory and replay when it reconnects. On a full page refresh, the browser re-fetches from SQLite via REST to get the authoritative history.

### The Resume Mechanism — SQLite as Checkpoint Store

The most important capability that SQLite enables is node-level resume. When a workflow fails at node 6 of 10, re-triggering it skips nodes 1–5 entirely:

```
Step 1 — Find a resumable run
  SELECT * FROM remote_agent_workflow_runs
  WHERE workflow_name = ?
    AND working_path = ?
    AND status IN ('failed', 'paused')
  ORDER BY started_at DESC LIMIT 1

Step 2 — Load what already completed
  SELECT step_name, data
  FROM remote_agent_workflow_events
  WHERE workflow_run_id = ?
    AND event_type = 'node_completed'
  ORDER BY created_at ASC

  → Returns Map<nodeId, outputText>
    e.g. { "fetch-issue" → "Issue body...", "classify" → "bug" }

Step 3 — Pre-populate the executor's output map
  nodeOutputs.set("fetch-issue", "Issue body...")
  nodeOutputs.set("classify", "bug")

Step 4 — Execute the DAG
  For each node in topological order:
    if nodeOutputs.has(node.id)  →  skip, emit node_skipped_prior_success
    else                         →  execute normally
```

The `node_output` value retrieved from `workflow_events` is substituted as `$nodeId.output` in downstream prompts — exactly as if the node had just run. The resumed workflow is indistinguishable from a fresh run from the perspective of every node that follows.

### End-to-End: One Message, Every Database Touch

```
User sends message
  └─► INSERT / upsert remote_agent_conversations
  └─► INSERT remote_agent_sessions

Executor creates run
  └─► INSERT remote_agent_workflow_runs  (status: 'running')

[Node A executes]
  └─► INSERT remote_agent_workflow_events  (node_started, step_name='A')
  │   every 60s: UPDATE workflow_runs SET last_activity_at = now()
  │   every 10s: SELECT status FROM workflow_runs  (cancellation check)
  └─► INSERT remote_agent_workflow_events  (node_completed, step_name='A')
  └─► [SSE fires in parallel — browser sees update in <10ms]

[Nodes B and C execute in parallel — same INSERT pattern each]

Executor finishes
  └─► UPDATE remote_agent_workflow_runs  (status: 'completed', completed_at = now())
  └─► [SSE fires workflow_completed — browser marks run done]

Browser loads history later
  └─► SELECT remote_agent_workflow_runs WHERE id = ?
  └─► SELECT remote_agent_workflow_events WHERE workflow_run_id = ? ORDER BY created_at
  └─► React Flow re-renders DAG with historical node states and durations
```

SQLite is touched at every significant moment of a workflow's life. It records the run's existence, tracks its heartbeat during execution, logs every step transition for observability, stores node outputs for inter-node data flow and resume, and serves historical state to the frontend after completion. The in-memory emitter and SSE layer exist purely to reduce latency during live execution — they are disposable. SQLite is the ground truth.

---

## 7. Real-Time Streaming

### The Event Path: Executor → Browser

```
DAG Executor (dag-executor.ts)
  │
  ├─► deps.store.createWorkflowEvent()     [ASYNC, fire-and-forget → DB write]
  │
  └─► WorkflowEventEmitter.emit()          [IN-MEMORY, synchronous fan-out]
            │
            ▼
      WorkflowEventBridge (server package)
      subscribes to emitter, maps events to SSE-friendly JSON
            │
            ▼
      SSETransport
      ┌─────────────────────────────────────────────────┐
      │  Active stream?  → write immediately            │
      │  No stream?      → buffer (max 500 events,      │
      │                    60s TTL; replayed on connect) │
      └─────────────────────────────────────────────────┘
            │
            ▼
      GET /api/stream/:conversationId    (Hono SSE route)
      GET /api/stream/__dashboard__      (all runs, for dashboard view)
            │
            ▼
      Browser EventSource
      useSSE hook → dispatch to Zustand workflow-store
            │
            ▼
      WorkflowDagViewer re-renders (React Flow nodes get new status)
```

### SSE Event Mapping

Internal emitter events are translated to a smaller set of UI-facing SSE types:

| Internal Event | SSE Type | Key Fields Sent |
|---|---|---|
| `workflow_started/completed/failed` | `workflow_status` | `status`, `runId`, `workflowName` |
| `node_started/completed/failed/skipped` | `dag_node` | `name`, `status`, `duration`, `error`, `reason` |
| `loop_iteration_*` | `workflow_step` | `iteration`, `status`, `duration` |
| `tool_started/completed` | `workflow_tool_activity` | `toolName`, `status`, `durationMs` |
| `approval_pending` | `workflow_status` | `status: 'paused'`, `approval: {nodeId, message}` |

---

## 8. Frontend Visualization Stack

### Technology Choices

| Concern | Library | Why |
|---|---|---|
| Graph canvas | React Flow v12 (`@xyflow/react`) | Handles node/edge rendering, selection, pan/zoom, minimap natively |
| Graph layout | Dagre (`@dagrejs/dagre`) | Auto-positions nodes in topological order (top-to-bottom); no manual coordinates |
| Live state | Zustand | Simple `Map<runId, WorkflowState>` store; SSE events flow directly in as mutations |
| REST sync | TanStack Query | 3-second polling while run is active; authoritative for historical data |
| Visual editor | React Flow (same library) | Builder and viewer reuse the same graph primitives |

### Layout Algorithm

Dagre takes the node list and edge list from the workflow definition and computes pixel coordinates:

```
Input:  DagNode[] with depends_on edges
Output: { x, y } positions for each node

Settings used:
  rankdir: 'TB'     → top-to-bottom flow
  ranksep: 80px     → vertical gap between layers
  nodesep: 40px     → horizontal gap between siblings
  node size: 180×80px
```

Layout is computed once (static topology) and merged with live status updates from SSE.

### Node Status → Visual Style

```
  pending   [ gray border, flat background        ]
  running   [ blue border, blue glow, spinner icon ] ← animating
  completed [ green border, green tint background  ]
  failed    [ red border,  red tint,  error text   ]
  skipped   [ gray border, 50% opacity             ]
```

### State Merge Strategy

The frontend maintains two data streams:
- **REST** (`/api/workflows/runs/{runId}`) — authoritative for structure, history, artifacts
- **SSE** (`/api/stream/{conversationId}`) — authoritative for live status

They are merged per render: REST provides the workflow definition + historical events; SSE updates override node status in real time. If SSE is disconnected, REST polling catches up on the next 3-second tick.

---

## 9. What Workflows Enable

### The Core Value: Automation With Human Judgment Preserved

Archon workflows bridge two previously incompatible goals:

**Full automation** — AI runs a sequence of steps (fetch issue → classify → investigate → implement → test → PR) without human intervention at each step.

**Human oversight** — At critical junctures (plan approved? implementation looks right?), the workflow pauses and waits for a human to confirm, provide feedback, or reject.

This is not a chatbot. It is closer to a **CI/CD pipeline where some stages are AI agents** and others are human review gates.

### The Three Workflow Patterns

```
PATTERN 1: Straight-Line Automation
  [A] → [B] → [C] → [D]
  Use when: Steps are well-defined, no branching, minimal risk
  Example: fetch-issue → classify → fix → create-pr

PATTERN 2: Conditional Branching
  [classify] → [investigate] (when: type == 'bug')
             → [plan]        (when: type == 'feature')
  Use when: Work differs based on classification output
  Example: archon-fix-github-issue

PATTERN 3: Iterative Refinement with Human Gates
  [explore ←→ user] → [plan] → [plan ←→ user] → [implement loop] → [review ←→ user]
  Use when: Requirements are unclear upfront or quality must be user-validated
  Example: archon-piv-loop (Plan-Implement-Validate)
```

### What You Get That You Cannot Build With Plain Prompts

| Capability | Plain Agent | DAG Workflow |
|---|---|---|
| Parallel steps | No | Yes — `Promise.allSettled` per layer |
| Resume on failure | No | Yes — skip completed nodes from DB |
| Conditional branches | Embedded in prompt | Explicit `when:` condition |
| Human review mid-run | Not structured | `approval` / interactive `loop` nodes |
| Per-step observability | None | Node-level status, duration, cost |
| Cost tracking | None | Aggregated `total_cost_usd` per run |
| Artifact management | None | `$ARTIFACTS_DIR` per run |
| Isolated git branch | None | Automatic worktree per run |

---

## 10. Human-in-the-Loop Patterns

### Pattern A: Approval Gate

```yaml
- id: review-plan
  approval:
    message: "Review the plan above. Approve to continue, or reject with feedback."
    capture_response: true   # stores user comment as $review-plan.output
    on_reject:
      prompt: "User rejected the plan. Feedback: $REJECTION_REASON. Revise the plan."
      max_attempts: 3
```

User approves → workflow continues. User rejects with feedback → AI revises, presents again (up to 3 times).

### Pattern B: Interactive Loop Gate

```yaml
- id: explore
  loop:
    prompt: |
      Prior user input: $LOOP_USER_INPUT
      Ask targeted questions about the feature...
    until: PLAN_READY
    max_iterations: 15
    interactive: true
    gate_message: "Answer the questions above, or say 'ready' to proceed."
```

AI asks questions → pauses → user answers (becomes `$LOOP_USER_INPUT`) → AI processes → asks more or exits when it sees `PLAN_READY` in its own output.

### Pattern C: Fresh-Context Implementation Loop

```yaml
- id: implement
  loop:
    prompt: |
      Read $ARTIFACTS_DIR/tasks.md. Find the next unfinished task.
      Implement it. Mark it done in the file. If all tasks done, emit COMPLETE.
    until: COMPLETE
    max_iterations: 20
    fresh_context: true   # Each iteration starts a new AI session
```

Each iteration: AI reads the task list from disk, picks the next task, implements it, marks it done. Fresh context prevents the AI from carrying forward state from previous tasks. This is how Archon implements long multi-task implementation runs without context window exhaustion.

---

## 11. The Default Workflow Library

Archon ships 13 production workflows covering the full development lifecycle:

| Workflow | Pattern | What It Does |
|---|---|---|
| `archon-assist` | Single node | Fallback: full AI agent for anything that doesn't match a workflow |
| `archon-piv-loop` | Iterative + gates | Plan → Implement → Validate with human approval at each phase |
| `archon-interactive-prd` | Interactive loop | Guided PRD creation with deep-dive questions |
| `archon-feature-development` | Straight-line | Implement a pre-existing plan → PR |
| `archon-fix-github-issue` | Conditional DAG | Fetch → classify → investigate or plan → implement → PR |
| `archon-validate-pr` | Parallel | Parallel code review + E2E test on both main and feature branches |
| `archon-smart-pr-review` | Parallel | Quick review with optional specialized agents |
| `archon-comprehensive-pr-review` | Parallel | 5 parallel review agents → synthesis → auto-fix |
| `archon-resolve-conflicts` | Straight-line | Detect and resolve merge conflicts |
| `archon-adversarial-dev` | Loop | GAN-style: Generator builds, Evaluator attacks, loop until quality passes |
| `archon-remotion-generate` | Straight-line | Generate video via Remotion from prompt |
| `archon-create-issue` | Straight-line | Create GitHub issue with full context |
| `archon-workflow-builder` | Straight-line | Generate a new workflow YAML from intent |

The router sends user messages to the most appropriate workflow via an LLM classification step. If no workflow matches, `archon-assist` handles it.

---

## 12. Google ADK

### What Google ADK Actually Is

Google ADK (Agent Development Kit) is an open-source, code-first framework for building AI agents, available in Python, TypeScript, Go, and Java. It includes a runtime, CLI (`adk web`, `adk run`), a built-in web dev UI with trace inspection, and deployment integrations (Cloud Run, Vertex AI Agent Engine). It is model-agnostic but optimized for Gemini.

Sources: [adk.dev](https://adk.dev/), [github.com/google/adk-python](https://github.com/google/adk-python)

### ADK's Orchestration Model

ADK uses a **tree-structured hierarchy**, not a DAG. Its primitives are:

| Primitive | Behavior |
|---|---|
| `LlmAgent` | Uses an LLM for reasoning, routing, and tool invocation |
| `SequentialAgent` | Runs sub-agents one after another |
| `ParallelAgent` | Runs sub-agents concurrently; they share `session.state` |
| `LoopAgent` | Repeats sub-agents until `max_iterations` or an agent escalates |

Data passes between agents via a shared flat key-value `session.state` dict. An agent declares `output_key="result"` and downstream agents reference `{result}` in their instructions via template substitution.

### ADK vs. Archon: Direct Comparison

| Dimension | Google ADK | Archon Workflows |
|---|---|---|
| **Graph model** | Implicit tree hierarchy; no arbitrary edges | Explicit DAG with `depends_on` edges |
| **Routing** | LLM-driven (`transfer_to_agent`) or deterministic primitives | Deterministic topological sort + optional `when:` LLM classification |
| **Data passing** | Flat `session.state` dict, `output_key` convention | `$nodeId.output` string substitution with JSON field access |
| **Parallelism** | `ParallelAgent` primitive | Any set of nodes in the same topological layer |
| **Conditional branches** | Not declarative; must be modeled as LLM routing or custom logic | `when:` condition string on any node |
| **State persistence** | `SessionService` (in-memory / DB / Vertex AI) | `workflow_runs` + `workflow_events` DB tables |
| **Resume on failure** | Session-level history; no node-level checkpoint/skip | Node-level: completed nodes are skipped on re-run |
| **Human-in-the-loop** | Not a built-in primitive; must be implemented manually | First-class `approval` and interactive `loop` node types |
| **Visualization** | Built-in `adk web` with trace/graph view | React Flow DAG visualization with live SSE status per node |
| **Artifact management** | Documented but scope unclear | `$ARTIFACTS_DIR` per run, downloadable from Web UI |
| **Single-parent constraint** | Yes — agent instances cannot be reused across parent contexts | No — workflow definition is data, not object instances |
| **DAG with arbitrary edges** | Not supported natively | Core feature |

### ADK's Genuine Strengths

- Native multi-language support (Python, TypeScript, Go, Java)
- First-class Gemini integration with Vertex AI deployment
- Built-in evaluation framework for comparing expected vs actual outputs
- `LlmAgent` with `transfer_to_agent` makes LLM-driven routing trivial to set up
- Session management (create, retrieve, persist, scope state) is handled out of the box

### Where ADK Falls Short for Complex Orchestration

1. **No DAG**: Cannot declare `node_b depends_on [node_a, node_c]` without nesting sequential/parallel primitives manually, which quickly becomes unwieldy
2. **No conditional edges**: Branching requires an `LlmAgent` to route or custom `BaseAgent` logic — not a declarative `when:` condition
3. **No node-level resume**: If a 10-step `SequentialAgent` fails at step 7, there is no built-in mechanism to skip steps 1–6 on retry
4. **Flat state is untyped**: `session.state` is a dict; there is no schema enforcement across agents
5. **`ParallelAgent` state contention**: Concurrent sub-agents share the same state dict with no built-in conflict resolution
6. **No approval gate primitive**: Human-in-the-loop requires implementing a custom `BaseAgent` that waits for external input

---

## 13. Applying This Pattern Elsewhere

### The Pattern Is Framework-Agnostic

Every component of Archon's orchestration approach can be implemented on top of any AI SDK, including Google ADK:

```
WHAT YOU NEED TO BUILD:

1. SCHEMA LAYER
   Define node types, depends_on, when, trigger_rule as data (JSON/YAML/Zod)

2. GRAPH LAYER
   Kahn's algorithm → topological layers
   Promise.allSettled (or equivalent) per layer
   $nodeId.output substitution before execution

3. EXECUTION ADAPTERS
   One function per node type that calls your AI SDK
   (Google ADK: runner.run_async() | Archon: aiClient.sendQuery())

4. PERSISTENCE LAYER
   Two tables: runs (status) + events (step-level facts)
   Resume query: SELECT node_output WHERE event_type = 'node_completed'

5. EVENT BUS
   In-memory emitter → SSE route → browser
   Buffer events when client is disconnected

6. VISUALIZATION LAYER
   React Flow + Dagre (copy Archon's dag-layout.ts directly)
   Zustand store receiving SSE events
   Node component rendering status colors
```

### Using Google ADK as the Execution Adapter

When integrating ADK into a DAG executor, each AI node in the DAG calls ADK's runner:

```
For each DAG node of type 'agent':
  1. Substitute $nodeId.output refs into the agent's instruction template
  2. Create or resume an ADK session (using ADK's SessionService)
  3. Call runner.run_async(user_id, session_id, message) → async for event
  4. Collect final response text → write to nodeOutputs map
  5. Write node_completed event to your DB
  6. Fire your event emitter → SSE → browser
```

ADK's `session.state` becomes the mechanism for passing structured data between sub-agents within a single ADK invocation, while `$nodeId.output` handles data flow between DAG nodes across ADK invocations.

### What You Retain From ADK

- All of ADK's tool ecosystem (Google Search, Code Execution, etc.)
- ADK's `LlmAgent` + `transfer_to_agent` for nodes that need internal LLM routing
- ADK's `SessionService` for within-node conversation history
- ADK's `adk web` UI for trace-level debugging of individual node invocations
- ADK's evaluation framework for testing individual agents

### What You Add on Top

- DAG topology with arbitrary dependency edges
- `when:` conditional node execution
- Parallel layer execution across ADK agent invocations
- Node-level persistence and resume
- Human-in-the-loop approval and interactive loop gates
- Cross-run cost tracking and artifact management
- React Flow DAG visualization with live SSE status

---

## Summary

Archon's workflow engine is, at its core, three things working together:

**1. A data format** — YAML files that describe a graph of work, with explicit dependency edges, conditional execution, and per-node AI configuration.

**2. A runtime** — Kahn's algorithm turns the graph into parallel execution layers; each layer runs concurrently via `Promise.allSettled`; outputs flow forward via string substitution; all state is written to a DB so runs can resume.

**3. An observation system** — Every state transition fires both a DB write (durable) and an in-memory event (fast); SSE carries those events to the browser in real time; React Flow + Dagre render the graph with live status per node.

The philosophical contribution is treating **AI agent calls as nodes in a data pipeline**, with the same properties that make data pipelines reliable: explicit dependencies, parallel execution, durable state, and resumability. Everything else — the UI, the YAML schema, the human-in-the-loop gates — follows naturally from that foundational choice.
