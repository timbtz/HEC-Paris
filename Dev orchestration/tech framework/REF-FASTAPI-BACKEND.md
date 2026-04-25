# FastAPI Backend Reference Guide
## HappyRobot Backend — Ingestion API Layer

> Research-validated reference for Claude instances building the FastAPI webhook and pipeline API.
> FastAPI 0.115+, Pydantic v2, Python 3.12, Uvicorn, systemd.

---

## Project Structure

```
api/
├── main.py              # FastAPI app + lifespan (3 background tasks + SQLite init)
├── dag_executor.py      # buildTopologicalLayers, execute_pipeline, execute_dag, resume_pipeline
├── pipeline_def.py      # PipelineNode, ApprovalNode, INBOUND_PIPELINE dataclasses
├── routes/
│   ├── __init__.py
│   ├── webhooks.py      # POST /webhook/interaction, POST /ingest/manual, POST /webhooks/crm
│   ├── pipelines.py     # POST /pipeline/run/{pipeline_name}
│   ├── drafts.py        # GET /drafts, PATCH /drafts/{id}/approve (DAG resume)
│   ├── wiki.py          # GET /wiki/{path}
│   └── status.py        # GET /status, GET /pipeline/active, GET /pipeline/status/{job_id}
├── models.py            # Pydantic v2 request/response models (webhook schemas)
├── dependencies.py      # Shared dependencies (webhook secret validator, get_db)
├── queue.py             # compile_worker, pipeline_scheduler, scheduled_send_loop
└── config.py            # Path constants, env loading
```

---

## App Initialization with Lifespan

Use the `lifespan` context manager (the modern pattern — `@app.on_event` is deprecated in FastAPI 0.115+):

```python
# api/main.py
import asyncio
from contextlib import asynccontextmanager
import aiosqlite
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from .queue import compile_worker, pipeline_scheduler, scheduled_send_loop
from .routes import webhooks, pipelines, drafts, wiki, status
from .config import DB_PATH
from .db import init_schema

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Open single long-lived SQLite connection (shared by all background tasks + handlers)
    db = await aiosqlite.connect(DB_PATH)
    db.row_factory = aiosqlite.Row
    await db.execute("PRAGMA journal_mode=WAL")
    await db.execute("PRAGMA foreign_keys=ON")
    await db.commit()
    await init_schema(db)
    app.state.db = db

    # Start 3 background tasks — each has a distinct job (never merge into one queue)
    asyncio.create_task(compile_worker())
    asyncio.create_task(pipeline_scheduler(db))
    asyncio.create_task(scheduled_send_loop(db))

    yield
    await db.close()

app = FastAPI(title="HappyRobot Backend", version="1.0.0", lifespan=lifespan)

# CORS must be added BEFORE including routers
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "https://your-dashboard-domain.com"],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH"],
    allow_headers=["Content-Type", "X-Webhook-Secret"],
)

app.include_router(webhooks.router, prefix="/webhook", tags=["webhooks"])
app.include_router(pipelines.router, prefix="/pipeline", tags=["pipeline"])
app.include_router(drafts.router, tags=["drafts"])
app.include_router(wiki.router, tags=["wiki"])
app.include_router(status.router, tags=["status"])
```

> **CORS gotcha**: `allow_origins=["*"]` and `allow_credentials=True` are **mutually exclusive**. Always list origins explicitly when using credentials.

---

## Async Compile Queue

The compile operation takes 30–120 seconds (Anthropic API agentic loop). The webhook must respond in <2 seconds. Solution: background queue worker.

```python
# api/queue.py
import asyncio
import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

compile_queue: asyncio.Queue[dict] = asyncio.Queue()
active_compiles: set[str] = set()   # track in-progress compiles per lead_id
_worker_task: Optional[asyncio.Task] = None


async def compile_worker():
    """Infinite consumer loop. Processes compile jobs sequentially."""
    while True:
        try:
            job = await compile_queue.get()
            lead_id = job["lead_id"]
            interaction_file = job["interaction_file"]

            try:
                if lead_id in active_compiles:
                    logger.warning(f"Compile already running for {lead_id}, skipping duplicate")
                    compile_queue.task_done()
                    continue

                active_compiles.add(lead_id)

                # Run compile.py as subprocess (async — doesn't block event loop)
                # compile.py accepts --lead {industry}/{company}/{person} (not --file)
                proc = await asyncio.create_subprocess_exec(
                    "python", "-m", "scripts.compile",
                    "--lead", lead_id,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    cwd="/opt/happyrobot",
                )
                stdout, stderr = await proc.communicate()

                if proc.returncode != 0:
                    logger.error(f"Compile failed for {lead_id}: {stderr.decode()}")
                else:
                    logger.info(f"Compile succeeded for {lead_id}")

            finally:
                active_compiles.discard(lead_id)
                compile_queue.task_done()

        except asyncio.CancelledError:
            logger.info("Compile worker shutting down")
            break
        except Exception as e:
            logger.exception(f"Compile worker unexpected error: {e}")
            await asyncio.sleep(1)   # brief backoff before next job


async def start_compile_worker():
    global _worker_task
    _worker_task = asyncio.create_task(compile_worker())


async def stop_compile_worker():
    global _worker_task
    if _worker_task:
        _worker_task.cancel()
        try:
            await _worker_task
        except asyncio.CancelledError:
            pass


async def enqueue_compile(lead_id: str, interaction_file: str):
    """Called from webhook handler. Returns immediately."""
    await compile_queue.put({"lead_id": lead_id, "interaction_file": interaction_file})
```

### Three Background Tasks (Canonical Pattern)

The FastAPI lifespan starts **three separate background tasks**. Never merge these into a single queue.

```python
# api/queue.py

import asyncio
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

compile_queue: asyncio.Queue = asyncio.Queue()
pending_pipelines: list["PendingPipeline"] = []
pending_lock: asyncio.Lock = asyncio.Lock()

@dataclass
class PendingPipeline:
    lead_id: str
    lead_display: str
    pipeline_name: str
    job_context: dict
    fire_at: datetime        # = receipt_time + RESPONSE_DELAY_SECONDS (default: 900)

# Task 1 — drain compile queue immediately, one at a time
async def compile_worker():
    while True:
        job = await compile_queue.get()
        try:
            await run_compile(job.lead_id)
        except CompileError as e:
            log.error(f"[compile] {e}")
        finally:
            compile_queue.task_done()

# Task 2 — fire pipelines after delay window elapses (poll every 30s)
async def pipeline_scheduler(db: aiosqlite.Connection):
    while True:
        now = datetime.utcnow()
        async with pending_lock:
            ready = [p for p in pending_pipelines if p.fire_at <= now]
            for p in ready:
                pending_pipelines.remove(p)
                asyncio.create_task(
                    execute_pipeline(p.lead_id, p.lead_display, p.pipeline_name, p.job_context, db)
                )
        await asyncio.sleep(30)

# Task 3 — dispatch scheduled drafts (send_after) every 60s — bypasses DAG
async def scheduled_send_loop(db: aiosqlite.Connection):
    while True:
        await _dispatch_ready_scheduled_drafts(db)
        await asyncio.sleep(60)
```

**Lifespan start (api/main.py):**
```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    db = await aiosqlite.connect(DB_PATH)
    db.row_factory = aiosqlite.Row
    await db.execute("PRAGMA journal_mode=WAL")
    await db.execute("PRAGMA foreign_keys=ON")
    await db.commit()
    await init_schema(db)
    app.state.db = db

    asyncio.create_task(compile_worker())
    asyncio.create_task(pipeline_scheduler(db))
    asyncio.create_task(scheduled_send_loop(db))
    yield
    await db.close()
```

**On webhook receipt (sequencing):**
```python
# 1. Write raw interaction file (immutable after this)
write_raw_interaction_file(lead_path, channel, payload)
# 2. Insert messages row (SQLite dual-write)
await db.execute("INSERT OR REPLACE INTO messages ...", params)
await db.commit()
# 3. Enqueue compile (starts within seconds)
await compile_queue.put(CompileJob(lead_id=lead_id))
# 4. Schedule pipeline (fires after RESPONSE_DELAY_SECONDS)
async with pending_lock:
    pending_pipelines.append(PendingPipeline(
        lead_id=lead_id,
        fire_at=datetime.utcnow() + timedelta(seconds=RESPONSE_DELAY_SECONDS),
        ...
    ))
```

### asyncio.create_task() vs BackgroundTasks

| | `BackgroundTasks` | `asyncio.create_task()` |
|-|-------------------|------------------------|
| Runs after response | Yes | Yes |
| Waits on shutdown | Yes (blocks Uvicorn) | No (task is independent) |
| For 30-120s work | Not recommended | Correct choice |
| Reference tracking | Automatic | Must keep strong reference |

For long-running operations, always use `asyncio.create_task()` (used internally by the queue pattern above, not raw `create_task` from route handlers — routing via the queue is cleaner).

---

## Pydantic v2 Models

```python
# api/models.py
from pydantic import BaseModel, Field, field_validator
from datetime import datetime
from typing import Optional


class InteractionPayload(BaseModel):
    """Canonical webhook body for POST /pipeline/run/{pipeline_name}.
    Also accepted at POST /webhook/interaction for direct ingestion."""
    lead_id: str = Field(..., description="Full lead path: '{industry}/{company}/{person}'")
    channel: str = Field(..., description="Channel: email | linkedin | activecampaign | salesforce | manual")
    raw_message: str = Field(..., description="Raw message/interaction content")
    trigger_source: str = Field(
        ...,
        description="gmail_push | activecampaign_webhook | salesforce_webhook | makecom_linkedin | manual"
    )
    delay_override_seconds: Optional[int] = Field(
        default=None,
        description="Override the 15-min default delay (null = use RESPONSE_DELAY_SECONDS env var)"
    )

    @field_validator("channel")    # Pydantic v2 syntax
    @classmethod                    # required in v2
    def validate_channel(cls, v: str) -> str:
        valid = {"email", "linkedin", "activecampaign", "salesforce", "manual"}
        if v not in valid:
            raise ValueError(f"channel must be one of {valid}")
        return v

    @field_validator("lead_id")
    @classmethod
    def validate_lead_id(cls, v: str) -> str:
        """Ensure format: {industry}/{company}/{person} — three slug segments."""
        parts = v.split("/")
        if len(parts) != 3 or not all(p.replace("-", "").isalnum() for p in parts):
            raise ValueError("lead_id must be '{industry}/{company}/{person}' with slug segments")
        return v.lower()

    @field_validator("company", "person")
    @classmethod
    def slugify_check(cls, v: str) -> str:
        """Ensure slug format: lowercase, hyphens only."""
        if not v.replace("-", "").isalnum():
            raise ValueError("Must be slug format: lowercase letters, hyphens only")
        return v.lower()


class PipelineRunResponse(BaseModel):
    """Response from POST /pipeline/run/{pipeline_name}."""
    job_id: str
    pipeline: str
    lead_id: str
    status: str                   # "queued"
    scheduled_run_at: str         # ISO-8601 UTC — when the pipeline will actually fire


class WikiFileResponse(BaseModel):
    path: str
    content: str
    modified_at: float


class DraftListItem(BaseModel):
    draft_id: str
    lead_id: str
    channel: str
    created_at: str
    path: str
    status: str                   # awaiting_approval | approved | sent | scheduled | send_failed
    run_id: Optional[str] = None  # links draft to pipeline_runs row for approval-resume


class ApproveResponse(BaseModel):
    status: str                   # "resumed"
    run_id: str
    pipeline: str
```

**Pydantic v2 key changes from v1:**
- `@validator` → `@field_validator` (must add `@classmethod`)
- `@root_validator` → `@model_validator(mode="after")`
- `Optional[X]` fields still work but prefer `X | None` syntax in 3.10+

---

## Webhook Secret Validation

```python
# api/dependencies.py
import os
from typing import Annotated
from fastapi import Header, HTTPException, status


async def verify_webhook_secret(
    x_webhook_secret: Annotated[Optional[str], Header()] = None
) -> str:
    """
    Validate X-Webhook-Secret header against WEBHOOK_SECRET env var.
    Raises 401 if missing or invalid.
    """
    expected = os.getenv("WEBHOOK_SECRET")
    if not expected:
        raise HTTPException(status_code=500, detail="Server misconfiguration: WEBHOOK_SECRET not set")
    if not x_webhook_secret or x_webhook_secret != expected:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing X-Webhook-Secret",
        )
    return x_webhook_secret
```

Usage in routes:
```python
from typing import Annotated
from fastapi import Depends
from .dependencies import verify_webhook_secret

@router.post("/interaction", status_code=202)
async def receive_interaction(
    payload: InteractionPayload,
    _: Annotated[str, Depends(verify_webhook_secret)],  # validates secret, result unused
):
    ...
```

---

## Webhook Route — Full Pattern

```python
# api/routes/webhook.py
import os
from pathlib import Path
from datetime import datetime
from typing import Annotated
from fastapi import APIRouter, Depends, status
from ..models import InteractionPayload
from ..dependencies import verify_webhook_secret
from ..queue import enqueue_compile

router = APIRouter()
WIKI_ROOT = Path(os.getenv("WIKI_ROOT", "/opt/happyrobot/wiki"))


@router.post("/interaction", status_code=status.HTTP_202_ACCEPTED)
async def receive_interaction(
    payload: InteractionPayload,
    _: Annotated[str, Depends(verify_webhook_secret)],
):
    """
    Receive interaction, write to disk, enqueue compile.
    Returns 202 immediately — compile runs in background.
    """
    # Build file path
    interactions_dir = WIKI_ROOT / "leads" / payload.company / "interactions"
    interactions_dir.mkdir(parents=True, exist_ok=True)

    timestamp_str = payload.timestamp.strftime("%Y-%m-%d-%H%M%S")
    filename = f"{timestamp_str}-{payload.channel}.md"
    filepath = interactions_dir / filename

    # Write interaction markdown (sync I/O is fine for small files)
    content = f"""# {payload.person.replace('-', ' ').title()} — {payload.channel.upper()}

**Date**: {payload.timestamp.isoformat()}
**Company**: {payload.company}
**Person**: {payload.person}
**Channel**: {payload.channel}

## Raw Content

{payload.raw_content}
"""
    filepath.write_text(content)

    # Enqueue compile (returns immediately)
    lead_id = f"{payload.company}/{payload.person}"
    await enqueue_compile(lead_id=lead_id, interaction_file=str(filepath))

    return {"status": "queued", "file": str(filepath.relative_to(WIKI_ROOT))}
```

---

## Pipeline Run Endpoint

```python
# api/routes/pipelines.py
from fastapi import APIRouter, Depends, Request
from datetime import datetime, timedelta
import uuid
from ..models import InteractionPayload, PipelineRunResponse
from ..queue import pending_pipelines, pending_lock, PendingPipeline
from ..config import RESPONSE_DELAY_SECONDS
from ..dependencies import get_db

router = APIRouter()

@router.post("/run/{pipeline_name}", status_code=202, response_model=PipelineRunResponse)
async def trigger_pipeline(
    pipeline_name: str,
    payload: InteractionPayload,
    request: Request,
    db = Depends(get_db),
):
    """
    Canonical pipeline trigger — called by Make.com, ActiveCampaign, Salesforce, and manual CLI.
    Queues lead for processing after RESPONSE_DELAY_SECONDS (default: 900s / 15 min).

    Supersedes POST /pipeline/run/{lead_id} from PRD 1.
    """
    delay = payload.delay_override_seconds or RESPONSE_DELAY_SECONDS
    fire_at = datetime.utcnow() + timedelta(seconds=delay)
    job_id = f"{payload.lead_id.replace('/', '-')}-{int(fire_at.timestamp())}"

    async with pending_lock:
        pending_pipelines.append(PendingPipeline(
            lead_id=payload.lead_id,
            lead_display=payload.lead_id,
            pipeline_name=pipeline_name,
            job_context={
                "channel": payload.channel,
                "raw_message": payload.raw_message,
                "trigger_source": payload.trigger_source,
                "job_id": job_id,
            },
            fire_at=fire_at,
        ))

    return PipelineRunResponse(
        job_id=job_id,
        pipeline=pipeline_name,
        lead_id=payload.lead_id,
        status="queued",
        scheduled_run_at=fire_at.isoformat() + "Z",
    )
```

---

## Pipeline Status Endpoints

`GET /pipeline/active` and `GET /pipeline/status/{job_id}` are backed by `pipeline_runs` + `pipeline_events` SQLite tables (PRD 4), **not** an in-memory dict or `state.json`.

```python
# GET /pipeline/status/{job_id} — per-node status with elapsed times
# Response shape (additive vs Phase 3 — dashboard JS needs no changes):
{
    "run_id": "uuid-1234",
    "job_id": "acme-corp-john-smith-1713456000",
    "pipeline": "inbound_responder",
    "lead_id": "logistics/acme-corp/john-smith",
    "status": "running",           # running | completed | failed | paused | cancelled
    "resume_count": 0,
    "created_at": "2026-04-18T10:00:00Z",
    "nodes": [
        {"node_id": "wiki-readiness", "status": "completed", "elapsed_ms": 3800},
        {"node_id": "strategy",       "status": "running",   "elapsed_ms": 62000},
        {"node_id": "response",       "status": "pending"},
        {"node_id": "draft-approval", "status": "pending"},
        {"node_id": "sender",         "status": "pending"}
    ]
}

# GET /pipeline/active — all runs with status='running' or 'paused'
```

---

## Safe Wiki File Serving

Path traversal prevention is critical — wiki files are on disk and must stay within WIKI_ROOT:

```python
# api/routes/wiki.py
import os
from pathlib import Path
from typing import Optional
from fastapi import APIRouter, HTTPException, status
from fastapi.responses import Response

router = APIRouter()
WIKI_ROOT = Path(os.getenv("WIKI_ROOT", "/opt/happyrobot/wiki"))


@router.get("/wiki/{path:path}")
async def serve_wiki_file(path: str):
    """Serve a wiki markdown file. Prevents path traversal."""
    if not path or path.endswith("/"):
        raise HTTPException(status_code=403, detail="Directory listing not allowed")

    # Resolve and verify within WIKI_ROOT
    requested = (WIKI_ROOT / path).resolve()
    try:
        requested.relative_to(WIKI_ROOT.resolve())   # raises ValueError if outside
    except ValueError:
        raise HTTPException(status_code=403, detail="Access denied")

    if not requested.is_file():
        raise HTTPException(status_code=404, detail="File not found")

    content = requested.read_text()
    return Response(
        content=content,
        media_type="text/markdown; charset=utf-8",
    )


@router.get("/drafts")
async def list_drafts():
    """List pending draft files."""
    drafts_dir = WIKI_ROOT / "drafts"
    if not drafts_dir.exists():
        return {"drafts": []}

    drafts = []
    for f in sorted(drafts_dir.glob("*.md")):
        parts = f.stem.split("-")
        drafts.append({
            "draft_id": f.stem,
            "path": str(f.relative_to(WIKI_ROOT)),
            "created_at": f.stat().st_mtime,
        })
    return {"drafts": drafts}


@router.patch("/drafts/{draft_id}/approve")
async def approve_draft(
    draft_id: str,
    request: Request,
    db: aiosqlite.Connection = Depends(get_db),
):
    """
    Approve a pending draft. Reads run_id from draft frontmatter and calls
    resume_pipeline() to continue the DAG from after the ApprovalNode.
    Phase 3 behavior (moving to approved/ folder) is SUPERSEDED by this PRD 4 pattern.
    """
    draft_path = (WIKI_ROOT / "drafts" / f"{draft_id}.md").resolve()
    try:
        draft_path.relative_to(WIKI_ROOT.resolve())
    except ValueError:
        raise HTTPException(status_code=403, detail="Access denied")
    if not draft_path.is_file():
        raise HTTPException(status_code=404, detail="Draft not found")

    # 1. Parse run_id from draft frontmatter
    fm = parse_draft_frontmatter(draft_path)    # returns dict from YAML block
    run_id = fm.get("run_id")
    if not run_id:
        raise HTTPException(status_code=422, detail="Draft is missing run_id frontmatter field")

    # 2. Write approval_received event and node_completed for draft-approval node
    await db.execute(
        "INSERT INTO pipeline_events (run_id, event_type, node_id, payload, created_at) "
        "VALUES (?, 'approval_received', 'draft-approval', ?, datetime('now'))",
        (run_id, json.dumps({"approved_by": "manager", "draft_id": draft_id})),
    )
    await db.execute(
        "INSERT INTO pipeline_events (run_id, event_type, node_id, payload, created_at) "
        "VALUES (?, 'node_completed', 'draft-approval', ?, datetime('now'))",
        (run_id, json.dumps({"approved": True})),
    )
    await db.commit()

    # 3. Resume the DAG synchronously (SenderAgent runs in this request's async task)
    asyncio.create_task(resume_pipeline(run_id, db))

    return {"status": "resumed", "run_id": run_id, "pipeline": fm.get("pipeline_name")}
```

---

## Running Subprocess Async

**Never** call `subprocess.run()` from an async route handler — it blocks the event loop.

```python
# WRONG — blocks event loop for 30-120 seconds
result = subprocess.run(["python", "-m", "scripts.compile", "--lead", lead_id])

# CORRECT — async subprocess (use in background worker, not route handler)
# compile.py CLI: --lead {industry}/{company}/{person}  |  --all  |  (no flag = all changed)
proc = await asyncio.create_subprocess_exec(
    "python", "-m", "scripts.compile",
    "--lead", lead_id,
    stdout=asyncio.subprocess.PIPE,
    stderr=asyncio.subprocess.PIPE,
    cwd="/opt/happyrobot",
)
stdout, stderr = await proc.communicate()   # await blocks the worker coroutine, not the event loop
```

The pattern is: route handler → enqueue → worker coroutine → `await create_subprocess_exec`.

### Invoking compile.py as Async Subprocess

`compile.py` is always invoked as an async subprocess — never imported directly (blocks event loop).

```python
async def run_compile(lead_id: str, force: bool = False) -> None:
    """Compile wiki articles for one lead. Non-blocking."""
    cmd = ["python", "-m", "scripts.compile", "--lead", lead_id]
    if force:
        cmd.append("--all")
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        env={**os.environ},
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=Path(__file__).parent.parent,   # repo root
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise CompileError(f"compile.py failed for {lead_id}:\n{stderr.decode()}")
```

Required `compile.py` CLI flags (must be implemented in `scripts/compile.py`):
- `--lead {lead_id}` — compile only this lead
- `--all` — with `--lead`: force-recompile all interactions regardless of SHA hash
- _(no flags)_ — existing behavior: compile all changed interactions across all leads

---

## File I/O: Sync vs Async

For the MVP, **synchronous file I/O is fine** for the small markdown files (typically <50KB):

```python
# Fine for small files in async handler
with open(filepath, "w") as f:
    f.write(content)
```

Use `aiofiles` only if:
- Files are >10MB, or
- You have >100 concurrent webhook requests writing files simultaneously

```python
import aiofiles

async with aiofiles.open(filepath, "w") as f:
    await f.write(content)
```

---

## Systemd Service

```ini
# /etc/systemd/system/happyrobot.service

[Unit]
Description=HappyRobot Backend API
After=network.target

[Service]
Type=simple
User=developer
Group=developer

# Load .env file (one KEY=VALUE per line, no export prefix, no quotes)
EnvironmentFile=/opt/happyrobot/.env

WorkingDirectory=/opt/happyrobot

# Uvicorn command — single worker for MVP (asyncio queue is in-process)
ExecStart=/usr/local/bin/uv run uvicorn api.main:app --host 0.0.0.0 --port 8001

Restart=on-failure
RestartSec=5
TimeoutStopSec=30
KillSignal=SIGTERM

StandardOutput=journal
StandardError=journal
SyslogIdentifier=happyrobot

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable happyrobot
sudo systemctl start happyrobot
sudo journalctl -u happyrobot -f   # follow logs
```

> **Important**: Use a **single worker** (`--workers 1`) with the in-memory asyncio.Queue. Multiple workers would have separate queues and separate state, breaking the deduplication logic. For the hackathon MVP, one worker is correct.

---

## Status Endpoint

```python
# api/routes/status.py
import json
from pathlib import Path
from fastapi import APIRouter

router = APIRouter()
SCRIPTS_DIR = Path("/opt/happyrobot/scripts")


@router.get("/status")
async def get_status():
    """Health check + cost/compile stats from state.json."""
    state_path = SCRIPTS_DIR / "state.json"
    state = {}
    if state_path.exists():
        state = json.loads(state_path.read_text())

    return {
        "status": "ok",
        "compiled_count": len(state.get("compiled", {})),
        "total_cost_usd": state.get("total_cost_usd", 0),
        "query_count": state.get("query_count", 0),
        "last_lint": state.get("last_lint"),
    }
```

> **Note:** `GET /pipeline/status/{job_id}` and `GET /pipeline/active` are backed by `pipeline_runs` and `pipeline_events` SQLite tables (PRD 4 §7), not `state.json` or an in-memory dict. See PRD 4 §9 for the complete response shapes including per-node status arrays.

---

## Key Gotchas

| Gotcha | Correct approach |
|--------|-----------------|
| BackgroundTasks for 30-120s work | Use asyncio.Queue + worker instead |
| `@app.on_event("startup")` | Deprecated — use `lifespan` context manager |
| subprocess.run() in async handler | Use `asyncio.create_subprocess_exec()` in worker |
| CORS `allow_origins=["*"]` + credentials | Invalid — list origins explicitly |
| Path traversal in file serving | Use `Path.resolve()` + `.relative_to(WIKI_ROOT)` check |
| Multiple Uvicorn workers | Use single worker with in-memory queue for MVP |
| Pydantic v2 @validator | Use `@field_validator` + `@classmethod` |
| EnvironmentFile format | No `export`, no quotes around values |
| Queue task_done() | Must call after every job or `queue.join()` will hang |

---

## References

- [FastAPI Documentation](https://fastapi.tiangolo.com)
- [FastAPI Background Tasks](https://fastapi.tiangolo.com/tutorial/background-tasks/)
- [FastAPI Lifespan Events](https://fastapi.tiangolo.com/advanced/events/)
- [Pydantic v2 Validators](https://docs.pydantic.dev/latest/concepts/validators/)
- [asyncio.Queue](https://docs.python.org/3/library/asyncio-queue.html)
- [asyncio.create_subprocess_exec](https://docs.python.org/3/library/asyncio-subprocess.html)
