"""Layer-by-layer DAG executor.

Source: 01_ORCHESTRATION_REFERENCE.md:49-105 (orchestrator + per-node
wrapper); RealMetaPRD §6.4 line 504 (`asyncio.gather(..., return_exceptions=
True)` semantics); REF-SSE-STREAMING-FASTAPI.md:217-249 (write_event
dual-write pattern).

Event invariant: a clean N-node run emits 2N+2 pipeline_events rows
(`pipeline_started` + 2 per node + `pipeline_completed`). RealMetaPRD §11
line 1554.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
import traceback
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, TYPE_CHECKING

from . import audit as audit_mod
from . import cache as cache_mod
from . import event_bus
from .context import AgnesContext
from .dag import topological_layers
from .registries import get_agent, get_condition, get_runner, get_tool
from .runners.base import AgentResult
from .store.writes import write_tx
from .yaml_loader import Pipeline, PipelineNode, load as load_pipeline

if TYPE_CHECKING:
    from .store.bootstrap import StoreHandles

logger = logging.getLogger(__name__)

_PIPELINES_DIR = Path(__file__).resolve().parent / "pipelines"

# Tracks in-flight runs so tests (and graceful shutdown) can await them.
_run_tasks: dict[int, asyncio.Task] = {}


@dataclass(frozen=True)
class _NodeOutcome:
    node_id: str
    output: Any
    error: str | None
    was_skipped: bool
    was_cache_hit: bool
    elapsed_ms: int


# --------------------------------------------------------------------------- #
# Public entry points
# --------------------------------------------------------------------------- #

async def execute_pipeline(
    pipeline_name: str,
    *,
    trigger_source: str,
    trigger_payload: dict[str, Any],
    store: "StoreHandles",
    employee_id: int | None = None,
    background: bool = True,
    pipelines_dir: Path | None = None,
) -> int:
    """Public entry. Inserts a pipeline_runs row and schedules _execute.

    With `background=True` (production): returns the run_id once the row is
    persisted; the task runs concurrently. With `background=False` (tests):
    awaits completion before returning.
    """
    pipelines_dir = pipelines_dir or _PIPELINES_DIR
    pipeline = load_pipeline(pipelines_dir / f"{pipeline_name}.yaml")

    run_id = await _insert_run(
        store,
        pipeline_name=pipeline.name,
        pipeline_version=pipeline.version,
        trigger_source=trigger_source,
        trigger_payload=trigger_payload,
        employee_id=employee_id,
    )

    ctx = AgnesContext(
        run_id=run_id,
        pipeline_name=pipeline.name,
        trigger_source=trigger_source,
        trigger_payload=trigger_payload,
        node_outputs={},
        store=store,
        employee_id=employee_id,
    )

    coro = _execute(ctx, pipeline)
    if background:
        task = asyncio.create_task(coro)
        _run_tasks[run_id] = task
        task.add_done_callback(lambda _t, _rid=run_id: _run_tasks.pop(_rid, None))
    else:
        await coro
    return run_id


async def wait_for_run(run_id: int) -> None:
    """Test/shutdown helper — await a previously-scheduled run."""
    task = _run_tasks.get(run_id)
    if task is not None:
        await task


# --------------------------------------------------------------------------- #
# Orchestrator loop
# --------------------------------------------------------------------------- #

async def _execute(ctx: AgnesContext, pipeline: Pipeline) -> None:
    await write_event(ctx, "pipeline_started", None,
                      {"pipeline_name": pipeline.name, "version": pipeline.version})
    try:
        layers = topological_layers(pipeline.nodes)
        for layer_index, layer in enumerate(layers):
            logger.info("dag.layer_started",
                        extra={"run_id": ctx.run_id, "layer_index": layer_index})

            results = await asyncio.gather(
                *[_run_node(node, ctx) for node in layer],
                return_exceptions=True,
            )

            failed_error: str | None = None
            for node, outcome in zip(layer, results):
                if isinstance(outcome, BaseException):
                    err = f"{type(outcome).__name__}: {outcome}"
                    await write_event(ctx, "node_failed", node.id, {"error": err})
                    failed_error = failed_error or err
                    continue

                assert isinstance(outcome, _NodeOutcome)
                if outcome.error is not None:
                    await write_event(ctx, "node_failed", outcome.node_id,
                                      {"error": outcome.error})
                    failed_error = failed_error or outcome.error
                    continue

                if outcome.was_skipped:
                    await write_event(ctx, "node_skipped", outcome.node_id,
                                      {"elapsed_ms": outcome.elapsed_ms})
                else:
                    ctx.node_outputs[outcome.node_id] = outcome.output
                    await write_event(ctx, "node_completed", outcome.node_id,
                                      {"elapsed_ms": outcome.elapsed_ms,
                                       "node_output": _safe_for_json(outcome.output),
                                       "cache_hit": outcome.was_cache_hit})

            if failed_error is not None:
                await write_event(ctx, "pipeline_failed", None,
                                  {"error": failed_error})
                await _update_run_status(ctx, status="failed", error=failed_error)
                return

        await write_event(ctx, "pipeline_completed", None, {})
        await _update_run_status(ctx, status="completed", error=None)

    except BaseException as exc:  # noqa: BLE001
        err = f"{type(exc).__name__}: {exc}"
        await write_event(ctx, "pipeline_failed", None,
                          {"error": err, "traceback": traceback.format_exc()})
        await _update_run_status(ctx, status="failed", error=err)
        if isinstance(exc, asyncio.CancelledError):
            raise


# --------------------------------------------------------------------------- #
# Per-node wrapper
# --------------------------------------------------------------------------- #

async def _run_node(node: PipelineNode, ctx: AgnesContext) -> _NodeOutcome:
    start = time.monotonic()
    try:
        # `when:` short-circuit
        if node.when is not None:
            cond = get_condition(node.when)
            if not bool(cond(ctx)):
                return _NodeOutcome(
                    node_id=node.id, output=None, error=None,
                    was_skipped=True, was_cache_hit=False,
                    elapsed_ms=int((time.monotonic() - start) * 1000),
                )

        await write_event(ctx, "node_started", node.id, {})

        canonical_input = _canonical_node_input(node, ctx)
        cache_key_value: str | None = None
        if node.cacheable:
            cache_key_value = cache_mod.cache_key(node.id, canonical_input)
            cached = await cache_mod.lookup(ctx.store.orchestration, cache_key_value)
            if cached is not None:
                await cache_mod.record_hit(
                    ctx.store.orchestration, ctx.store.orchestration_lock, cache_key_value,
                )
                await write_event(ctx, "cache_hit", node.id, {"cache_key": cache_key_value})
                return _NodeOutcome(
                    node_id=node.id, output=cached.get("output"),
                    error=None, was_skipped=False, was_cache_hit=True,
                    elapsed_ms=int((time.monotonic() - start) * 1000),
                )

        # Dispatch
        if node.is_tool:
            output = await _dispatch_tool(node, ctx)
        else:
            output = await _dispatch_agent(node, ctx)

        # Cache write-back (tools only — agents opt out by convention)
        if node.cacheable and cache_key_value is not None:
            await cache_mod.store(
                ctx.store.orchestration, ctx.store.orchestration_lock,
                key=cache_key_value, node_id=node.id,
                pipeline_name=ctx.pipeline_name,
                input_json=canonical_input,
                output_json={"output": _safe_for_json(output)},
            )

        return _NodeOutcome(
            node_id=node.id, output=output, error=None,
            was_skipped=False, was_cache_hit=False,
            elapsed_ms=int((time.monotonic() - start) * 1000),
        )

    except asyncio.CancelledError:
        raise
    except BaseException as exc:  # noqa: BLE001
        return _NodeOutcome(
            node_id=node.id, output=None,
            error=f"{type(exc).__name__}: {exc}\n{traceback.format_exc()}",
            was_skipped=False, was_cache_hit=False,
            elapsed_ms=int((time.monotonic() - start) * 1000),
        )


async def _dispatch_tool(node: PipelineNode, ctx: AgnesContext) -> Any:
    fn = get_tool(node.tool)  # type: ignore[arg-type]
    if asyncio.iscoroutinefunction(fn):
        return await fn(ctx)
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, fn, ctx)


async def _dispatch_agent(node: PipelineNode, ctx: AgnesContext) -> Any:
    """Resolve agent + runner, call agent (which calls runner), then audit."""
    agent_fn = get_agent(node.agent)  # type: ignore[arg-type]
    if not asyncio.iscoroutinefunction(agent_fn):
        raise TypeError(f"agent '{node.agent}' must be async")

    result = await agent_fn(ctx)

    if not isinstance(result, AgentResult):
        # Agents may also return raw dicts (e.g. cached path); only audit when
        # we got a full AgentResult. For Phase 1 the noop_agent returns one.
        return result

    provider = _provider_for_runner(node.runner or "")
    await audit_mod.propose_checkpoint_commit(
        audit_db=ctx.store.audit,
        audit_lock=ctx.store.audit_lock,
        run_id=ctx.run_id,
        node_id=node.id,
        result=result,
        runner=node.runner or "",
        employee_id=ctx.employee_id,
        provider=provider,
    )
    return result.output


def _provider_for_runner(runner_key: str) -> str:
    if runner_key == "anthropic":
        return "anthropic"
    if runner_key == "pydantic_ai":
        return "cerebras"
    if runner_key == "adk":
        return "google"
    return runner_key or "unknown"


# --------------------------------------------------------------------------- #
# Event + run-status writes
# --------------------------------------------------------------------------- #

async def write_event(
    ctx: AgnesContext,
    event_type: str,
    node_id: str | None,
    data: dict[str, Any],
) -> None:
    """Dual-write: pipeline_events row + event_bus fanout."""
    payload = json.dumps(_safe_for_json(data), separators=(",", ":"))
    elapsed = data.get("elapsed_ms") if isinstance(data, dict) else None

    async with write_tx(ctx.store.orchestration, ctx.store.orchestration_lock) as conn:
        await conn.execute(
            "INSERT INTO pipeline_events (run_id, event_type, node_id, data, elapsed_ms) "
            "VALUES (?, ?, ?, ?, ?)",
            (ctx.run_id, event_type, node_id, payload, elapsed),
        )

    await event_bus.publish_event(
        ctx.run_id,
        {
            "run_id": ctx.run_id,
            "event_type": event_type,
            "node_id": node_id,
            "data": _safe_for_json(data),
            "ts": datetime.now(timezone.utc).isoformat(),
        },
    )


async def _insert_run(
    store: "StoreHandles", *,
    pipeline_name: str,
    pipeline_version: int,
    trigger_source: str,
    trigger_payload: dict[str, Any],
    employee_id: int | None,
) -> int:
    payload = json.dumps(_safe_for_json(trigger_payload), separators=(",", ":"))
    employee_id_logical = str(employee_id) if employee_id is not None else None
    async with write_tx(store.orchestration, store.orchestration_lock) as conn:
        cur = await conn.execute(
            "INSERT INTO pipeline_runs ("
            "  pipeline_name, pipeline_version, trigger_source, trigger_payload,"
            "  employee_id_logical, status"
            ") VALUES (?, ?, ?, ?, ?, ?)",
            (pipeline_name, pipeline_version, trigger_source, payload,
             employee_id_logical, "running"),
        )
        run_id = cur.lastrowid
        if run_id is None:  # pragma: no cover
            raise RuntimeError("pipeline_runs insert returned no rowid")
        return run_id


async def _update_run_status(
    ctx: AgnesContext, *, status: str, error: str | None,
) -> None:
    async with write_tx(ctx.store.orchestration, ctx.store.orchestration_lock) as conn:
        await conn.execute(
            "UPDATE pipeline_runs SET status = ?, error = ?, completed_at = ? WHERE id = ?",
            (status, error, datetime.now(timezone.utc).isoformat(), ctx.run_id),
        )


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

def _canonical_node_input(node: PipelineNode, ctx: AgnesContext) -> dict[str, Any]:
    """Build the deterministic input dict for cache-key purposes."""
    return {
        "trigger_payload": ctx.trigger_payload,
        "deps": {d: ctx.node_outputs.get(d) for d in node.depends_on},
    }


def _safe_for_json(obj: Any) -> Any:
    """Recursively coerce values json.dumps can't natively encode.

    AgentResult, Pipeline, etc. show up here when a tool returns them.
    """
    if obj is None or isinstance(obj, (bool, int, str)):
        return obj
    if isinstance(obj, float):
        return obj
    if isinstance(obj, dict):
        return {str(k): _safe_for_json(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_safe_for_json(v) for v in obj]
    if hasattr(obj, "_asdict"):
        return _safe_for_json(obj._asdict())
    if hasattr(obj, "__dict__"):
        return _safe_for_json({k: v for k, v in vars(obj).items()
                               if not k.startswith("_")})
    return repr(obj)
