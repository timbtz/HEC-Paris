# Briefing 02 — DAG Executor, YAML Workflows, Deterministic vs LLM Nodes

> Audience: an engineer about to define the first FinTech pipeline (e.g., `kyc_onboarding.yaml`, `transaction_dispute.yaml`). This briefing explains exactly how the executor consumes a YAML and runs it.

---

## 1. The YAML schema — three concepts, no more

Pipelines live in `orchestration/pipelines/*.yaml`. The schema is intentionally tiny:

```yaml
name: <pipeline_name>          # must match filename without .yaml
trigger: chat | manual | schedule | data_update
nodes:
  - id: <node_id>              # unique within the pipeline
    tool_class: <ToolName>     # OR agent_class — never both
    agent_class: <AgentName>
    depends_on: [<node_id>, ...]   # empty list = root node
    when: <condition_name>     # optional — gates execution
```

A real example (`orchestration/pipelines/supplier_fallout.yaml:1-39`):

```yaml
name: supplier_fallout
trigger: chat
nodes:
  - id: find-alternatives
    tool_class: SupplierAlternativesTool
    depends_on: []

  - id: web-research
    agent_class: ResearchAgent
    depends_on: [find-alternatives]
    when: needs_supplier_research

  - id: verify-entity
    tool_class: EntityVerifyTool
    depends_on: [find-alternatives]
    when: has_alternatives

  - id: gate-compliance
    tool_class: ComplianceReasonerTool
    depends_on: [find-alternatives]
    when: has_alternatives

  - id: bom-impact
    tool_class: BomImpactTool
    depends_on: [find-alternatives]

  - id: format-rfqs
    tool_class: RfqFormatterTool
    depends_on: [gate-compliance, bom-impact]
    when: compliance_reasoner_feasible

  - id: write-proposal
    agent_class: ReactiveAgent
    depends_on: [format-rfqs, bom-impact, web-research, verify-entity]
```

**The same shape for a FinTech KYC pipeline:**

```yaml
name: kyc_onboarding
trigger: chat
nodes:
  - id: pull-application
    tool_class: ApplicationFetchTool
    depends_on: []

  - id: verify-identity
    tool_class: IdvProviderTool
    depends_on: [pull-application]

  - id: screen-sanctions
    tool_class: SanctionsListTool
    depends_on: [pull-application]

  - id: pull-credit-bureau
    tool_class: CreditBureauTool
    depends_on: [pull-application]
    when: identity_verified

  - id: external-context
    agent_class: AdverseMediaAgent          # LLM — searches news / litigation
    depends_on: [verify-identity]
    when: high_risk_jurisdiction

  - id: risk-score
    tool_class: RiskScoreTool
    depends_on: [verify-identity, screen-sanctions, pull-credit-bureau]

  - id: write-decision-memo
    agent_class: DecisionMemoWriter         # LLM — narrative for reviewer
    depends_on: [risk-score, external-context]
```

That's the entire mental model. The shape of the YAML *is* the shape of the audit trail.

---

## 2. How the executor turns YAML into work

`orchestration/api/dag_executor.py` is ~130 lines. Three functions do everything.

### 2.1 `_topological_layers(nodes)` — `dag_executor.py:27-52`

Kahn's algorithm. Returns a **list of layers**, each layer being a list of nodes whose dependencies have already been satisfied. Cycles are detected (`raise ValueError("Cycle detected in pipeline DAG")`).

For the supply-chain example, Kahn produces:

```
Layer 0: [find-alternatives]
Layer 1: [web-research, verify-entity, gate-compliance, bom-impact]
Layer 2: [format-rfqs]
Layer 3: [write-proposal]
```

Everything in a layer runs **in parallel**. Layer N+1 does not start until every node in layer N finishes (or is skipped).

### 2.2 `_run_node(node, ctx)` — `dag_executor.py:55-76`

Per-node execution wrapper:

1. Write a `node_started` event.
2. Evaluate the `when` guard. If false, return `(node_id, None, None)` → executor records a `node_skipped` event and downstream nodes that depend on this one transitively short-circuit.
3. Dispatch:
   - **Tool node** → `get_tool(name)` → run in default thread pool via `loop.run_in_executor` (tools are sync, so the event loop stays unblocked).
   - **Agent node** → `get_agent(name)` → `await fn(ctx)` (agents are native async).
4. Append `_elapsed_ms` to the output, return `(node_id, output, None)`.
5. On exception: capture full traceback, return `(node_id, None, error_string)` — the executor will fail the run.

### 2.3 `_execute(pipeline_name, run_id, ...)` — `dag_executor.py:91-131`

The orchestrator. Builds `AgnesContext`, loads the pipeline, computes layers, and for each layer:

```python
results = await asyncio.gather(*[_run_node(n, ctx) for n in layer])
for node_id, output, error in results:
    if error:
        write_event("node_failed", ...); update_run_status("failed"); return
    elif output is None:
        write_event("node_skipped", ...)
    else:
        ctx.node_outputs[node_id] = output
        write_event("node_completed", node_id, {"node_output": output})
```

**Failure semantics: fail-fast within a layer, fail-stop for the run.** Any node error aborts the whole pipeline. There is no per-node retry. If a FinTech workflow needs retry-with-backoff (e.g., flaky bureau API), put the retry inside the tool/agent itself, not in the executor.

### 2.4 `execute_pipeline(...)` — `dag_executor.py:79-88`

The public entrypoint. Creates the run row in SQLite, schedules `_execute` as a background task with `asyncio.create_task`, returns the `run_id` immediately. **All pipeline runs are non-blocking from the caller's perspective** — the HTTP request returns in ~50ms with a run ID, and the frontend then subscribes to the SSE stream.

---

## 3. The data plane: `AgnesContext`

`orchestration/api/agnes_context.py` defines the dataclass that every node receives:

```python
@dataclass
class AgnesContext:
    run_id: str
    pipeline_name: str
    trigger_source: str                # "chat" | "manual" | "schedule" | "data_update"
    trigger_payload: dict              # the original input (e.g., {"ingredient_name": "..."})
    node_outputs: dict[str, dict]      # populated as nodes complete; downstream nodes read here
    orchestration_db_path: Path
    enriched_db_path: Path             # domain DB; FinTech would replace with ledger / cust DB

    def get(self, node_id: str) -> dict | None:
        return self.node_outputs.get(node_id)
```

This is the **only** mechanism for state flowing between nodes. There is no shared mutable state, no global, no message bus inside a run. A node reads the trigger payload and any upstream outputs it cares about, then returns a fresh dict. The executor stores that dict in `ctx.node_outputs[node_id]` and later nodes read it via `ctx.get("upstream-id")`.

This shape has three consequences worth internalising:

1. **Determinism is local.** A node's output is a function only of `ctx`. Re-running the pipeline with the same payload and the same upstream outputs gives identical results — except for LLM stochasticity, which can be controlled by setting temperature to 0 inside the agent.
2. **Output dicts are the audit record.** Whatever a node returns is what gets persisted to `pipeline_events`. If you want compliance to see "we used credit score 712 from Equifax pulled at 14:22 UTC," put that in the dict. Don't hide it in logs.
3. **No node can mutate another node's output.** This is enforced by convention, not by the framework, but the pattern is clear in the codebase: nodes always return new dicts and never reach into `ctx.node_outputs[...]` to write.

---

## 4. Conditions: gating without branching

`when:` references a named condition function in `orchestration/api/conditions.py`. There are 14 today; one looks like this:

```python
def has_alternatives(ctx: AgnesContext) -> bool:
    out = ctx.get("find-alternatives") or {}
    return bool(out.get("alternative_suppliers"))
```

The executor calls `evaluate(condition_name, ctx)` (`dag_executor.py:60`). If false, the node is skipped and a `node_skipped` event is written. Downstream nodes that depend solely on a skipped node will themselves never run (their inputs are missing).

For FinTech, this is your branching primitive. Examples:

```python
def identity_verified(ctx) -> bool:
    return (ctx.get("verify-identity") or {}).get("status") == "passed"

def high_risk_jurisdiction(ctx) -> bool:
    country = (ctx.get("pull-application") or {}).get("country")
    return country in HIGH_RISK_COUNTRIES

def above_sar_threshold(ctx) -> bool:
    score = (ctx.get("risk-score") or {}).get("composite_score", 0)
    return score >= 80
```

There is no `if/else` in YAML. Instead, you express both arms as nodes with mutually exclusive `when:` guards. This keeps the DAG flat and the audit trail flat — every reviewer sees the same node list and can see at a glance which branches were taken.

---

## 5. Two node kinds, one executor

The split between **deterministic tools** and **LLM agents** is the single most important design decision in this framework, and it is exactly the split a regulated workflow needs.

### Tools (`orchestration/tools/*.py`)

```python
def run(ctx: AgnesContext) -> dict:
    payload = ctx.trigger_payload
    # ... pure Python: SQL, REST, calculation, rule engine ...
    return {"field": value, "confidence": 0.92, "sources": [...]}
```

- **Synchronous.** Runs in a thread pool inside the executor.
- **Deterministic.** Same input → same output. Suitable for any decision that must be reproducible byte-for-byte for compliance.
- 13 tools shipped today; registered in `orchestration/api/agent_registry.py:15-29`.
- FinTech examples: sanctions screen, limit check, IBAN/SWIFT validation, exposure calc, rule engine, ledger query.

### Agents (`orchestration/agents/*.py`)

```python
async def run(ctx: AgnesContext) -> dict:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return {"skipped": "key not set"}
    # ... build prompt from ctx.trigger_payload + ctx.node_outputs ...
    response = await client.messages.create(...)
    return {"narrative": response.content, "confidence": ..., "assumptions": [...]}
```

- **Async.** Awaited directly by the executor.
- **Stochastic** unless temperature is pinned to 0. Suitable for narratives, summaries, triage hints, escalation rationale — *not* for the actual binary decision.
- 8 agents shipped today; registered in `agent_registry.py:4-13`.
- FinTech examples: decision memo writer, adverse media triage, customer comms drafter, complaint summariser.

### The discipline

For a regulated workflow, **the binary decision should always come from a tool, never from an agent**. The agent's job is to *explain* the tool's output, not to make the call. Concretely:

- `screen-sanctions` (tool) returns `{"hit": true, "list": "OFAC SDN", "match_score": 0.94}`.
- `risk-score` (tool) returns `{"composite_score": 87, "components": {...}, "decision": "decline"}`.
- `write-decision-memo` (agent) reads both and produces a one-page rationale. It does not change the decision.

If you need an LLM in the decision loop (e.g., "does this transaction *look* like trade-based money laundering?"), wrap the LLM judgment inside a tool that captures both the LLM verdict *and* the deterministic floor: `{"llm_verdict": "suspicious", "llm_confidence": 0.7, "deterministic_floor_hit": true, "final_decision": "escalate"}`. The decision-making logic is then in plain Python, auditable line by line.

---

## 6. Adding a new node: the four steps

To add `screen-sanctions` to a new FinTech pipeline:

1. **Write the tool.** Create `orchestration/tools/sanctions_screen.py`:
    ```python
    from orchestration.api.agnes_context import AgnesContext

    def run(ctx: AgnesContext) -> dict:
        applicant = (ctx.get("pull-application") or {}).get("legal_name", "")
        hit = sanctions_client.check(applicant)
        return {
            "hit": hit.is_match,
            "list": hit.list_name,
            "match_score": hit.score,
            "checked_at": datetime.utcnow().isoformat(),
            "provider": "Refinitiv WC",
        }
    ```
2. **Register it.** Add to `orchestration/api/agent_registry.py:15-29`:
    ```python
    "SanctionsScreenTool": "orchestration.tools.sanctions_screen:run",
    ```
3. **Reference it in YAML.** Add to your pipeline file:
    ```yaml
    - id: screen-sanctions
      tool_class: SanctionsScreenTool
      depends_on: [pull-application]
    ```
4. **(Optional) Write a condition** if downstream nodes should gate on the result, in `orchestration/api/conditions.py`:
    ```python
    def sanctions_clear(ctx) -> bool:
        return not (ctx.get("screen-sanctions") or {}).get("hit", False)
    ```

No executor change. No SDK change. No frontend change — the new node will appear automatically in `/pipelines/{name}/graph` and stream into the live DAG view as soon as the pipeline runs.
