# 04 · Agent / Tool Contracts and Confidence Scoring

This is the most important file for the new project. The cache-warmer pattern in §4 is the structural template for Swan-transaction → counterparty resolution. Read that section last; it ties the rest together.

---

## 1. The contract every tool and agent obeys

### Tool — `def run(ctx: FingentContext) -> dict`

```python
# orchestration/tools/supplier_alternatives.py:12
def run(ctx: FingentContext) -> dict:
    payload = ctx.trigger_payload
    ingredient_name: str = payload.get("ingredient_name", "")
    ...
    return {
        "alternatives": alternatives,
        "canonical_id": canonical_id,
        "canonical_name": resolution["canonical_name"],
        "ingredient_name": ingredient_name,
        "count": len(alternatives),
        "resolution": resolution,
    }
```

**Tool contract:**

- **Synchronous** — no `async`. The executor calls `loop.run_in_executor(None, fn, ctx)` (`dag_executor.py:65`), so blocking SQLite calls are fine.
- **Returns a `dict`.** The dict is deep-merged with `_elapsed_ms` (line 73) and stored as `ctx.node_outputs[node.id]`.
- **Errors propagate as exceptions.** The executor catches them at `_run_node` (line 74), formats the traceback, and writes `node_failed`. Tools should *not* try/except internally unless they want to convert a failure into a structured result (see `_ingredient_resolver.resolve()` which catches and returns `{"resolution_failed": True}` so the next tool can branch on it).
- **No global state.** Read DB paths from `ctx.enriched_db_path`. Open connections inline. Close them.

### Agent — `async def run(ctx: FingentContext) -> dict`

```python
# orchestration/agents/proactive_agent.py:31
async def run(ctx: FingentContext) -> dict:
    opportunities = ctx.get("scan-opportunities", {}).get("opportunities", [])
    if not opportunities:
        return {"proposals": [], "count": 0}
    if not os.environ.get("GOOGLE_API_KEY"):
        return {"proposals_narrative": None, "skipped": "GOOGLE_API_KEY not set", ...}
    payload = json.dumps({"opportunities": opportunities}, indent=2)
    proposals_narrative = await run_adk_agent(_AGENT, payload, ctx.run_id)
    return {"summary": proposals_narrative, ...}
```

**Agent contract:** identical to tool, except:

- **Async** — `async def run`, awaited directly by the executor (line 68).
- **Output schema is convention, not enforced.** Each agent has a documented return shape; downstream conditions key off specific fields (`reasoning` peeks at `ctx.get("write-proposal", {}).get("narrative")`). There is no Pydantic, no JSON schema validation. **Honest gap:** the new project should add output validation — even a lightweight `dataclasses.dataclass` per node return type prevents drift when an agent's output and a downstream condition disagree about field names.
- **Graceful degradation when API keys are missing.** Every agent checks `os.environ.get("GOOGLE_API_KEY")` and returns a `{"skipped": "..."}` dict instead of raising. The pipeline completes; the UI shows "skipped" reason. Lift this pattern.

### Error propagation — single rule

The executor turns any uncaught exception into:

```python
return node.id, None, f"{type(exc).__name__}: {exc}\n{traceback.format_exc()}"
```

— and writes `node_failed`. The full traceback is in `pipeline_events.data`. There is no retry layer. The new project may want one, but it belongs in a wrapper around `_run_node`, not in tools/agents themselves.

---

## 2. Google ADK integration — `_adk_runner.py`

```python
# orchestration/agents/_adk_runner.py:9
async def run_adk_agent(agent: LlmAgent, payload: str, run_id: str) -> str:
    """Run one ADK LlmAgent as a single DAG node. Returns final text output."""
    if not os.environ.get("GOOGLE_API_KEY"):
        return ""
    runner = InMemoryRunner(agent=agent, app_name=agent.name)
    session = await runner.session_service.create_session(app_name=agent.name, user_id=run_id)
    final = ""
    async for event in runner.run_async(
        user_id=run_id,
        session_id=session.id,
        new_message=types.Content(role="user", parts=[types.Part(text=payload)]),
    ):
        if event.is_final_response() and event.content:
            for part in event.content.parts:
                if part.text:
                    final += part.text
    return final.strip()
```

**Honest assessment for the new project: do not adopt ADK.**

ADK is here primarily because the original Fingent implementation used Gemini and benefited from ADK's `google_search` tool wiring (see `search_sub_agent.py`, called by `research_agent.py:36`). It is *not* core to the orchestration model. The runner is 25 lines and does only three things:

1. Create an `InMemoryRunner` per call (no session reuse — every invocation is a fresh session keyed by `run_id`).
2. Send a single user message containing the JSON payload.
3. Concatenate text from `final_response` events.

**The new project should use Anthropic's SDK directly.** Replace `_adk_runner.py` with a 30-line `_claude_runner.py` that:

- Takes a system prompt + user payload.
- Calls `client.messages.create(model="claude-sonnet-4-6", system=..., messages=...)`.
- Returns the final text content.
- Optionally accepts a `tools=[...]` list for tool calling.

The agent files (`reactive_agent.py`, `proactive_agent.py`) become thinner — no `LlmAgent` wrapper, just `_SYSTEM = "..."` + a call to the runner.

There is also no schema validation on ADK outputs in Fingent. Every agent does `re.search(r"\[.*\]", raw, re.DOTALL)` or `re.search(r"\{.*\}", ...)` to pull JSON out of free-form text (e.g. `research_agent.py:58-67`). This is fragile. The new project should use Claude's native tool-calling for any structured output — define a tool with a JSON schema, force the model to call it, and read `tool_use.input` directly. Same pattern, no regex, no parse errors.

---

## 3. Deterministic confidence scoring — the `reasoning/` modules

These modules formalize the "every decision has a confidence and a refusal mode" pattern. Worth lifting verbatim.

### 3a. `reasoning/base.py` — the contract

```python
# reasoning/base.py:22
@dataclass
class ToolResult:
    result: Any
    confidence: Optional[float]
    evidence_ids: List[int] = field(default_factory=list)
    refusal: Optional[str] = None
    tool_name: str = ""
    latency_ms: int = 0

    @property
    def passed(self) -> bool:
        return self.refusal is None and self.result is not None

def compound_confidence(values: Iterable[Optional[float]]) -> float:
    """Multiplicative confidence aggregation.
    None values are treated as 0.5 (unknown -> half-trust)..."""
    vs = [0.5 if v is None else max(0.0, min(1.0, float(v))) for v in values]
    if not vs: return 0.0
    out = 1.0
    for v in vs: out *= v
    return out
```

The `ToolResult` shape: every reasoning step emits a `result` (the decision), a `confidence` in [0,1], a list of `evidence_ids` referencing rows in an append-only ledger, and a `refusal` reason (string) when the step declined to produce an answer.

`compound_confidence` is **multiplicative**, not averaged. Rationale baked into the docstring: any single weak link in a chain of gates should drop the whole chain. Missing values are 0.5, not 1.0 — "unknown" is half-trust, never full-trust. **This is the most important transferable rule.**

For accounting: per-line item, gate on (counterparty resolved? account inferred? VAT resolved? balance check passes?) and multiply the four confidences. If any is below ~0.7, raise the entry into a review queue rather than auto-posting.

### 3b. `reasoning/supplier_scorer.py` — normalised + weighted scoring

```python
# reasoning/supplier_scorer.py:82
for r in rows:
    p_score = (1.0 - _norm(r["Price_USD_Per_KG"], min_p, max_p)) if r["Price_USD_Per_KG"] is not None else 0.5
    l_score = (1.0 - _norm(r["Lead_Time_Days"], min_l, max_l)) if r["Lead_Time_Days"] is not None else 0.5
    q_parts = []
    if r["Purity_Pct"] is not None: q_parts.append(min(r["Purity_Pct"] / 100.0, 1.0))
    q_parts.append(0.0 if r["Grade_Unverified"] else 1.0)
    if r["Confidence"] is not None: q_parts.append(float(r["Confidence"]))
    q_score = _safe_mean(q_parts)
    weighted = (W_P * p_score + W_L * l_score + W_Q * q_score) / W_total
```

Pattern: **normalise each axis to [0,1] across the candidate set**, then weight-and-average. Weights live in `Scoring_Config` (key/value table); defaults from `_DEFAULT_WEIGHTS` apply if the table is missing or empty (`supplier_scorer.py:22-31`). Missing input data slots in 0.5, not 0.0 or 1.0 — same "unknown is half-trust" rule.

This is generic — it has nothing to do with supply chain. Lift the shape verbatim for ranking journal-entry candidate accounts when the chart-of-accounts mapping is ambiguous.

### 3c. `reasoning/justification.py` — human-readable trace

`render()` (`justification.py:34-105`) takes the `ToolResult.result` dicts from the gate engine, compliance reasoner, and refusal engine, and produces a markdown explanation. Critically:

- **It is rule-based, not LLM-generated** (line 12 docstring: "we deliberately keep this rule-based (no LLM) so the demo is reproducible and we can show 'no hallucination' with a straight face").
- It enumerates every gate, marks pass/fail with a confidence percentage, and emits a "Why we refused" section if the decision was a refusal.

For accounting: use this pattern verbatim. Render the per-entry decision trace as deterministic markdown (rules fired, confidence, alternatives considered, refusal reason). The narrative *around* the journal entries can be Claude-generated; the decision trace itself stays rule-based and reproducible.

### 3d. `reasoning/evidence_ledger.py` — append-only ledger

```python
# reasoning/evidence_ledger.py:31
class EvidenceLedger:
    def add(self, source, claim, url=None, raw_excerpt=None, confidence=None) -> int:
        cur = self.conn.execute(
            "INSERT INTO Evidence_Ledger (Source, Url, Claim, RawExcerpt, Confidence) VALUES (?, ?, ?, ?, ?)",
            (source, url, claim, raw_excerpt, confidence))
        self.conn.commit()
        return int(cur.lastrowid)
```

There is no `update()`, no `delete()` — append-only by API. `evidence_ids` from `ToolResult` are FK-style references to rows in this ledger. The justification renderer joins them in to display source URLs and snippets.

> The new project's analog is `claim_citations` (described in 03_SQLITE_BACKBONE.md §2c). Same shape, same write-only API.

### 3e. `reasoning/red_team.py` — adversarial test harness

This module is **generic and worth lifting** even though the attacks are domain-specific. The pattern:

1. Build a list of `AttackCase` dataclasses, each containing inputs designed to trick the chain.
2. For each case, run the full reasoning chain (`gate → compliance → refusal`) and record the verdict.
3. The pass criterion (`red_team.py:292-302`) is "the chain refused OR flagged the case". A clean `recommend` on an adversarial input is the failure mode being detected.
4. Verdicts are persisted to `Case_Library` for regression tracking.

For accounting, build attack cases like: "duplicate invoice with one cent difference", "credit note with mismatched VAT", "transfer between two subsidiaries with reversed signs", "expense with missing counterparty masquerading as a refund". The same harness shape — `expected_refusal=True`, `expected_failure_tag="some_substring"` — proves the system catches things rather than waving them through. **The exit code is 0 only if every attack was caught** (`red_team.py:378`).

### 3f. Where gate thresholds live

- `RefusalEngine.CONFIDENCE_FLOOR = 0.50` (`reasoning/refusal_engine.py:23`). Below this, a recommendation becomes `refuse_low_confidence`. The comment at line 24-29 explains why it is 0.50 and not 0.60: with 6 gate factors × 2 compliance factors multiplied, missing-signal rows would push the product below 0.60 even when the chain is fundamentally fine. *Tune empirically with your own attack suite; do not pick a number from prior art.*
- Gate-level confidences are baked into `gate_engine.py` per gate.
- Fuzzy-match thresholds: `_FUZZY_THRESHOLD = 85` (`_ingredient_resolver.py:117`) — RapidFuzz token-set ratio.

The pattern: **named constants at module level, with a comment explaining how they were calibrated.** No magic numbers buried in expressions.

---

## 4. End-to-end cache-warmer flow — Fingent example, walked node by node

> **This is the section to internalize.** The new project's counterparty resolution is structurally identical.

**Scenario:** A user types "we lost our supplier for vitamin d3" into the chat. The router classifies → `supplier_fallout` pipeline runs.

### Step 1 — pipeline starts

`POST /chat` → router agent classifies → `execute_pipeline("supplier_fallout", "chat", {"ingredient_name": "vitamin d3"})`. The executor creates a `pipeline_runs` row with status=`running`, returns `run_id` to the client immediately, and kicks off the DAG (`dag_executor.py:79-88`).

### Step 2 — `find-alternatives` (deterministic resolver tries first)

YAML (`pipelines/supplier_fallout.yaml:8-10`):

```yaml
- id: find-alternatives
  tool_class: SupplierAlternativesTool
  depends_on: []
```

Tool body (`tools/supplier_alternatives.py:12-30`):

```python
def run(ctx: FingentContext) -> dict:
    payload = ctx.trigger_payload
    ingredient_name: str = payload.get("ingredient_name", "")
    resolution = resolve(ingredient_name, db_path=ctx.enriched_db_path)
    if resolution["resolution_failed"]:
        return {"alternatives": [], "ingredient_name": ingredient_name,
                "error": f"ingredient not found ({resolution.get('reason')})",
                "resolution": resolution}
    canonical_id = resolution["canonical_id"]
    ...
```

The deterministic resolver `_ingredient_resolver.resolve()` (`tools/_ingredient_resolver.py:120`) tries three stages in order:

1. **Exact case-insensitive match** on `Ingredient_Canonical.Name` → confidence 1.0, method `"exact"`.
2. **Curated synonym lookup** (`_SYNONYMS` dict, ~80 entries: `"vitamin d3" → "Vitamin D"`) → confidence 0.95, method `"synonym"`. *This is the warm cache.* Hits avoid an LLM call entirely.
3. **RapidFuzz** `token_set_ratio` over all canonical names with threshold 85 → confidence min(0.85, score/100), method `"fuzzy"`.

For "vitamin d3", stage 2 hits → canonical_id resolved → SQL query returns ranked alternatives → tool returns `{"alternatives": [...], "canonical_id": ..., "resolution": {...}}`. **No Claude call. Sub-second.** This is the steady-state path.

### Step 3 — fallthrough to LLM agent on miss

If stage 3 also misses (`resolution_failed=True`), the tool returns `{"alternatives": []}`. The next node has `when: needs_supplier_research`:

```python
# orchestration/api/conditions.py:60
def needs_supplier_research(ctx):
    out = ctx.get("find-alternatives", {})
    alts = out.get("alternatives", [])
    if len(alts) < 3: return True
    return all(a.get("Lead_Time_Days") is None for a in alts)
```

When the deterministic path produces fewer than 3 results, this guard fires and the **LLM agent runs:**

```yaml
- id: web-research
  agent_class: ResearchAgent
  depends_on: [find-alternatives]
  when: needs_supplier_research
```

`ResearchAgent.run()` (`agents/research_agent.py:27`) calls `search_sub_agent` (web search), then asks Gemini to extract structured JSON: `[{supplier_name, country, website, certifications, ...}]`. Output is parsed with regex (`research_agent.py:58-67`).

### Step 4 — output is "schema-validated"

In Fingent today, this is just `json.loads(m.group())` inside a try/except. **In the new project**, this is where Claude tool-calling earns its keep: define a tool with a JSON schema for the counterparty record, force the model to call it, and read `tool_use.input` directly. No regex, no parse errors.

### Step 5 — written back as a confidence-scored cache row

`research_agent.py:46` stages discovered suppliers via `_stage_suppliers()`:

```python
# research_agent.py:70
def _stage_suppliers(suppliers, ingredient_name, db_path):
    conn = sqlite3.connect(str(db_path))
    row = conn.execute("SELECT Id FROM Ingredient_Canonical WHERE LOWER(Name) = LOWER(?) LIMIT 1",
                       (ingredient_name,)).fetchone()
    canonical_id = row[0] if row else None
    for s in suppliers:
        conn.execute("""INSERT INTO Agent_Log (Run_Id, Agent, Node, Status, Input_JSON, Output_JSON,
                        Related_IngredientId, Logged_At) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""", ...)
```

**Honest gap:** in current Fingent, the writeback lands in `Agent_Log` (the audit table), not in `Ingredient_Canonical` itself. So the *next* time the same ingredient query arrives, the deterministic resolver still misses unless someone promotes the staged row. The new project must close this loop end-to-end: the agent's output should be written into `counterparties` (with its confidence and `match_method = "claude_agent"`) so the next occurrence resolves through stage 1 of the deterministic resolver. **This is the single highest-leverage fix when transferring this pattern.**

### Step 6 — next occurrence is deterministic

Once `counterparties` (or in Fingent: `Ingredient_Canonical`) has the row, the next inbound query for the same identifier hits stage 1 — exact match, confidence 1.0, no LLM call. The cache is now warm for that entity. Aggregate behaviour: AI populates the cache for novel inputs; deterministic rules serve all subsequent traffic. Cost flatlines as the cache fills.

### Step 7 — downstream nodes consume

The remaining DAG nodes (`verify-entity`, `gate-compliance`, `bom-impact`, `format-rfqs`, `write-proposal`) all read `ctx.get("find-alternatives", {})` plus their other dependencies. Each tool emits a structured result; each agent emits a narrative. The final node (`write-proposal`, `agent_class: ReactiveAgent`) joins all upstream outputs into a `payload = json.dumps({...})` and asks the LLM for the user-facing summary (`reactive_agent.py:36-43`).

The decision trace — what the resolver did, what gates passed, what confidence each step carried — is *already* in `pipeline_events.data` because each tool's full return dict is logged by the executor. Combined with the per-domain refusal log (§2c of 03_SQLITE_BACKBONE.md), the audit trail is complete: from raw input to final decision, every step is queryable.

---

## 5. Summary — what to lift verbatim

- `ToolResult` shape (`reasoning/base.py:22`) — every reasoning step returns this.
- `compound_confidence` (multiplicative, missing=0.5) (`reasoning/base.py:44`).
- The 3-stage resolver pattern (exact → curated synonym → fuzzy with threshold) (`tools/_ingredient_resolver.py:120`).
- `RefusalEngine.CONFIDENCE_FLOOR` named constant + commented rationale (`reasoning/refusal_engine.py:23`).
- The rule-based `justification.render()` pattern — markdown decision trace, no LLM (`reasoning/justification.py:34`).
- The append-only `EvidenceLedger.add()` API (`reasoning/evidence_ledger.py:36`).
- The red-team harness shape (`reasoning/red_team.py`) — list of attack cases, run all, exit code reflects whether all attacks were caught.

What to *not* lift: ADK, regex JSON extraction, the missing writeback loop in `research_agent._stage_suppliers`. Replace with Claude SDK + tool-calling + a proper writeback to the canonical table.
