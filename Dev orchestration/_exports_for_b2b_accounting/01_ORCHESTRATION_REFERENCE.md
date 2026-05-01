# 01 · Orchestration Reference — DAG Executor, End to End

This document describes the orchestration spine that the new project should lift. All file paths are relative to the Fingent repo root. The transferable parts are the executor, the registry, the context object, the condition evaluator, and the failure semantics. The domain (supply chain) is incidental.

---

## 1. Execution model

The executor lives in `orchestration/api/dag_executor.py`. It is ~130 lines and does five things:

1. **Build dependency layers** via Kahn's algorithm.
2. **Run each layer in parallel** with `asyncio.gather`.
3. **Resolve `agent_class`/`tool_class`** to a Python callable via the registry.
4. **Evaluate node-level `when:` guards** against the shared context.
5. **Persist every state transition** to `orchestration.db` and **fan out** via an in-process pub/sub bus (SSE backbone).

### 1a. Topological layering — `dag_executor.py:27-52`

```python
def _topological_layers(nodes: list[PipelineNode]) -> list[list[PipelineNode]]:
    """Kahn's algorithm — returns node layers (each layer runs in parallel)."""
    in_degree = {n.id: len(n.depends_on) for n in nodes}
    dependents: dict[str, list[str]] = {n.id: [] for n in nodes}
    by_id = {n.id: n for n in nodes}
    for n in nodes:
        for dep in n.depends_on:
            dependents[dep].append(n.id)
    ready = [n for n in nodes if in_degree[n.id] == 0]
    layers: list[list[PipelineNode]] = []
    while ready:
        layers.append(ready)
        next_ready = []
        for node in ready:
            for child_id in dependents[node.id]:
                in_degree[child_id] -= 1
                if in_degree[child_id] == 0:
                    next_ready.append(by_id[child_id])
        ready = next_ready
    if sum(len(l) for l in layers) != len(nodes):
        raise ValueError("Cycle detected in pipeline DAG")
    return layers
```

Each returned `layer` is a `list[PipelineNode]` whose nodes have all their `depends_on` already satisfied. Layers run sequentially; nodes within a layer run in parallel.

### 1b. Per-node execution — `dag_executor.py:55-77`

```python
async def _run_node(node, ctx) -> tuple[str, Optional[dict], Optional[str]]:
    start = time.monotonic()
    try:
        await _db.write_event(ctx.run_id, "node_started", node.id, {}, ctx.orchestration_db_path)
        if node.when and not evaluate(node.when, ctx):
            return node.id, None, None  # skipped
        if node.tool_class:
            fn = get_tool(node.tool_class)
            output = await asyncio.get_event_loop().run_in_executor(None, fn, ctx)
        elif node.agent_class:
            fn = get_agent(node.agent_class)
            output = await fn(ctx)
        else:
            raise ValueError(f"Node {node.id!r} has neither agent_class nor tool_class")
        elapsed = int((time.monotonic() - start) * 1000)
        return node.id, {**(output or {}), "_elapsed_ms": elapsed}, None
    except Exception as exc:
        elapsed = int((time.monotonic() - start) * 1000)
        return node.id, None, f"{type(exc).__name__}: {exc}\n{traceback.format_exc()}"
```

**Key design choices, with caveats:**

- **Tools run on a thread executor** (`run_in_executor`); agents are awaited directly. This is because Fingent's tools are synchronous SQLite functions — the new project should keep this split if it uses sync DB drivers.
- A returned `(node_id, None, None)` means *skipped* (the `when:` guard was false). `(node_id, output, None)` means *succeeded*. `(node_id, None, error)` means *failed*.
- **Honest gap:** `_elapsed_ms` is stamped onto the output dict, which means it's reachable to downstream nodes that read `ctx.get(...)["_elapsed_ms"]`. Probably an oversight; the new project should namespace it (`__meta__`) or strip it before assigning to `node_outputs`.

### 1c. The orchestrator loop — `dag_executor.py:91-131`

```python
async def _execute(pipeline_name, run_id, trigger_source, trigger_payload, db_path):
    ctx = FingentContext(run_id=run_id, pipeline_name=pipeline_name,
                       trigger_source=trigger_source, trigger_payload=trigger_payload,
                       orchestration_db_path=db_path)
    try:
        pipeline = load_pipeline(pipeline_name)
        await _db.write_event(run_id, "pipeline_started", data={"pipeline": pipeline_name}, db_path=db_path)
        layers = _topological_layers(pipeline.nodes)
        for layer in layers:
            results = await asyncio.gather(*[_run_node(n, ctx) for n in layer])
            for node_id, output, error in results:
                if error:
                    await _db.write_event(run_id, "node_failed", node_id, {"error": error}, db_path)
                    _db.update_run_status(run_id, "failed", error=f"Node {node_id} failed: {error[:200]}", db_path=db_path)
                    return
                elif output is None:
                    await _db.write_event(run_id, "node_skipped", node_id, {}, db_path)
                else:
                    ctx.node_outputs[node_id] = output
                    await _db.write_event(run_id, "node_completed", node_id, {"node_output": output}, db_path)
        await _db.write_event(run_id, "pipeline_completed", data={"pipeline": pipeline_name}, db_path=db_path)
        _db.update_run_status(run_id, "completed", db_path=db_path)
    except Exception as exc:
        err = f"{type(exc).__name__}: {exc}"
        await _db.write_event(run_id, "pipeline_failed", data={"error": err}, db_path=db_path)
        _db.update_run_status(run_id, "failed", error=err, db_path=db_path)
```

`execute_pipeline()` (lines 79-88) creates the run row synchronously and returns the `run_id` to the caller; the actual execution kicks off via `asyncio.create_task` and runs in the background. The HTTP route hands `run_id` to the client immediately so the UI can subscribe to SSE before any work starts.

---

## 2. The shared context object — `orchestration/api/fingent_context.py`

```python
@dataclass
class FingentContext:
    run_id: str
    pipeline_name: str
    trigger_source: str
    trigger_payload: dict[str, Any] = field(default_factory=dict)
    node_outputs: dict[str, Any] = field(default_factory=dict)
    enriched_db_path: Path = field(default_factory=lambda: _PROJECT_ROOT / "db_enriched.sqlite")
    orchestration_db_path: Path = field(default_factory=lambda: _PROJECT_ROOT / "orchestration.db")
    def get(self, node_id: str, default=None): return self.node_outputs.get(node_id, default)
```

Just a dataclass. Three things it carries:

- **`trigger_payload`** — the dict from the route handler (e.g. `{"ingredient_name": "Vitamin C"}`). Set once, read by tools/agents that need router-extracted params.
- **`node_outputs`** — mutated by the executor *between layers*. Downstream nodes read upstream output via `ctx.get("node-id", {})`.
- **DB paths** — the two SQLite files. Tools dial them directly (`sqlite3.connect(str(ctx.enriched_db_path))`). The path is never global; everything routes through `ctx`.

There is **no I/O on this object**, no method that calls anything. It is a pure pipe. That's the whole design: the executor mutates `node_outputs`, tools read from it. Concurrency safety inside a layer is nominal — nodes within a layer must not write to the *same* downstream node_id (which can't happen because each node owns its own id).

---

## 3. Agent / tool registry — `orchestration/api/agent_registry.py`

```python
_AGENT_REGISTRY: dict[str, str] = {
    "ReactiveAgent":     "orchestration.agents.reactive_agent:run",
    "ProactiveAgent":    "orchestration.agents.proactive_agent:run",
    ...
}
_TOOL_REGISTRY: dict[str, str] = {
    "SupplierAlternativesTool":   "orchestration.tools.supplier_alternatives:run",
    "ComplianceGateTool":         "orchestration.tools.compliance_gate:run",
    ...
}
def get_agent(name): return _import(_AGENT_REGISTRY[name])
def get_tool(name):  return _import(_TOOL_REGISTRY[name])
```

Two flat string-keyed dicts mapping the YAML name to a `module.path:attr` dotted reference. `_import` does `importlib.import_module(...).<attr>`. Lazy — the module is only imported the first time `get_agent`/`get_tool` is called.

**To register a new tool:**
1. Write `orchestration/tools/<name>.py` exposing `def run(ctx: FingentContext) -> dict`.
2. Add one line to `_TOOL_REGISTRY`.
3. Reference the YAML key (`tool_class: NameTool`) in a pipeline.

That's it. There's no decorator, no auto-discovery, no class hierarchy. The new project should keep this simplicity.

---

## 4. Condition evaluator — `orchestration/api/conditions.py`

The `when:` field in YAML is **not** an expression — it is a **named guard** registered in a Python dict.

```python
def has_alternatives(ctx: FingentContext) -> bool:
    out = ctx.get("find-alternatives", {})
    return bool(out.get("alternatives"))

def no_substitutes_found(ctx: FingentContext) -> bool:
    out = ctx.get("find-substitutes", {})
    return "substitutes" in out and not out["substitutes"]

_REGISTRY: dict[str, ConditionFn] = {
    "has_alternatives": has_alternatives,
    "no_substitutes_found": no_substitutes_found,
    ...
}
def evaluate(name: str, ctx: FingentContext) -> bool:
    fn = _REGISTRY.get(name)
    if fn is None:
        raise ValueError(f"Unknown condition: {name!r}")
    return fn(ctx)
```

There are 14 conditions registered (`conditions.py:88-103`). Each is a one-purpose function reading specific upstream node outputs. **There is no expression grammar.** A YAML `when: has_alternatives` just looks the function up by name and calls it.

**This is deliberate and worth lifting verbatim.** Expression DSLs over context dicts always grow into half-baked Python; named guards keep the conditional surface small and reviewable.

To set a flag, an upstream node simply writes a key into its return dict — e.g. `find-alternatives` returns `{"alternatives": [...]}`, and the next node's `when: has_alternatives` peeks at that key.

---

## 5. Failure semantics

- **Per-node failure short-circuits the run.** `_execute` (line 117) calls `update_run_status(... "failed")` and `return`s on the first node error. Sibling nodes already started in the same `asyncio.gather` will run to completion (gather doesn't cancel them); they just won't propagate. This is fine because the executor never re-uses a failed run, but for the new project, if you want hard cancellation, switch to `asyncio.wait(..., return_when=FIRST_EXCEPTION)` and cancel the rest. **Honest gap:** the prompt asked about `FIRST_COMPLETED` and per-node timeouts (60s/300s) — those are *not* in this code. Fingent uses unbounded `asyncio.gather`; node timeouts must be enforced inside the tool/agent body. The new project should add a wrapper that wraps each `_run_node` call in `asyncio.wait_for(..., timeout=node.timeout)`.
- **Skipped nodes are first-class.** Returning `None` for output (when `when:` is false) emits a `node_skipped` event but does *not* fail the run. Downstream nodes that `depends_on` a skipped node still run — they read `ctx.get("skipped-id", {})` and get `{}`, the default. Tools must defensively handle the empty case.
- **Everything is logged to `orchestration.db`.** Schema in `orchestration/schema/pipeline_schema.sql`:

```sql
CREATE TABLE IF NOT EXISTS pipeline_runs (
    id TEXT PRIMARY KEY,        -- UUID
    pipeline_name TEXT NOT NULL,
    trigger_source TEXT NOT NULL,
    trigger_payload TEXT NOT NULL DEFAULT '{}',
    status TEXT NOT NULL DEFAULT 'running',     -- running|completed|failed
    started_at TEXT NOT NULL DEFAULT (datetime('now')),
    completed_at TEXT, error TEXT,
    metadata TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS pipeline_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    event_type TEXT NOT NULL,   -- pipeline_started|node_started|node_completed|node_failed|node_skipped|pipeline_completed|pipeline_failed
    node_id TEXT, data TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (run_id) REFERENCES pipeline_runs(id)
);
```

Every node emits `node_started`/`node_completed|failed|skipped`. The output dict is JSON-stringified into `pipeline_events.data`. This *is* the audit trail — you can reconstruct any run end-to-end from these two tables, no log files needed. The new project should mirror this exactly.

- **SSE fan-out.** `_db.write_event` (in `orchestration/api/db.py:79-94`) inserts the row *and* calls `await publish(run_id, payload)` from `orchestration/api/event_bus.py`. The FastAPI SSE route reads from the bus to push live updates to the UI. Keep the dual-write — insert first (durable), publish second (best-effort).

---

## 6. What the caller sees

```python
run_id = await execute_pipeline("supplier_fallout", "chat", {"ingredient_name": "Vitamin C"})
# returns immediately; client subscribes to /events/{run_id}
```

The HTTP route returns `run_id` synchronously. Status flips through `running → completed|failed` in `pipeline_runs`. Final node outputs are queryable via `db.get_run_with_events(run_id)` (`db.py:106-112`), which joins runs + events. There is no other return surface — outputs are not collected and returned to the route. The new project may want to add a "fetch terminal output by node id" endpoint for non-SSE clients.
