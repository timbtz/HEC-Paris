# 02 · YAML Workflow DSL — Spec

The pipeline DSL is intentionally tiny. It is a YAML serialization of a `Pipeline` dataclass; there is no schema validator, no reserved word list, no expression language. Read `orchestration/api/pipeline_def.py` and `orchestration/api/pipeline_loader.py` together — together they are 25 lines of Python and they are the entire surface.

---

## 1. Top-level keys

```yaml
name: <string>          # required — must equal the filename stem (loader uses the filename)
trigger: <string>       # optional, default "manual" — manual|scheduled|data_update|chat
nodes: [<node>, ...]    # required — list of nodes (the DAG)
```

Source: `orchestration/api/pipeline_loader.py:9-24`:

```python
def load(name: str) -> Pipeline:
    path = _PIPELINES_DIR / f"{name}.yaml"
    if not path.exists():
        raise FileNotFoundError(f"Pipeline not found: {name}")
    raw = yaml.safe_load(path.read_text())
    nodes = [
        PipelineNode(
            id=n["id"],
            agent_class=n.get("agent_class"),
            tool_class=n.get("tool_class"),
            depends_on=n.get("depends_on", []),
            when=n.get("when"),
        )
        for n in raw.get("nodes", [])
    ]
    return Pipeline(name=raw["name"], trigger=raw.get("trigger", "manual"), nodes=nodes)
```

`trigger` is metadata only — the executor does not switch on it. It is consulted by route handlers and the UI to decide *when* to call `execute_pipeline()`. Valid values are not enforced: any string parses, but four are conventional (`manual`, `scheduled`, `data_update`, `chat`).

`name` in the YAML body must match the filename stem; otherwise the loader can't find it and downstream consumers (UI, router) won't recognise it. There is no validation — keep the filename and `name:` field in lockstep manually.

---

## 2. Per-node keys

```yaml
- id: <string>                   # required — unique within the pipeline
  tool_class: <string>           # required iff agent_class is absent
  agent_class: <string>          # required iff tool_class is absent
  depends_on: [<id>, <id>, ...]  # optional, default []
  when: <condition_name>         # optional — single named guard from conditions.py
```

The dataclass (`pipeline_def.py:5-19`):

```python
@dataclass
class PipelineNode:
    id: str
    agent_class: Optional[str] = None   # LLM agent class name (from agent_registry)
    tool_class: Optional[str] = None    # Deterministic tool class name (from tools/)
    depends_on: list[str] = field(default_factory=list)
    when: Optional[str] = None          # Named condition guard (from conditions.py)

@dataclass
class Pipeline:
    name: str
    trigger: str
    nodes: list[PipelineNode] = field(default_factory=list)
```

### Node types

There are exactly two node types, distinguished by which class field is set:

| Type | Field set | Resolved by | Execution mode |
|------|-----------|-------------|----------------|
| **Tool** | `tool_class:` | `agent_registry.get_tool()` | sync `def run(ctx)` invoked via `run_in_executor` |
| **Agent** | `agent_class:` | `agent_registry.get_agent()` | async `async def run(ctx)` awaited directly |

Setting both fields is a misconfiguration the executor catches at runtime: `_run_node` checks `tool_class` first, falls through to `agent_class`, and raises `ValueError` if neither is set (`dag_executor.py:69-70`).

There is **no third "terminal" node type.** The reference prompt mentioned one — it does not exist in this codebase. The "terminal" semantics is achieved by convention: the last node in topological order is whichever node nothing else depends on, and pipelines name it `write-proposal` / `write-alerts` / `write-alert-narrative`. The UI knows to render that node's output as the final answer; the executor does not care.

### Mandatory vs optional

| Field | Mandatory | Notes |
|-------|-----------|-------|
| `id` | yes | Must be unique within the pipeline; used as a key in `ctx.node_outputs` and as the foreign reference in `depends_on:` and conditions |
| `tool_class` **or** `agent_class` | yes (exactly one) | Must exist in the corresponding registry in `agent_registry.py` |
| `depends_on` | no (default `[]`) | List of upstream node ids; controls the topological layer the node lands in |
| `when` | no | Name of a function in `conditions._REGISTRY` |

There are **no other fields supported.** Anything else in YAML is silently dropped by the loader. If you want timeouts, retries, priorities, or labels, they have to be added to `PipelineNode` and `pipeline_loader.py` first. The new project will likely want at least `timeout` and possibly `retries`.

---

## 3. The `when:` conditional surface

`when:` accepts **a single condition name**, not an expression. There is no `and`/`or`/`not`, no comparison, no parenthesisation. If you need a compound, write a new function in `conditions.py` that combines them.

The full registry (`orchestration/api/conditions.py:88-103`) is 14 named guards. Examples:

```python
def has_alternatives(ctx):
    return bool(ctx.get("find-alternatives", {}).get("alternatives"))

def needs_supplier_research(ctx):
    out = ctx.get("find-alternatives", {})
    alts = out.get("alternatives", [])
    if len(alts) < 3:
        return True
    return all(a.get("Lead_Time_Days") is None for a in alts)

def no_substitutes_found(ctx):
    out = ctx.get("find-substitutes", {})
    return "substitutes" in out and not out["substitutes"]
```

**How flags reach the context:** they don't — the condition reads upstream node outputs directly. `find-alternatives` returns a dict containing `alternatives`; `has_alternatives` peeks at that key. There is no separate "flag space"; the condition layer is just a set of pure functions over `ctx.node_outputs`.

**Naming convention:** positive guards (`has_X`, `compliance_feasible`) and negative guards (`no_X_found`, `needs_Y`) are paired so two sibling nodes can branch on the same upstream output — one runs the happy path, one runs the fallback. See the substitution discovery example below.

---

## 4. Annotated examples

### Linear pipeline — `pipelines/price_monitor.yaml`

```yaml
name: price_monitor              # filename: price_monitor.yaml
trigger: scheduled               # informational; the cron/scheduler calls execute_pipeline
nodes:
  - id: find-stale               # node id, referenced by depends_on of next node
    tool_class: PriceStalenesCheckerTool   # deterministic SQL — finds entries with stale data
    depends_on: []               # entry node — no dependencies, lands in layer 0

  - id: fetch-prices
    agent_class: PriceFetchAgent           # LLM-driven price extraction (async)
    depends_on: [find-stale]               # waits for find-stale → lands in layer 1
    when: has_stale_prices                 # skipped if find-stale returned count: 0

  - id: write-alert-narrative
    agent_class: PriceAlertWriter          # LLM-driven prose (async)
    depends_on: [fetch-prices]             # waits for fetch-prices → layer 2
    when: has_price_alerts                 # skipped if no price changes ≥15% were detected
```

This is the classic warm-cache cascade: the deterministic tool finds candidates, the agent only runs when there are candidates to process, and the narrative writer only runs when the agent produced something worth narrating. The executor will do exactly three layers, each one node deep.

### Conditional + fan-out — `pipelines/substitution_discovery.yaml`

```yaml
name: substitution_discovery
trigger: chat
nodes:
  - id: find-substitutes                   # layer 0: deterministic graph walk
    tool_class: SubstitutionWalkerTool
    depends_on: []

  # ---------- layer 1 — five siblings fan out from find-substitutes ----------
  - id: gate-compliance
    tool_class: ComplianceReasonerTool
    depends_on: [find-substitutes]
    when: has_substitutes                  # only run if substitutes were found

  - id: find-alternatives
    tool_class: SupplierAlternativesTool
    depends_on: [find-substitutes]
    when: has_substitutes

  - id: bom-impact
    tool_class: BomImpactTool
    depends_on: [find-substitutes]         # NO `when` — always run; downstream tools
                                           # need impact data even if substitutes are empty

  - id: benchmark-prices
    tool_class: PriceBenchmarkTool
    depends_on: [find-substitutes]
    when: has_substitutes

  - id: no-data-fallback                   # the negative-path sibling
    tool_class: NoDataExplainerTool
    depends_on: [find-substitutes]
    when: no_substitutes_found             # mutually exclusive with the four above

  # ---------- layer 2 — the joiner ----------
  - id: write-proposal
    agent_class: ProposalWriter
    depends_on: [gate-compliance, find-alternatives, bom-impact, benchmark-prices]
    when: has_substitutes                  # if no substitutes, writer is skipped;
                                           # the fallback explainer carries the answer
```

Things to notice:

- **Five siblings in layer 1** all depend on the same upstream node and run in parallel via `asyncio.gather`. Sibling cardinality is unbounded; the only limit is what the asyncio loop can chew through.
- **Mutually exclusive branches** are expressed by paired `when:` guards (`has_substitutes` vs `no_substitutes_found`). The executor does not enforce mutual exclusion — it's the conditions' job to be disjoint.
- **A node without `when:`** (`bom-impact`) always runs and is robust against an empty upstream input. This is the convention for "metric" nodes whose output is informative even on the failure path.
- **The joiner's `depends_on` lists every layer-1 sibling it consumes**, even if some are skipped. Skipped nodes return `None` and downstream `ctx.get()` calls default to `{}` — the agent must read defensively.

---

## 5. How to add a new pipeline (cookbook)

Order matters; follow this sequence:

1. **Define the contract for any new tools/agents.** Each tool returns a dict; pick the shape *first* because conditions and downstream consumers will key off it.
2. **Implement tools** in `orchestration/tools/<name>.py` exposing `def run(ctx: AgnesContext) -> dict`. Implement agents in `orchestration/agents/<name>.py` exposing `async def run(ctx: AgnesContext) -> dict`.
3. **Register them** in `orchestration/api/agent_registry.py` — one line each in `_TOOL_REGISTRY` or `_AGENT_REGISTRY`.
4. **Add named conditions** in `orchestration/api/conditions.py` if branching is needed. Add the function and append it to the `_REGISTRY` dict at the bottom.
5. **Write the YAML** at `orchestration/pipelines/<name>.yaml`. The filename stem becomes the pipeline name.
6. **Wire the trigger.** For `chat`-triggered pipelines, add the pipeline name to the router's `_VALID_PIPELINES` set in `orchestration/agents/router_agent.py:62`. For `scheduled` or `data_update`, add a caller that invokes `execute_pipeline("name", ...)` from a cron job or webhook handler.
7. **Test by calling `execute_pipeline()` directly** from a Python REPL with the appropriate trigger payload, then watch `pipeline_events` rows appear in `orchestration.db`. The route layer is just a wrapper around this.

There is no "deploy", "publish", or "register" step beyond the registry edit. The loader picks up the YAML on first call. There's also no hot-reload — restart the FastAPI process to pick up changes.

---

## 6. Things the DSL deliberately doesn't support

- **Loops.** No `for_each`, no iteration. If you need to process N items, the upstream node returns the list and the downstream tool/agent iterates internally.
- **Subpipelines / nested DAGs.** Each YAML is one flat DAG.
- **Per-node parameters.** Tools/agents read everything from `ctx.trigger_payload` and upstream outputs. There is no `params:` block on a node. The new project may want to add one for static config (e.g. `mcc_threshold: 0.8`) — keep it simple, just pass the dict through to the callable.
- **Error recovery / retry.** Single attempt per node. Downstream agents should treat empty `ctx.get(...)` as the failure signal.

These omissions are features. Adding them is easy; keeping them out keeps the executor reviewable in one read.
