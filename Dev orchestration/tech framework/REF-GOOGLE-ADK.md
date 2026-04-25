# Google ADK Reference Guide
## HappyRobot Backend — Orchestration Layer

> Research-validated reference for Claude instances building the ADK agent pipeline.
> Sources: google.github.io/adk-docs, github.com/google/adk-python, official codelabs.

---

## Installation & Setup

```bash
pip install google-adk          # or:
uv add google-adk
```

- **Python minimum**: 3.10 (supports 3.10–3.14).
- **Latest stable version**: 1.29.0 (April 9, 2026). Avoid 1.27.0 (yanked — breaking change in Agent Engine deployments).
- **Package name on PyPI**: `google-adk`

### Authentication

For local dev with Gemini API key (no GCP project required):

```bash
export GOOGLE_API_KEY="AIza..."
export GOOGLE_GENAI_USE_VERTEXAI=false   # REQUIRED to use API key instead of Vertex AI ADC
```

For GCP production: use Application Default Credentials (`gcloud auth application-default login`).

---

## Core Agent Class

### Import Path

```python
from google.adk.agents import LlmAgent     # ← correct import (NOT from google.adk import Agent)
```

### Constructor Parameters

```python
agent = LlmAgent(
    name="ingest_agent",                           # unique identifier, no spaces
    model="gemini-2.5-flash",                      # model string (see below)
    instruction=open("prompts/ingest.md").read(),   # system prompt — can load from file
    tools=[read_file, list_directory, search_wiki], # plain functions or FunctionTool wrappers
    description="Validates and ingests new interactions", # optional
    output_key="ingest_output",                    # optional: saves final response to context.state
)
```

**Model string format**: Use simple name — `"gemini-2.5-flash"`, not `"models/gemini-2.5-flash"`.

Valid Gemini model strings:
- `"gemini-2.5-flash"` — fast, cheap; use for IngestAgent, StrategyAgent
- `"gemini-2.5-pro"` — slower, higher quality; optional for StrategyAgent if quality matters
- `"gemini-flash-latest"` — stable alias for latest Flash

---

## Workflow Agents

### SequentialAgent

> **HappyRobot note**: `SequentialAgent` is **not used as the runtime orchestrator** in this project.
> PRD 4's Python DAG executor (`api/dag_executor.py`) calls `InMemoryRunner` individually per node.
> `SequentialAgent` is documented here for context — the per-node pattern below is what gets built.

```python
from google.adk.agents import SequentialAgent

pipeline = SequentialAgent(
    name="lead_processor",
    sub_agents=[ingest_agent, strategy_agent, response_agent],  # parameter is sub_agents (not agents)
)
```

**State sharing**: All sub-agents inside a SequentialAgent share the **same `InvocationContext`**. They can read each other's outputs via `context.state`. However, for the HappyRobot architecture, inter-agent communication happens exclusively through files on disk — not session state. This is intentional (see Architecture constraints below).

### ParallelAgent

```python
from google.adk.agents import ParallelAgent

parallel = ParallelAgent(
    name="parallel_workers",
    sub_agents=[agent_a, agent_b, agent_c],  # run concurrently
)
```

Use for analytics: run multiple lead analysis agents simultaneously. Each agent should write to unique wiki paths to avoid file conflicts.

### LoopAgent

```python
from google.adk.agents import LoopAgent

loop = LoopAgent(
    name="refiner",
    sub_agents=[draft_agent, review_agent],
    max_iterations=3,  # required — ADK won't auto-terminate without this
)
```

**Termination**: LoopAgent does NOT auto-stop. Always set `max_iterations` or implement a condition in agent logic that signals completion.

---

## FunctionTool — Registering Custom Python Functions

ADK inspects function **name, docstring, and type hints** to auto-generate the tool schema. Two equivalent patterns:

```python
# Pattern 1: Pass function directly (ADK auto-wraps)
agent = LlmAgent(name="...", model="...", instruction="...", tools=[my_fn])

# Pattern 2: Explicit FunctionTool wrapper
from google.adk.tools import FunctionTool
agent = LlmAgent(name="...", model="...", instruction="...", tools=[FunctionTool(func=my_fn)])
```

### Return Type

Prefer `dict`. ADK auto-wraps non-dict returns as `{'result': your_value}`:

```python
def read_file(path: str) -> dict:
    """
    Read a markdown file from the wiki directory.

    Args:
        path: Path relative to wiki root, e.g. 'leads/acme-corp/john-smith.md'

    Returns:
        Dictionary with 'status' ('success' or 'error') and 'content' or 'error' keys.
    """
    try:
        with open(WIKI_ROOT / path) as f:
            return {"status": "success", "content": f.read()}
    except FileNotFoundError:
        return {"status": "error", "error": f"File not found: {path}"}
    except Exception as e:
        return {"status": "error", "error": str(e)}
```

**Critical**: The docstring `Args:` section is parsed to generate parameter descriptions. Write it clearly — it directly affects how well the model uses the tool.

### Tool Error Handling Pattern

```python
def write_file(path: str, content: str) -> dict:
    """Write content to a wiki file. Creates parent directories if needed."""
    try:
        full_path = (WIKI_ROOT / path).resolve()
        # Path safety: ensure within WIKI_ROOT
        full_path.relative_to(WIKI_ROOT.resolve())
        full_path.parent.mkdir(parents=True, exist_ok=True)
        full_path.write_text(content)
        return {"status": "success", "path": path, "bytes_written": len(content)}
    except ValueError:
        return {"status": "error", "error": "Path escapes wiki root"}
    except Exception as e:
        return {"status": "error", "error": str(e)}
```

ADK catches exceptions from tools and converts them to error results automatically. Returning structured error dicts (with a `status` field) gives the model better context to reason about failures.

---

## Running ADK Agents

### CLI (Dev/Debug)

```bash
adk run /path/to/agents/dir     # interactive terminal session
adk web                          # dev UI at http://localhost:8000 (NOT localhost:4200)
adk api_server --port 8000       # REST API server
```

> **Correction vs PRD**: The ADK dev UI runs on `localhost:8000`, not `localhost:4200`.

### Programmatic Execution (from Python / FastAPI)

```python
from google.adk.agents import LlmAgent, SequentialAgent
from google.adk.runners import InMemoryRunner
from google.adk.sessions import InMemorySessionService

# Build pipeline once at module level
pipeline = SequentialAgent(
    name="lead_processor",
    sub_agents=[ingest_agent, strategy_agent, response_agent],
)

# Create runner (reuse across requests)
session_service = InMemorySessionService()
runner = InMemoryRunner(agent=pipeline, session_service=session_service)

# Run from FastAPI endpoint
async def run_pipeline(lead_id: str):
    response = await runner.run_async(
        session_id=lead_id,
        user_id="system",
        new_message={"role": "user", "content": f"Process lead: {lead_id}"},
    )
    return response
```

**FastAPI integration**: Use `await runner.run_async(...)` from async route handlers. Module-level runner instantiation is correct — don't recreate it per request.

### Per-Node InMemoryRunner Usage (DAG Executor Pattern)

In the HappyRobot DAG executor, `InMemoryRunner` is instantiated **once per node**, not once per pipeline. Each `execute_node()` call creates a fresh runner for the single agent it is about to run. This is different from the pipeline-level pattern where one runner sequences all agents.

```python
from google.adk.runners import InMemoryRunner
from google.adk.sessions import InMemorySessionService

async def run_adk_agent(
    agent,                          # ADK LlmAgent instance
    user_message: str,              # prompt / task description for this node
    context: PipelineContext,       # lead_id, lead_wiki_path, channel, etc.
) -> str:
    """Run one ADK agent as a single node in the DAG. Returns the agent's final text output."""
    session_service = InMemorySessionService()
    runner = InMemoryRunner(agent=agent, app_name=agent.name, session_service=session_service)

    session = await session_service.create_session(
        app_name=agent.name,
        user_id=context.lead_id,
        # session_id is optional — if omitted, ADK auto-generates one.
        # Pass one explicitly for traceability in logs.
        session_id=f"{context.lead_id}-{agent.name}",
    )

    final_response = ""
    async for event in runner.run_async(
        user_id=context.lead_id,
        session_id=session.id,
        new_message=types.Content(role="user", parts=[types.Part(text=user_message)]),
    ):
        if event.is_final_response() and event.content:
            for part in event.content.parts:
                if part.text:
                    final_response += part.text

    return final_response
```

Key points:
- Session state is **not used for inter-agent communication**. Files on disk are the message bus.
- A fresh `InMemorySessionService` per node ensures no state bleeds between agents.
- `context.lead_id` is used as `user_id` for traceability in logs.
- The return value is the agent's final text output, stored by `execute_node()` in `pipeline_events.data`.

### PipelineContext — DAG Executor Context Object

The DAG executor passes execution context to each node via a `PipelineContext` dataclass defined in `api/dag_executor.py`:

```python
from dataclasses import dataclass

@dataclass
class PipelineContext:
    lead_id: str            # e.g. "logistics/acme-corp/john-smith"
    lead_wiki_path: str     # full path: "{WIKI_ROOT}/leads/logistics/acme-corp/john-smith"
    channel: str            # "email" | "linkedin" | "activecampaign" | "salesforce" | "manual"
    pipeline_name: str      # "inbound_responder"
    run_id: str             # UUID from pipeline_runs.id
    job_id: str             # legacy "{company}-{person}-{unix_ts}" key
```

This object is constructed once per `execute_pipeline()` call and passed down through `execute_dag()` → `execute_node()` → `run_adk_agent()`. It is NOT stored in ADK session state.

---

## Claude Models Inside ADK

ADK supports Claude models via Anthropic API:

```python
import os
os.environ["ANTHROPIC_API_KEY"] = "sk-ant-..."

response_agent = LlmAgent(
    name="response_agent",
    model="claude-sonnet-4-6",   # Claude model string format for ADK
    instruction=open("prompts/response.md").read(),
    tools=[read_file, search_wiki, write_file],
)
```

Valid Claude model strings in ADK:
- `"claude-sonnet-4-6"` — confirmed correct model ID for this project
- Requires `ANTHROPIC_API_KEY` in addition to `GOOGLE_API_KEY`

---

## Session State & Inter-Agent Communication

### What ADK Session State Is

All sub-agents in a SequentialAgent share `context.state` — a dict that persists within a single invocation. Agent 1 can write:

```python
context.state["ingest_notes"] = "Lead profile updated, 3 missing fields"
```

Agent 2 reads:

```python
notes = context.state.get("ingest_notes")
```

### HappyRobot Architecture: Files, Not Session State

**The HappyRobot project does NOT use ADK session state for inter-agent communication.** All agent communication happens through files on disk:

- IngestAgent validates wiki entries → writes `ingest-notes.md` (optional)
- StrategyAgent reads wiki → writes `strategy-memo.md` → this is the ONLY inter-agent message
- ResponseAgent reads `strategy-memo.md` → writes draft to `drafts/`

**Why**: File-based state is durable across process restarts, debuggable (just read the file), reproducible (same file = same output), and inspectable by humans. ADK session state disappears when the process ends.

**Implication for tool design**: All ADK agents use the same `read_file`/`write_file` tools as the scripts layer. These tools are the actual communication channel, not ADK session state.

---

## Agent Initialization Pattern (Recommended)

For the hackathon, module-level initialization is correct:

```python
# agents/pipeline.py

from google.adk.agents import LlmAgent, SequentialAgent
from tools.file_tools import read_file, write_file, list_directory, search_wiki

ingest_agent = LlmAgent(
    name="ingest_agent",
    model="gemini-2.5-flash",
    instruction=open("prompts/ingest.md").read(),
    tools=[read_file, list_directory, search_wiki, write_file],
)

strategy_agent = LlmAgent(
    name="strategy_agent",
    model="gemini-2.5-flash",
    instruction=open("prompts/strategy.md").read(),
    tools=[read_file, list_directory, search_wiki, write_file],
)

response_agent = LlmAgent(
    name="response_agent",
    model="gemini-2.5-flash",   # or "claude-sonnet-4-6" for quality
    instruction=open("prompts/response.md").read(),
    tools=[read_file, search_wiki, write_file],
)

lead_pipeline = SequentialAgent(
    name="lead_processor",
    sub_agents=[ingest_agent, strategy_agent, response_agent],
)
```

---

## ADK Built-In Tools (Available if Needed)

```python
from google.adk.tools import google_search   # ← from google.adk.tools

# Add Google Search grounding to StrategyAgent (optional enhancement)
strategy_agent = LlmAgent(
    name="strategy_agent",
    model="gemini-2.5-flash",
    instruction="...",
    tools=[read_file, search_wiki, google_search],  # google_search is an ADK built-in
)
```

**Code execution** is a separate module — import it differently:
```python
from google.adk.code_executors import BuiltInCodeExecutor   # NOT from google.adk.tools

agent = LlmAgent(
    name="...",
    model="gemini-2.5-flash",
    instruction="...",
    tools=[...],
    code_executor=BuiltInCodeExecutor(),    # passed as code_executor, not in tools list
)
```

---

## Key Gotchas

| Gotcha | Correct approach |
|--------|-----------------|
| Import path | `from google.adk.agents import LlmAgent` (not `from google.adk import Agent`) |
| SequentialAgent param | `sub_agents=[...]` (not `agents=[...]`) |
| Dev UI port | `localhost:8000` (not `localhost:4200` as some older docs say) |
| Auth for local dev | Set `GOOGLE_GENAI_USE_VERTEXAI=false` or Gemini API key won't work |
| LoopAgent termination | Always set `max_iterations` — ADK won't auto-stop |
| Tool return type | Return `dict`, not `str` — LLM gets better context |
| Docstring quality | Drives tool schema quality — write clear `Args:` and `Returns:` |
| Claude in ADK | Uses `"claude-sonnet-4-6"` model string (same ID as direct SDK); needs `ANTHROPIC_API_KEY` in env |
| Session state | Don't use for inter-agent messages in HappyRobot — use files only |

---

## References

- [ADK Documentation](https://google.github.io/adk-docs)
- [Sequential Agents](https://google.github.io/adk-docs/agents/workflow-agents/sequential-agents/)
- [Function Tools](https://google.github.io/adk-docs/tools/function-tools/)
- [Claude Models in ADK](https://google.github.io/adk-docs/agents/models/anthropic/)
- [ADK Python GitHub](https://github.com/google/adk-python)
- [ADK Samples](https://github.com/google/adk-samples)
