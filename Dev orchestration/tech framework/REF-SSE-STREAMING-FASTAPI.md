# REF-SSE-STREAMING-FASTAPI

**HappyRobot Sales Intelligence Platform — Implementation Reference**
**Version:** 1.0 | **Date:** 2026-04-15
**Scope:** Server-Sent Events streaming layer for pipeline run observability (PRD 6)
**Depends on:** `api/dag_executor.py`, `api/routes/pipelines.py`, `api/main.py`

---

## Table of Contents

1. [Architecture Overview](#1-architecture-overview)
2. [In-Process Event Bus](#2-in-process-event-bus)
3. [Hooking write_event() into the Bus](#3-hooking-write_event-into-the-bus)
4. [SSE Endpoint Implementation](#4-sse-endpoint-implementation)
5. [Frontend EventSource Subscription (Alpine.js / Vanilla JS)](#5-frontend-eventsource-subscription)
6. [CORS Considerations](#6-cors-considerations)
7. [Failure Modes and Mitigations](#7-failure-modes-and-mitigations)
8. [Complete Event Schema Reference](#8-complete-event-schema-reference)
9. [Library Decision: sse-starlette vs. Pure StreamingResponse](#9-library-decision-sse-starlette-vs-pure-streamingresponse)
10. [Integration Checklist](#10-integration-checklist)

---

## 1. Architecture Overview

### Data Flow

```
DAG Executor (dag_executor.py)
  │
  ├─► write_event() ─► SQLite pipeline_events table   [DURABLE — ground truth]
  │
  └─► publish_event() ─► asyncio.Queue (per run_id)   [IN-MEMORY — speed path]
              │
              ▼
      _event_bus: dict[str, asyncio.Queue]
      (module-level singleton in api/routes/pipelines.py)
              │
              ▼
      GET /pipeline/stream/{run_id}
      (StreamingResponse, media_type="text/event-stream")
              │
              ▼
      Browser EventSource / Alpine.js
      (live DAG node status updates)
```

### Late Subscriber / Replay Path

Clients that connect after a run has already started — or after it has completed — get full history via a SQLite replay before the live queue is attached:

```
Client connects (run already at node 3 of 5)
  │
  ├─► Step 1: SELECT * FROM pipeline_events WHERE run_id=? ORDER BY created_at ASC
  │           → stream all historical events as SSE immediately
  │
  └─► Step 2: Subscribe to live asyncio.Queue
              → stream new events as they arrive
              → terminate after pipeline_completed/pipeline_failed + 60s grace
```

This means a client that connects after the run finishes still receives the full event history (from SQLite) and a clean terminal event, then the SSE connection closes normally.

### Module Layout

| File | Responsibility |
|---|---|
| `api/dag_executor.py` | Writes events to SQLite; calls `publish_event()` (new) |
| `api/routes/pipelines.py` | Owns `_event_bus` singleton; exposes SSE endpoint |
| `api/main.py` | Starts bus-reaper background task in lifespan |

---

## 2. In-Process Event Bus

Add the following to **`api/routes/pipelines.py`**, after the imports, before the router definition. This is the complete in-process event bus implementation.

```python
# ---------------------------------------------------------------------------
# In-process SSE event bus
# ---------------------------------------------------------------------------
import time
from typing import List

_event_bus: dict[str, List[asyncio.Queue]] = {}
# Maps run_id -> list of subscriber queues (one per active SSE client)
# Using a list of queues (not a single queue) to support multiple simultaneous
# SSE clients for the same run_id without one client draining the other's events.

_bus_expiry: dict[str, float] = {}
# run_id -> unix timestamp when the bus becomes eligible for cleanup
# Set to time.time() + 120 when a terminal event (pipeline_completed or
# pipeline_failed) is published. The reaper removes entries after this time.

_bus_lock: asyncio.Lock = asyncio.Lock()
# Protects mutations to _event_bus and _bus_expiry from concurrent coroutines.

_BUS_QUEUE_MAXSIZE = 500
# Maximum events buffered per subscriber queue. If a slow client falls
# this far behind, new events are discarded rather than blocking the producer.

_BUS_TTL_SECONDS = 120
# How long after pipeline completion the bus entry is kept (for reconnects).

_BUS_REAPER_INTERVAL = 60
# How often the reaper background task runs (seconds).

TERMINAL_EVENT_TYPES = frozenset({"pipeline_completed", "pipeline_failed"})


async def get_or_create_bus(run_id: str) -> List[asyncio.Queue]:
    """Return the subscriber-list for run_id, creating it if absent.

    Call this when a new SSE client connects. Append a new asyncio.Queue
    to the returned list to register the client as a subscriber.
    """
    async with _bus_lock:
        if run_id not in _event_bus:
            _event_bus[run_id] = []
        return _event_bus[run_id]


async def publish_event(run_id: str, event: dict) -> None:
    """Fan out one event to all active subscriber queues for run_id.

    Uses put_nowait() so a slow/disconnected client never blocks the DAG
    executor. If a subscriber queue is full (maxsize=500), the event is
    silently discarded for that subscriber — they can catch up via SQLite
    replay on reconnect.

    Also sets bus expiry when a terminal event is detected.
    """
    async with _bus_lock:
        subscribers = _event_bus.get(run_id, [])
        for q in subscribers:
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                log.warning(
                    f"[sse_bus] subscriber queue full for run={run_id}, "
                    f"discarding event {event.get('event_type')}"
                )
        # Mark expiry on terminal events so the reaper can clean up
        if event.get("event_type") in TERMINAL_EVENT_TYPES:
            _bus_expiry[run_id] = time.time() + _BUS_TTL_SECONDS


async def remove_subscriber(run_id: str, queue: asyncio.Queue) -> None:
    """Remove one subscriber queue when its SSE connection closes."""
    async with _bus_lock:
        subscribers = _event_bus.get(run_id, [])
        if queue in subscribers:
            subscribers.remove(queue)
        # If no subscribers remain and run has no expiry set yet,
        # set a short expiry so the entry doesn't linger forever.
        if not subscribers and run_id not in _bus_expiry:
            _bus_expiry[run_id] = time.time() + _BUS_TTL_SECONDS


async def cleanup_expired_buses() -> None:
    """Remove bus entries whose TTL has elapsed.

    Intended to be called by the reaper background task every 60 seconds.
    Safe to call concurrently — acquires _bus_lock internally.
    """
    now = time.time()
    async with _bus_lock:
        expired = [
            run_id for run_id, expiry in _bus_expiry.items()
            if now >= expiry
        ]
        for run_id in expired:
            _event_bus.pop(run_id, None)
            _bus_expiry.pop(run_id, None)
    if expired:
        log.info(f"[sse_bus] reaped {len(expired)} expired bus entries: {expired}")


async def bus_reaper_task() -> None:
    """Background task: periodically clean up expired bus entries.

    Add to the lifespan task list in api/main.py:
        asyncio.create_task(bus_reaper_task())
    """
    while True:
        await asyncio.sleep(_BUS_REAPER_INTERVAL)
        await cleanup_expired_buses()
```

### Why a List of Queues (Not a Single Queue)

The naive approach — one `asyncio.Queue` per `run_id` — has a fatal flaw: if two SSE clients connect for the same run, one client's `get()` drains events before the other can see them. Each subscriber must have its own private queue. The bus fan-out in `publish_event()` copies each event into every subscriber's queue independently.

---

## 3. Hooking write_event() into the Bus

Modify `api/dag_executor.py` to dual-write: existing SQLite write stays unchanged, a new `publish_event()` call is appended.

### Current write_event() (lines 164–173 of dag_executor.py)

```python
async def write_event(run_id, event_type, data, db, node_id=None):
    """Insert one pipeline_events row. Never raises — logs on error."""
    try:
        await db.execute(
            "INSERT INTO pipeline_events (id, run_id, event_type, node_id, data) VALUES (?, ?, ?, ?, ?)",
            (str(uuid.uuid4()), run_id, event_type, node_id, json.dumps(data)),
        )
        await db.commit()
    except Exception as e:
        log.warning(f"[dag] write_event failed run={run_id} type={event_type}: {e}")
```

### Modified write_event() — add SSE bus publish

```python
async def write_event(run_id, event_type, data, db, node_id=None):
    """Insert one pipeline_events row AND publish to SSE bus.

    SQLite write is primary (durable). Bus publish is secondary (fast path).
    Both are fire-and-forget on error — callers must not rely on either
    raising exceptions.
    """
    event_id = str(uuid.uuid4())
    try:
        await db.execute(
            "INSERT INTO pipeline_events (id, run_id, event_type, node_id, data) VALUES (?, ?, ?, ?, ?)",
            (event_id, run_id, event_type, node_id, json.dumps(data)),
        )
        await db.commit()
    except Exception as e:
        log.warning(f"[dag] write_event SQLite failed run={run_id} type={event_type}: {e}")

    # NEW: also publish to SSE in-process bus
    try:
        from api.routes.pipelines import publish_event
        event_payload = {
            "id": event_id,              # UUID — used for Last-Event-ID dedup
            "event_type": event_type,
            "node_id": node_id,
            "data": data,
            "created_at": utcnow(),
        }
        await publish_event(run_id, event_payload)
    except Exception as e:
        log.warning(f"[dag] write_event bus publish failed run={run_id} type={event_type}: {e}")
```

### Import Note

The `from api.routes.pipelines import publish_event` import is deferred inside the function body to avoid circular imports (dag_executor is imported by pipelines.py at module level). This is the same deferred-import pattern already used in dag_executor.py (e.g., `from api.pipeline_def import ApprovalNode` inside `execute_node()`).

### Register Reaper in main.py Lifespan

Add one line to the task list in `api/main.py`:

```python
# In lifespan(), alongside the existing background tasks:
from .routes.pipelines import bus_reaper_task

tasks = [
    asyncio.create_task(compile_worker()),
    asyncio.create_task(pipeline_scheduler(db)),
    asyncio.create_task(scheduled_send_loop(db)),
    asyncio.create_task(bus_reaper_task()),   # NEW — SSE bus cleanup
]
```

---

## 4. SSE Endpoint Implementation

### Route: GET /pipeline/stream/{run_id}

Add this to **`api/routes/pipelines.py`**, after the existing route handlers.

```python
import asyncio
import json
import time
from datetime import datetime, timezone

from fastapi import Request
from fastapi.responses import StreamingResponse

# Heartbeat interval: 30s. Prevents proxies (nginx, Cloudflare, AWS ALB) from
# closing idle connections. SSE spec comment lines (": text\n\n") are ignored
# by EventSource but reset the proxy's idle timeout.
_HEARTBEAT_INTERVAL = 30.0

# Grace period after terminal event: keep streaming for 60s in case the client
# is slow to receive and close its EventSource.
_TERMINAL_GRACE_SECONDS = 60.0


def _format_sse_event(
    event_type: str,
    data: dict,
    event_id: str | None = None,
) -> str:
    """Serialize one event to SSE wire format.

    SSE wire format (RFC):
        id: <uuid>\n
        event: <event_type>\n
        data: <json>\n
        \n          ← blank line terminates the event

    The `id:` field enables Last-Event-ID reconnect: the browser sends
    the last received id as the `Last-Event-ID` header on reconnect, and
    the server can skip already-delivered events.
    """
    lines = []
    if event_id:
        lines.append(f"id: {event_id}")
    lines.append(f"event: {event_type}")
    lines.append(f"data: {json.dumps(data)}")
    lines.append("")   # blank line = event boundary
    lines.append("")
    return "\n".join(lines)


def _heartbeat_comment() -> str:
    """SSE comment line. Ignored by EventSource; resets proxy idle timers."""
    return ": keepalive\n\n"


@router.get("/stream/{run_id}")
async def stream_pipeline_events(
    run_id: str,
    request: Request,
    db: aiosqlite.Connection = Depends(get_db),
):
    """Stream pipeline execution events as Server-Sent Events.

    Sequence:
      1. Replay all historical events from SQLite (handles late joiners).
      2. Subscribe to the live in-process event bus.
      3. Stream live events until pipeline_completed/pipeline_failed + 60s grace.
      4. Send a synthetic `stream_closed` event and close.

    Reconnect support:
      Pass `Last-Event-ID` header (browser does this automatically) to skip
      events already received. The server filters historical replay by
      created_at order using the event UUID — UUID v4 is not time-ordered,
      so we track a `seen_ids` set from the Last-Event-ID header instead.

    Client disconnect:
      The generator checks `await request.is_disconnected()` on each iteration.
      On GeneratorExit (client disconnect), the subscriber queue is removed
      from the bus.
    """
    # Capture Last-Event-ID for reconnect deduplication
    last_event_id: str | None = request.headers.get("last-event-id")

    async def event_generator():
        subscriber_queue: asyncio.Queue = asyncio.Queue(maxsize=_BUS_QUEUE_MAXSIZE)
        terminal_received = False
        terminal_received_at: float | None = None

        # ── Step 1: subscribe to bus BEFORE replaying history ───────────────
        # Subscribe first to avoid a race: if we replayed history then
        # subscribed, a node could complete between the two steps and
        # its event would be lost.
        subscribers = await get_or_create_bus(run_id)
        async with _bus_lock:
            subscribers.append(subscriber_queue)

        seen_ids: set[str] = set()
        replay_complete = False

        try:
            # ── Step 2: replay historical events from SQLite ─────────────────
            # If reconnecting (Last-Event-ID set), we still replay all history
            # but skip events whose id we've already seen. This is safe because
            # the event stream is idempotent — re-delivering an event the client
            # already processed causes no harm; skipping is purely an
            # optimization to avoid redundant UI updates.
            skip_until_after: str | None = last_event_id

            async with db.execute(
                "SELECT id, event_type, node_id, data, created_at "
                "FROM pipeline_events "
                "WHERE run_id = ? "
                "ORDER BY created_at ASC",
                (run_id,),
            ) as cursor:
                skip_mode = skip_until_after is not None
                async for row in cursor:
                    event_id = row["id"]

                    if skip_mode:
                        # Skip events up to and including the last seen event
                        if event_id == skip_until_after:
                            skip_mode = False
                        seen_ids.add(event_id)
                        continue

                    seen_ids.add(event_id)
                    try:
                        data = json.loads(row["data"])
                    except (json.JSONDecodeError, TypeError):
                        data = {}

                    yield _format_sse_event(
                        event_type=row["event_type"],
                        data={
                            "node_id": row["node_id"],
                            "created_at": row["created_at"],
                            **data,
                        },
                        event_id=event_id,
                    )

                    # Track if a terminal event was already in history
                    if row["event_type"] in TERMINAL_EVENT_TYPES:
                        terminal_received = True
                        terminal_received_at = time.time()

            replay_complete = True

            # If run is already complete (terminal event was in history),
            # drain the live queue briefly then close — the run is done.
            # We do NOT return immediately: there may be a few in-flight
            # events in the queue from the moment between history read and
            # queue subscribe.

            # ── Step 3: stream live events from the subscriber queue ─────────
            heartbeat_deadline = time.time() + _HEARTBEAT_INTERVAL

            while True:
                # Check for client disconnect
                if await request.is_disconnected():
                    log.info(f"[sse] client disconnected for run={run_id}")
                    return

                # If terminal was received (from history or live), check grace period
                if terminal_received:
                    elapsed = time.time() - terminal_received_at
                    if elapsed >= _TERMINAL_GRACE_SECONDS:
                        break

                # Compute wait time: minimum of time to next heartbeat and
                # remaining grace period (if in grace)
                if terminal_received:
                    wait = min(
                        heartbeat_deadline - time.time(),
                        _TERMINAL_GRACE_SECONDS - (time.time() - terminal_received_at),
                        1.0,   # poll at most every 1s during grace period
                    )
                else:
                    wait = max(0.0, heartbeat_deadline - time.time())
                    wait = min(wait, 1.0)   # cap at 1s to check disconnect

                try:
                    event = await asyncio.wait_for(
                        subscriber_queue.get(),
                        timeout=max(wait, 0.05),
                    )
                except asyncio.TimeoutError:
                    # No event arrived in time — send heartbeat if due
                    if time.time() >= heartbeat_deadline:
                        yield _heartbeat_comment()
                        heartbeat_deadline = time.time() + _HEARTBEAT_INTERVAL
                    continue

                event_id = event.get("id")

                # Dedup: skip events already sent via replay
                if event_id and event_id in seen_ids:
                    continue
                if event_id:
                    seen_ids.add(event_id)

                event_type = event.get("event_type", "unknown")
                yield _format_sse_event(
                    event_type=event_type,
                    data={
                        "node_id": event.get("node_id"),
                        "created_at": event.get("created_at"),
                        **(event.get("data") or {}),
                    },
                    event_id=event_id,
                )

                # Reset heartbeat on each real event
                heartbeat_deadline = time.time() + _HEARTBEAT_INTERVAL

                if event_type in TERMINAL_EVENT_TYPES:
                    terminal_received = True
                    terminal_received_at = time.time()

            # ── Step 4: send terminal marker and close ───────────────────────
            yield _format_sse_event(
                event_type="stream_closed",
                data={"run_id": run_id, "reason": "pipeline_terminal"},
            )

        except GeneratorExit:
            log.info(f"[sse] GeneratorExit for run={run_id} (client disconnected)")
        except Exception as exc:
            log.error(f"[sse] unexpected error for run={run_id}: {exc}", exc_info=True)
            try:
                yield _format_sse_event(
                    event_type="stream_error",
                    data={"error": str(exc)},
                )
            except Exception:
                pass
        finally:
            # Always unsubscribe from the bus on disconnect or completion
            await remove_subscriber(run_id, subscriber_queue)
            log.debug(f"[sse] subscriber removed for run={run_id}")

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            # Disable buffering in proxies that respect this header
            "X-Accel-Buffering": "no",
            # Prevent any caching layer from storing the stream
            "Cache-Control": "no-cache, no-transform",
            # Keep the TCP connection alive
            "Connection": "keep-alive",
        },
    )
```

### SSE Wire Format Example

For a `node_completed` event, the bytes sent over the wire look like:

```
id: 3f2a1b4c-8e7d-4a2f-9c1e-000000000001
event: node_completed
data: {"node_id": "strategy", "created_at": "2026-04-15T10:22:01.123Z", "node_output": "RECOMMENDED_ACTION: send_response\n..."}

```

Note the blank line after `data:`. The SSE spec requires it. Each `\n` in the JSON must be part of the `data:` line (i.e., multi-line JSON is sent as a single `data:` line with the JSON serialized to a compact string).

### Endpoint URL

The route is registered under the `prefix="/pipeline"` in `main.py`, so the full URL is:

```
GET /pipeline/stream/{run_id}
```

---

## 5. Frontend EventSource Subscription

### Vanilla JavaScript (no library required)

This works with Alpine.js via `x-init` or as a standalone function. No third-party SSE library is needed — `EventSource` is built into every modern browser.

```javascript
/**
 * subscribeToRun — open an SSE connection for one pipeline run.
 *
 * @param {string}   runId          - pipeline_runs.id (UUID)
 * @param {object}   handlers       - optional event type overrides
 * @param {Function} handlers.onNodeStarted     - called with parsed event data
 * @param {Function} handlers.onNodeCompleted   - called with parsed event data
 * @param {Function} handlers.onNodeFailed      - called with parsed event data
 * @param {Function} handlers.onPipelineCompleted
 * @param {Function} handlers.onApprovalRequested
 * @returns {EventSource}  the EventSource instance (call .close() to stop early)
 */
function subscribeToRun(runId, handlers = {}) {
    const url = `/pipeline/stream/${runId}`;
    const es = new EventSource(url);

    // Generic error handler — EventSource auto-reconnects on network errors.
    // The browser will automatically send `Last-Event-ID` on reconnect so the
    // server can skip already-seen events.
    es.onerror = (e) => {
        // readyState 2 = CLOSED (permanent failure, not just a reconnect)
        if (es.readyState === EventSource.CLOSED) {
            console.warn(`[sse] stream permanently closed for run ${runId}`);
        } else {
            // readyState 0 = CONNECTING (browser is retrying)
            console.warn(`[sse] SSE error, browser will retry automatically`, e);
        }
    };

    // pipeline_started — run has entered the executor
    es.addEventListener('pipeline_started', (e) => {
        const data = JSON.parse(e.data);
        console.log(`[sse] pipeline started: ${data.pipeline_name}`);
        handlers.onPipelineStarted?.(data);
    });

    // node_started — an agent node has begun executing
    es.addEventListener('node_started', (e) => {
        const data = JSON.parse(e.data);
        console.log(`[sse] node started: ${data.node_id} (agent: ${data.agent_class})`);
        handlers.onNodeStarted?.(data);
        // Example: update a UI node to "running" state
        updateNodeStatus(data.node_id, 'running');
    });

    // node_completed — agent node finished successfully
    es.addEventListener('node_completed', (e) => {
        const data = JSON.parse(e.data);
        console.log(`[sse] node completed: ${data.node_id}`);
        handlers.onNodeCompleted?.(data);
        updateNodeStatus(data.node_id, 'completed', data.node_output);
    });

    // node_failed — agent node threw an exception
    es.addEventListener('node_failed', (e) => {
        const data = JSON.parse(e.data);
        console.error(`[sse] node failed: ${data.node_id}`, data.error);
        handlers.onNodeFailed?.(data);
        updateNodeStatus(data.node_id, 'failed', null, data.error);
    });

    // node_skipped — node's `when:` condition evaluated to false
    es.addEventListener('node_skipped', (e) => {
        const data = JSON.parse(e.data);
        handlers.onNodeSkipped?.(data);
        updateNodeStatus(data.node_id, 'skipped');
    });

    // approval_requested — pipeline paused at ApprovalNode
    es.addEventListener('approval_requested', (e) => {
        const data = JSON.parse(e.data);
        console.log(`[sse] approval requested: draft=${data.draft_id}`);
        handlers.onApprovalRequested?.(data);
        // Show approval UI — do NOT close the stream here; the pipeline
        // resumes after approval and sends more events.
    });

    // pipeline_completed — all nodes finished; close the stream
    es.addEventListener('pipeline_completed', (e) => {
        const data = JSON.parse(e.data);
        console.log(`[sse] pipeline completed for run ${runId}`);
        handlers.onPipelineCompleted?.(data);
        es.close();  // close immediately — no more events will arrive
    });

    // pipeline_failed — executor caught an unrecoverable error
    es.addEventListener('pipeline_failed', (e) => {
        const data = JSON.parse(e.data);
        console.error(`[sse] pipeline failed: ${data.error}`);
        handlers.onPipelineFailed?.(data);
        es.close();
    });

    // stream_closed — server-side terminal marker; belt-and-suspenders close
    es.addEventListener('stream_closed', (e) => {
        console.log(`[sse] stream_closed received for run ${runId}`);
        es.close();
    });

    return es;
}

// ── Example stub — replace with your actual DAG UI update logic ──────────────
function updateNodeStatus(nodeId, status, output = null, error = null) {
    // Find the node element in your DAG canvas and update its visual state.
    // Example with Alpine.js reactive data:
    //   Alpine.store('pipeline').nodes[nodeId].status = status;
    console.log(`updateNodeStatus: ${nodeId} → ${status}`);
}
```

### Alpine.js Integration Example

```html
<div x-data="pipelineMonitor()" x-init="init()">
    <template x-for="node in nodes" :key="node.id">
        <div :class="nodeClass(node.status)">
            <span x-text="node.id"></span>
            <span x-text="node.status"></span>
        </div>
    </template>
</div>

<script>
function pipelineMonitor() {
    return {
        runId: null,
        nodes: [],
        es: null,

        init() {
            // runId typically comes from the URL or from the POST /pipeline/run response
            this.runId = new URLSearchParams(location.search).get('run_id');
            if (!this.runId) return;

            this.es = subscribeToRun(this.runId, {
                onNodeStarted: (data) => this.setNodeStatus(data.node_id, 'running'),
                onNodeCompleted: (data) => this.setNodeStatus(data.node_id, 'completed'),
                onNodeFailed: (data) => this.setNodeStatus(data.node_id, 'failed'),
                onNodeSkipped: (data) => this.setNodeStatus(data.node_id, 'skipped'),
                onPipelineCompleted: () => this.setAllDone(),
            });
        },

        setNodeStatus(nodeId, status) {
            const node = this.nodes.find(n => n.id === nodeId);
            if (node) node.status = status;
        },

        setAllDone() {
            this.es?.close();
        },

        nodeClass(status) {
            return {
                'border-gray-400': status === 'pending',
                'border-blue-500 animate-pulse': status === 'running',
                'border-green-500': status === 'completed',
                'border-red-500': status === 'failed',
                'opacity-50': status === 'skipped',
            };
        },
    };
}
</script>
```

### How EventSource Auto-Reconnects

The browser `EventSource` implementation automatically:
1. Reconnects after a network drop (default retry interval ~3 seconds, configurable via `retry: <ms>` SSE field)
2. Sends the `Last-Event-ID` header on reconnect, set to the `id:` field from the most recently received event
3. The server reads `request.headers.get("last-event-id")` and skips already-delivered events in the replay step

This means a brief network interruption is transparent to the UI — events are not duplicated or lost.

---

## 6. CORS Considerations

### The Problem

`EventSource` follows browser CORS rules. If the SSE endpoint is served from a different origin than the page (e.g., page at `http://localhost:3000`, API at `http://localhost:8000`), the browser requires CORS preflight responses.

However, `EventSource` does **not** send a preflight `OPTIONS` request — it sends a `GET` directly. The response must include `Access-Control-Allow-Origin` on that `GET`, or the browser will silently refuse to open the stream.

### Current CORS Configuration (api/main.py)

The app already has a permissive CORS configuration:

```python
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["GET", "POST", "PATCH"],
    allow_headers=["Content-Type", "X-Webhook-Secret"],
)
```

This is sufficient for development and the current demo configuration. The SSE `GET` request will have `Access-Control-Allow-Origin: *` on its response.

### SSE-Specific CORS Header

The `Last-Event-ID` header sent by the browser on reconnect must be in `allow_headers`. Add it:

```python
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["GET", "POST", "PATCH"],
    allow_headers=["Content-Type", "X-Webhook-Secret", "Last-Event-ID"],  # add this
)
```

Without `Last-Event-ID` in `allow_headers`, browsers will block the reconnect request.

### Production CORS (Narrow Origins)

In production, replace `allow_origins=["*"]` with the explicit list of allowed origins. `allow_origins=["*"]` and `allow_credentials=True` are mutually exclusive in FastAPI/Starlette — if you need credentials (cookies), you must name origins explicitly:

```python
ALLOWED_ORIGINS = [
    "https://yourdomain.com",
    "https://dashboard.yourdomain.com",
    # Local dev:
    "http://localhost:3000",
    "http://localhost:8080",
    "http://127.0.0.1:3000",
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,   # only valid when origins are not "*"
    allow_methods=["GET", "POST", "PATCH"],
    allow_headers=["Content-Type", "X-Webhook-Secret", "Last-Event-ID"],
)
```

### Proxy / Reverse Proxy Notes

Nginx by default buffers SSE responses, which breaks the real-time nature. Ensure your nginx config or proxy config includes:

```nginx
location /pipeline/stream/ {
    proxy_pass http://localhost:8000;
    proxy_set_header Connection '';
    proxy_http_version 1.1;
    proxy_buffering off;
    proxy_cache off;
    proxy_read_timeout 3600s;   # long timeout for persistent connections
}
```

The `X-Accel-Buffering: no` header set in the `StreamingResponse` is the nginx-specific way to disable buffering inline (without nginx config changes).

---

## 7. Failure Modes and Mitigations

| Failure | Symptom | Mitigation |
|---|---|---|
| **Client disconnects mid-run** | Subscriber queue grows; events back-fill forever | `put_nowait()` raises `QueueFull` when queue reaches maxsize=500. Caught and logged. Slow/dead clients are silently dropped at the bus level. |
| **Two SSE clients for same run_id** | With a single `asyncio.Queue`, one client drains events before the other sees them | `_event_bus[run_id]` is a `list[asyncio.Queue]` — one queue per subscriber. `publish_event()` fans out to all queues independently. |
| **Bus entry never cleaned up** | `_event_bus` and `_bus_expiry` grow unbounded; memory leak | Reaper background task (`bus_reaper_task`) runs every 60s and removes entries whose TTL (120s after terminal event) has elapsed. |
| **SSE client joins after run completes** | No live bus entry; no events received; blank page | SQLite replay path (Step 1 in `event_generator`) runs unconditionally. Historical events are always delivered. Terminal event in history sets `terminal_received = True` so grace period starts immediately. |
| **Proxy strips idle SSE connection** | Connection drops every 60–90s even with healthy pipeline | 30-second heartbeat comment lines (`: keepalive\n\n`) sent on idle reset proxy timers. `Connection: keep-alive` and `X-Accel-Buffering: no` headers also help. |
| **DAG executor crashes mid-run** | No `pipeline_failed` event; SSE stream never terminates | Grace period check: if no event arrives for `_TERMINAL_GRACE_SECONDS` AND replay showed no terminal event, the stream eventually closes. Additionally, `fail_orphaned_runs()` at startup marks stale runs as failed; a late-connecting client sees that via replay. |
| **SQLite lock during replay** | Historical query blocks while executor writes | WAL mode (already configured in `main.py`) allows concurrent reads and writes. Replay reads and executor writes proceed simultaneously without blocking. |
| **asyncio.Lock contention** | `_bus_lock` held during fan-out blocks producers | Fan-out in `publish_event()` uses `put_nowait()` (non-blocking) inside the lock. Even with many subscribers, the lock is held only for the duration of N `put_nowait()` calls, each O(1). |
| **Event bus not yet created when first event fires** | `publish_event()` called before any SSE client connects; event silently discarded | This is expected and safe. Events before a client connects are recoverable via SQLite replay when the client eventually connects. |
| **`from api.routes.pipelines import publish_event` circular import** | ImportError at startup | Import is deferred inside `write_event()` function body (not at module level). Same pattern as other deferred imports in dag_executor.py. |

---

## 8. Complete Event Schema Reference

All events are written by `dag_executor.py::write_event()`. The `data` column in `pipeline_events` contains a JSON object. Events are published to the SSE bus with this envelope:

```json
{
  "id": "<uuid4>",
  "event_type": "<string>",
  "node_id": "<string | null>",
  "data": { ... },
  "created_at": "<ISO8601 UTC>"
}
```

### Event Types

| event_type | node_id | data payload | Trigger location |
|---|---|---|---|
| `pipeline_started` | `null` | `{"pipeline_name": str, "lead_id": str}` | `execute_pipeline()` — new run created |
| `node_started` | node id | `{"agent_class": str}` | `execute_node()` — before `run_adk_agent()` |
| `node_completed` | node id | `{"node_output": str}` | `execute_node()` — after `run_adk_agent()` returns |
| `node_failed` | node id | `{"error": str}` | `execute_node()` — on exception from `run_adk_agent()` or `when()` |
| `node_skipped` | node id | `{"reason": "when_condition_false"}` | `execute_node()` — when `node.when(context)` returns `False` |
| `node_skipped_prior_success` | node id | `{"reason": "prior_success"}` | `execute_node()` — node already completed in a prior resumed run |
| `approval_requested` | node id | `{"message": str, "draft_id": str \| null}` | `execute_node()` — when node is `ApprovalNode` instance |
| `approval_received` | node id | `{"decision": "approved", "comment": str}` | `resume_pipeline()` — after operator approves via POST endpoint |
| `pipeline_completed` | `null` | `{}` | `execute_dag()` — after all nodes complete successfully |
| `guardrail_violation` | `null` | `{"floor": float, "ceiling": float, "offer_price": float}` OR `{"error": str}` | `validate_and_set_guardrail_flag()` — PRD 5 pricing check |

### node_id Values

`node_id` corresponds to the `id` field on `PipelineNode` / `ApprovalNode` in `api/pipeline_def.py`. Common values observed in the existing pipelines:

| node_id | agent_class | Pipeline |
|---|---|---|
| `wiki_readiness` | `WikiReadinessAgent` | inbound_responder, new_lead_onboarding |
| `strategy` | `StrategyAgent` | inbound_responder |
| `response` | `ResponseAgent` | inbound_responder |
| `response_agent` | `ResponseAgent` | (alias used in some pipelines) |
| `approval` | — (ApprovalNode) | inbound_responder |
| `sender` | `SenderAgent` | inbound_responder |
| `knowledge_curation` | `KnowledgeCurationAgent` | knowledge_sync |
| `cold_outreach` | `ColdOutreachAgent` | cold_outreach |
| `opportunity_agent` | `OpportunityAgent` | opportunity_scan |
| `autodream_agent` | `AutodreamAgent` | autodream |
| `lead_discovery` | `LeadDiscoveryAgent` | new_lead_onboarding |
| `lead_classification` | `LeadClassificationAgent` | new_lead_onboarding |

### node_completed.node_output Content

The `node_output` field in `node_completed` events contains the raw text returned by the ADK agent. The format is agent-specific:

| Agent | Output format |
|---|---|
| `ResponseAgent` | Markdown draft body preceded by `DRAFT_PATH: leads/...` on first line |
| `StrategyAgent` | YAML frontmatter block with `recommended_action:`, `tone:`, `channel:` fields |
| `SenderAgent` | Confirmation string; side effect is writing `status: sent` to draft file |
| All others | Free-form text reasoning / analysis |

---

## 9. Library Decision: sse-starlette vs. Pure StreamingResponse

### Recommendation: Pure StreamingResponse (no new dependency)

**`sse-starlette`** is a thin wrapper around Starlette's `StreamingResponse` that provides:
- A helper class `ServerSentEvent` for building SSE payloads
- Automatic handling of `Last-Event-ID` header
- Disconnect detection via `asyncio.CancelledError`

**Verdict for HappyRobot:** Not worth adding as a dependency. Reasons:

1. The `_format_sse_event()` helper in this guide replaces `ServerSentEvent` with 8 lines of code.
2. FastAPI's `StreamingResponse` already handles all the HTTP mechanics correctly.
3. Adding a dependency for a problem that takes 8 lines to solve increases the `requirements.txt` surface area for no meaningful benefit.
4. `sse-starlette` v1.x and v2.x have a breaking API change (EventSourceResponse vs ServerSentEvent model) — avoiding it also avoids this upgrade risk.

**When `sse-starlette` is worth it:** If you need the dashboard SSE stream (`GET /pipeline/stream/__all__` that fans out events from all active runs simultaneously), `sse-starlette`'s `EventSourceResponse` has a cleaner API for that pattern. Defer the decision until that endpoint is built.

---

## 10. Integration Checklist

Use this checklist when implementing the SSE layer. Each item maps to a section in this guide.

### Code Changes

- [ ] **`api/routes/pipelines.py`** — Add imports: `time`, `List`, `Request`, `StreamingResponse`
- [ ] **`api/routes/pipelines.py`** — Add bus singleton variables (`_event_bus`, `_bus_expiry`, `_bus_lock`, constants)
- [ ] **`api/routes/pipelines.py`** — Add functions: `get_or_create_bus`, `publish_event`, `remove_subscriber`, `cleanup_expired_buses`, `bus_reaper_task`
- [ ] **`api/routes/pipelines.py`** — Add `_format_sse_event()` and `_heartbeat_comment()` helpers
- [ ] **`api/routes/pipelines.py`** — Add `stream_pipeline_events` route: `GET /pipeline/stream/{run_id}`
- [ ] **`api/dag_executor.py`** — Modify `write_event()`: add deferred import of `publish_event` and fan-out call after SQLite write
- [ ] **`api/main.py`** — Add `bus_reaper_task` to the lifespan background tasks list
- [ ] **`api/main.py`** — Add `"Last-Event-ID"` to `CORSMiddleware.allow_headers`

### Verification Steps

- [ ] Start the server; confirm no `ImportError` on startup (circular import check)
- [ ] `POST /pipeline/run/inbound_responder` with a test lead
- [ ] In a second terminal: `curl -N http://localhost:8000/pipeline/stream/<run_id>`
- [ ] Confirm events appear in real time (`node_started`, `node_completed`, etc.)
- [ ] Wait for `pipeline_completed` — confirm curl exits ~60s after that event
- [ ] Re-run the curl command after the run completes — confirm full history replays from SQLite
- [ ] Open two curl connections simultaneously — confirm both receive all events (fan-out works)
- [ ] Kill one curl mid-run — confirm the other continues unaffected
- [ ] Confirm heartbeat lines appear every ~30s during long-running nodes

### Frontend Smoke Test

```html
<!-- Drop in static/dashboard.html for a quick manual test -->
<script>
const runId = prompt("Enter run_id:");
const es = subscribeToRun(runId, {
    onNodeStarted: (d) => console.log("STARTED:", d.node_id),
    onNodeCompleted: (d) => console.log("DONE:", d.node_id, d.node_output?.slice(0, 80)),
    onPipelineCompleted: () => console.log("PIPELINE DONE"),
});
</script>
```

---

*End of REF-SSE-STREAMING-FASTAPI.md*
