"""Internal API: pipeline trigger, run inspection, SSE, trace, review approve.

Source: RealMetaPRD §10.

Endpoints:

  POST /pipelines/run/{name}             — manual trigger
  GET  /pipelines                        — pipeline catalog (Phase 4 §1)
  GET  /pipelines/{name}                 — pipeline DAG topology (Phase 4 §1)
  GET  /runs                             — paginated run list (Phase 4 §1)
  GET  /runs/{run_id}                    — full run reconstruction
  GET  /runs/{run_id}/stream             — per-run SSE
  GET  /journal_entries/{id}/trace       — auditable drilldown
  POST /review/{entry_id}/approve        — approve a held entry

The SSE endpoint heartbeats every 15 s and breaks on the first
`pipeline_completed` / `pipeline_failed` event.
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, Any

import yaml
from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from ..orchestration import event_bus
from ..orchestration.executor import execute_pipeline
from ..orchestration.store.writes import write_tx
from ..orchestration.yaml_loader import load as load_pipeline


_PIPELINES_DIR = Path(__file__).resolve().parent.parent / "orchestration" / "pipelines"
_ROUTING_YAML = Path(__file__).resolve().parent.parent / "ingress" / "routing.yaml"


router = APIRouter()


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

def _row_to_dict(row: Any) -> dict[str, Any] | None:
    if row is None:
        return None
    return {k: row[k] for k in row.keys()}


def _rows_to_dicts(rows: list[Any]) -> list[dict[str, Any]]:
    return [{k: r[k] for k in r.keys()} for r in rows]


# --------------------------------------------------------------------------- #
# POST /pipelines/run/{name}
# --------------------------------------------------------------------------- #

@router.post("/pipelines/run/{name}")
async def trigger_pipeline(name: str, request: Request) -> dict[str, Any]:
    body: dict[str, Any] = {}
    try:
        raw = await request.body()
        if raw:
            body = json.loads(raw.decode("utf-8") or "{}")
            if not isinstance(body, dict):
                raise HTTPException(status_code=400, detail="body must be an object")
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail=f"invalid json: {exc}") from exc

    trigger_payload = body.get("trigger_payload") or {}
    if not isinstance(trigger_payload, dict):
        raise HTTPException(status_code=400, detail="trigger_payload must be an object")
    employee_id = body.get("employee_id")
    if employee_id is not None and not isinstance(employee_id, int):
        raise HTTPException(status_code=400, detail="employee_id must be int|null")

    store = request.app.state.store
    run_id = await execute_pipeline(
        name,
        trigger_source="manual",
        trigger_payload=trigger_payload,
        store=store,
        employee_id=employee_id,
    )
    return {"run_id": run_id, "stream_url": f"/runs/{run_id}/stream"}


# --------------------------------------------------------------------------- #
# GET /runs/{run_id}
# --------------------------------------------------------------------------- #

@router.get("/runs/{run_id}")
async def get_run(run_id: int, request: Request) -> dict[str, Any]:
    store = request.app.state.store

    cur = await store.orchestration.execute(
        "SELECT * FROM pipeline_runs WHERE id = ?", (run_id,)
    )
    run_row = await cur.fetchone()
    await cur.close()
    if run_row is None:
        raise HTTPException(status_code=404, detail=f"run {run_id} not found")

    cur = await store.orchestration.execute(
        "SELECT * FROM pipeline_events WHERE run_id = ? ORDER BY created_at, id",
        (run_id,),
    )
    event_rows = await cur.fetchall()
    await cur.close()

    cur = await store.audit.execute(
        "SELECT * FROM agent_decisions WHERE run_id_logical = ? ORDER BY id",
        (run_id,),
    )
    decision_rows = await cur.fetchall()
    await cur.close()

    return {
        "run": _row_to_dict(run_row),
        "events": _rows_to_dicts(list(event_rows)),
        "agent_decisions": _rows_to_dicts(list(decision_rows)),
    }


# --------------------------------------------------------------------------- #
# GET /runs/{run_id}/stream — per-run SSE
# --------------------------------------------------------------------------- #

_SSE_POLL_INTERVAL_S = 1.0
_SSE_HEARTBEAT_INTERVAL_S = 15.0


@router.get("/runs/{run_id}/stream")
async def stream_run(run_id: int, request: Request) -> StreamingResponse:
    async def event_stream():
        q = await event_bus.subscribe(run_id)
        try:
            yield ": heartbeat\n\n"
            since_heartbeat = 0.0
            while True:
                if await request.is_disconnected():
                    return
                try:
                    event = await asyncio.wait_for(q.get(), timeout=_SSE_POLL_INTERVAL_S)
                except asyncio.TimeoutError:
                    since_heartbeat += _SSE_POLL_INTERVAL_S
                    if since_heartbeat >= _SSE_HEARTBEAT_INTERVAL_S:
                        yield ": heartbeat\n\n"
                        since_heartbeat = 0.0
                    continue
                since_heartbeat = 0.0
                yield f"data: {json.dumps(event)}\n\n"
                if event.get("event_type") in ("pipeline_completed", "pipeline_failed"):
                    break
        finally:
            await event_bus.remove_subscriber(run_id, q)

    headers = {
        "Cache-Control": "no-cache",
        "X-Accel-Buffering": "no",
        "Connection": "keep-alive",
    }
    return StreamingResponse(
        event_stream(), media_type="text/event-stream", headers=headers
    )


# --------------------------------------------------------------------------- #
# GET /journal_entries/{id}/trace
# --------------------------------------------------------------------------- #

@router.get("/journal_entries/{entry_id}/trace")
async def get_entry_trace(entry_id: int, request: Request) -> dict[str, Any]:
    store = request.app.state.store

    cur = await store.accounting.execute(
        "SELECT * FROM journal_entries WHERE id = ?", (entry_id,)
    )
    entry_row = await cur.fetchone()
    await cur.close()
    if entry_row is None:
        raise HTTPException(status_code=404, detail=f"journal_entry {entry_id} not found")
    entry = _row_to_dict(entry_row) or {}

    cur = await store.accounting.execute(
        "SELECT * FROM journal_lines WHERE entry_id = ? ORDER BY id", (entry_id,)
    )
    line_rows = list(await cur.fetchall())
    await cur.close()
    lines = _rows_to_dicts(line_rows)

    line_ids = [int(r["id"]) for r in lines]
    traces: list[dict[str, Any]] = []
    if line_ids:
        placeholders = ",".join("?" for _ in line_ids)
        cur = await store.accounting.execute(
            f"SELECT * FROM decision_traces WHERE line_id IN ({placeholders}) ORDER BY id",
            tuple(line_ids),
        )
        traces = _rows_to_dicts(list(await cur.fetchall()))
        await cur.close()

    # Pull audit-side decisions + cost rows for any trace that points back.
    decision_ids: list[int] = []
    for t in traces:
        adi = t.get("agent_decision_id_logical")
        if adi is not None:
            try:
                decision_ids.append(int(adi))
            except (TypeError, ValueError):
                continue

    agent_decisions: list[dict[str, Any]] = []
    agent_costs: list[dict[str, Any]] = []
    if decision_ids:
        placeholders = ",".join("?" for _ in decision_ids)
        cur = await store.audit.execute(
            f"SELECT * FROM agent_decisions WHERE id IN ({placeholders}) ORDER BY id",
            tuple(decision_ids),
        )
        agent_decisions = _rows_to_dicts(list(await cur.fetchall()))
        await cur.close()

        cur = await store.audit.execute(
            f"SELECT * FROM agent_costs WHERE decision_id IN ({placeholders}) ORDER BY decision_id",
            tuple(decision_ids),
        )
        agent_costs = _rows_to_dicts(list(await cur.fetchall()))
        await cur.close()

    # source_run_id → trigger_payload for human context.
    source_run_id = entry.get("source_run_id")
    source_run: dict[str, Any] | None = None
    if source_run_id is not None:
        cur = await store.orchestration.execute(
            "SELECT * FROM pipeline_runs WHERE id = ?", (source_run_id,)
        )
        run_row = await cur.fetchone()
        await cur.close()
        source_run = _row_to_dict(run_row)

    # Cross-DB joins via in-Python merge — no ATTACH (RealMetaPRD §6.5).
    swan_tx_ids = {r["swan_transaction_id"] for r in lines if r.get("swan_transaction_id")}
    swan_transactions: list[dict[str, Any]] = []
    if swan_tx_ids:
        placeholders = ",".join("?" for _ in swan_tx_ids)
        cur = await store.accounting.execute(
            f"SELECT * FROM swan_transactions WHERE id IN ({placeholders})",
            tuple(swan_tx_ids),
        )
        swan_transactions = _rows_to_dicts(list(await cur.fetchall()))
        await cur.close()

    document_ids = {r["document_id"] for r in lines if r.get("document_id") is not None}
    documents: list[dict[str, Any]] = []
    if document_ids:
        placeholders = ",".join("?" for _ in document_ids)
        cur = await store.accounting.execute(
            f"SELECT * FROM documents WHERE id IN ({placeholders})",
            tuple(document_ids),
        )
        documents = _rows_to_dicts(list(await cur.fetchall()))
        await cur.close()

    return {
        "entry": entry,
        "lines": lines,
        "traces": traces,
        "agent_decisions": agent_decisions,
        "agent_costs": agent_costs,
        "source_run": source_run,
        "swan_transactions": swan_transactions,
        "documents": documents,
    }


# --------------------------------------------------------------------------- #
# POST /review/{entry_id}/approve
# --------------------------------------------------------------------------- #

@router.post("/review/{entry_id}/approve")
async def approve_entry(entry_id: int, request: Request) -> dict[str, Any]:
    body: dict[str, Any] = {}
    try:
        raw = await request.body()
        if raw:
            body = json.loads(raw.decode("utf-8") or "{}")
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail=f"invalid json: {exc}") from exc

    approver_id = body.get("approver_id")
    if not isinstance(approver_id, int):
        raise HTTPException(status_code=400, detail="approver_id (int) required")

    store = request.app.state.store

    # Verify the entry exists up front so we can 404 cleanly.
    cur = await store.accounting.execute(
        "SELECT id, status FROM journal_entries WHERE id = ?", (entry_id,)
    )
    row = await cur.fetchone()
    await cur.close()
    if row is None:
        raise HTTPException(status_code=404, detail=f"journal_entry {entry_id} not found")

    approved_at = datetime.now(timezone.utc).isoformat()

    async with write_tx(store.accounting, store.accounting_lock) as conn:
        await conn.execute(
            "UPDATE decision_traces "
            "SET approver_id = ?, approved_at = ? "
            "WHERE line_id IN (SELECT id FROM journal_lines WHERE entry_id = ?)",
            (approver_id, approved_at, entry_id),
        )
        # Idempotent: only flips review→posted; second approve is a no-op.
        await conn.execute(
            "UPDATE journal_entries SET status = 'posted' "
            "WHERE id = ? AND status = 'review'",
            (entry_id,),
        )

    # Notify dashboard subscribers post-commit (skip envelope mapping for MVP).
    await event_bus.publish_event_dashboard(
        {
            "event_type": "ledger.entry_posted",
            "entry_id": entry_id,
            "approver_id": approver_id,
            "approved_at": approved_at,
        }
    )

    return {"entry_id": entry_id, "approver_id": approver_id, "status": "approved"}


# --------------------------------------------------------------------------- #
# GET /journal_entries  — paginated ledger list, newest first
# --------------------------------------------------------------------------- #

@router.get("/journal_entries")
async def list_journal_entries(
    request: Request,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    status_filter: Annotated[str | None, Query(alias="status")] = None,
) -> dict[str, Any]:
    store = request.app.state.store
    where = "WHERE je.status = ?" if status_filter else ""
    params: tuple[Any, ...] = (status_filter,) if status_filter else ()
    cur = await store.accounting.execute(
        f"SELECT je.id, je.basis, je.entry_date, je.description, je.status, "
        f"       je.source_pipeline, je.source_run_id, je.accrual_link_id, je.reversal_of_id, je.created_at, "
        f"       COALESCE(SUM(jl.debit_cents), 0) AS total_cents, "
        f"       COUNT(jl.id) AS line_count "
        f"FROM journal_entries je "
        f"LEFT JOIN journal_lines jl ON jl.entry_id = je.id "
        f"{where} "
        f"GROUP BY je.id "
        f"ORDER BY je.id DESC "
        f"LIMIT ? OFFSET ?",
        params + (limit, offset),
    )
    rows = await cur.fetchall()
    await cur.close()
    cur = await store.accounting.execute(
        f"SELECT COUNT(*) FROM journal_entries je {where}", params,
    )
    total_row = await cur.fetchone()
    await cur.close()
    total = int(total_row[0]) if total_row else 0
    return {
        "items": _rows_to_dicts(rows),
        "total": total,
        "limit": limit,
        "offset": offset,
    }


# --------------------------------------------------------------------------- #
# GET /runs — paginated run list with cost + review aggregates
# --------------------------------------------------------------------------- #


def _ms_between(started: str | None, completed: str | None) -> int | None:
    """Compute elapsed_ms between two ISO timestamps. Tolerant of ' '/'T'."""
    if not started or not completed:
        return None
    try:
        s = datetime.fromisoformat(started.replace("Z", "+00:00"))
        c = datetime.fromisoformat(completed.replace("Z", "+00:00"))
    except ValueError:
        return None
    return int((c - s).total_seconds() * 1000)


@router.get("/runs")
async def list_runs(
    request: Request,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    pipeline_name: Annotated[str | None, Query()] = None,
    status_filter: Annotated[str | None, Query(alias="status")] = None,
    from_: Annotated[str | None, Query(alias="from")] = None,
    to: Annotated[str | None, Query()] = None,
) -> dict[str, Any]:
    """List recent pipeline runs with cost + review-count aggregates.

    Cross-DB joins are done in Python (RealMetaPRD §6.5 — no ATTACH):
      1. Page over `pipeline_runs` first.
      2. Sum `audit.agent_costs.cost_micro_usd` per run via
         `agent_decisions.run_id_logical`.
      3. Count open `accounting.review_queue` rows whose `entry_id`
         points at a journal entry with `source_run_id` in this page.
    """
    store = request.app.state.store
    clauses: list[str] = []
    params: list[Any] = []
    if pipeline_name is not None:
        clauses.append("pipeline_name = ?")
        params.append(pipeline_name)
    if status_filter is not None:
        clauses.append("status = ?")
        params.append(status_filter)
    if from_ is not None:
        clauses.append("started_at >= ?")
        params.append(from_)
    if to is not None:
        clauses.append("started_at <= ?")
        params.append(to)
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""

    cur = await store.orchestration.execute(
        f"SELECT id, pipeline_name, pipeline_version, trigger_source, "
        f"       employee_id_logical, status, error, started_at, completed_at "
        f"FROM pipeline_runs {where} "
        f"ORDER BY id DESC LIMIT ? OFFSET ?",
        tuple(params) + (limit, offset),
    )
    run_rows = list(await cur.fetchall())
    await cur.close()

    cur = await store.orchestration.execute(
        f"SELECT COUNT(*) FROM pipeline_runs {where}", tuple(params),
    )
    total_row = await cur.fetchone()
    await cur.close()
    total = int(total_row[0]) if total_row else 0

    run_ids = [int(r["id"]) for r in run_rows]
    cost_by_run: dict[int, int] = {}
    review_by_run: dict[int, int] = {}
    if run_ids:
        placeholders = ",".join("?" for _ in run_ids)
        cur = await store.audit.execute(
            f"SELECT ad.run_id_logical AS run_id, "
            f"       COALESCE(SUM(ac.cost_micro_usd), 0) AS total "
            f"FROM agent_decisions ad "
            f"LEFT JOIN agent_costs ac ON ac.decision_id = ad.id "
            f"WHERE ad.run_id_logical IN ({placeholders}) "
            f"GROUP BY ad.run_id_logical",
            tuple(run_ids),
        )
        for row in await cur.fetchall():
            cost_by_run[int(row["run_id"])] = int(row["total"] or 0)
        await cur.close()

        cur = await store.accounting.execute(
            f"SELECT je.source_run_id AS run_id, COUNT(rq.id) AS n "
            f"FROM journal_entries je "
            f"JOIN review_queue rq ON rq.entry_id = je.id "
            f"WHERE je.source_run_id IN ({placeholders}) "
            f"  AND rq.resolved_at IS NULL "
            f"GROUP BY je.source_run_id",
            tuple(run_ids),
        )
        for row in await cur.fetchall():
            review_by_run[int(row["run_id"])] = int(row["n"] or 0)
        await cur.close()

    items: list[dict[str, Any]] = []
    for r in run_rows:
        rid = int(r["id"])
        emp_logical = r["employee_id_logical"]
        try:
            emp_id: int | None = int(emp_logical) if emp_logical is not None else None
        except (TypeError, ValueError):
            emp_id = None
        items.append({
            "id": rid,
            "pipeline_name": r["pipeline_name"],
            "pipeline_version": int(r["pipeline_version"]),
            "trigger_source": r["trigger_source"],
            "employee_id_logical": emp_id,
            "status": r["status"],
            "error": r["error"],
            "started_at": r["started_at"],
            "completed_at": r["completed_at"],
            "elapsed_ms": _ms_between(r["started_at"], r["completed_at"]),
            "total_cost_micro_usd": cost_by_run.get(rid, 0),
            "review_count": review_by_run.get(rid, 0),
        })

    return {"items": items, "total": total, "limit": limit, "offset": offset}


# --------------------------------------------------------------------------- #
# GET /pipelines  — catalog of YAML-defined pipelines
# GET /pipelines/{name}  — DAG topology
# --------------------------------------------------------------------------- #


def _routing_event_pipelines() -> set[str]:
    """Return the set of pipeline names referenced from `routing.yaml`."""
    if not _ROUTING_YAML.exists():
        return set()
    try:
        raw = yaml.safe_load(_ROUTING_YAML.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError:
        return set()
    names: set[str] = set()
    for entries in (raw.get("routes") or {}).values():
        for n in entries or []:
            if isinstance(n, str):
                names.add(n)
    for entries in (raw.get("defaults") or {}).values():
        if isinstance(entries, list):
            for n in entries:
                if isinstance(n, str):
                    names.add(n)
        elif isinstance(entries, str):
            names.add(entries)
    return names


def _trigger_string(trigger: dict[str, Any]) -> str | None:
    """Project the YAML `trigger:` mapping into a single string."""
    if not isinstance(trigger, dict):
        return None
    src = trigger.get("source")
    if isinstance(src, str):
        return src
    return None


@router.get("/pipelines")
async def list_pipelines(request: Request) -> dict[str, Any]:
    """Catalog of all pipelines on disk.

    The `kind` field reflects whether the pipeline is referenced from
    `ingress/routing.yaml` (`event`) or only manually-triggerable
    (`manual`). `node_count` is the static node count from the YAML.
    """
    if not _PIPELINES_DIR.exists():
        return {"items": []}
    event_names = _routing_event_pipelines()
    items: list[dict[str, Any]] = []
    for path in sorted(_PIPELINES_DIR.glob("*.yaml")):
        try:
            pipeline = load_pipeline(path)
        except Exception:  # noqa: BLE001 — bad YAML shouldn't sink the catalog
            continue
        items.append({
            "name": pipeline.name,
            "version": pipeline.version,
            "kind": "event" if pipeline.name in event_names else "manual",
            "trigger": _trigger_string(pipeline.trigger),
            "node_count": len(pipeline.nodes),
        })
    return {"items": items}


@router.get("/pipelines/{name}")
async def get_pipeline(name: str, request: Request) -> dict[str, Any]:
    path = _PIPELINES_DIR / f"{name}.yaml"
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"pipeline '{name}' not found")
    try:
        pipeline = load_pipeline(path)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"invalid pipeline yaml: {exc}") from exc
    event_names = _routing_event_pipelines()
    nodes = [
        {
            "id": n.id,
            "kind": "tool" if n.is_tool else "agent",
            "ref": n.tool if n.is_tool else n.agent,
            "runner": n.runner,
            "depends_on": list(n.depends_on),
            "when": n.when,
            "cacheable": n.cacheable,
        }
        for n in pipeline.nodes
    ]
    return {
        "name": pipeline.name,
        "version": pipeline.version,
        "kind": "event" if pipeline.name in event_names else "manual",
        "trigger": _trigger_string(pipeline.trigger),
        "nodes": nodes,
    }


# --------------------------------------------------------------------------- #
# GET /envelopes  — current envelope state for the dashboard rings
# --------------------------------------------------------------------------- #

@router.get("/envelopes")
async def list_envelopes(
    request: Request,
    employee_id: Annotated[int | None, Query()] = None,
    period: Annotated[str | None, Query(pattern=r"^\d{4}-\d{2}$")] = None,
    scope_kind: Annotated[str | None, Query()] = None,
) -> dict[str, Any]:
    store = request.app.state.store
    clauses: list[str] = []
    params: list[Any] = []
    if employee_id is not None:
        clauses.append("(be.scope_kind = 'employee' AND be.scope_id = ?)")
        params.append(employee_id)
    elif scope_kind == "company":
        clauses.append("be.scope_kind = 'company'")
    if period is not None:
        clauses.append("be.period = ?")
        params.append(period)
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    cur = await store.accounting.execute(
        f"SELECT be.id, be.scope_kind, be.scope_id, be.category, be.period, "
        f"       be.cap_cents, be.soft_threshold_pct, "
        f"       COALESCE(SUM(ba.amount_cents), 0) AS used_cents, "
        f"       COUNT(ba.id) AS allocation_count "
        f"FROM budget_envelopes be "
        f"LEFT JOIN budget_allocations ba ON ba.envelope_id = be.id "
        f"{where} "
        f"GROUP BY be.id "
        f"ORDER BY be.scope_kind, be.scope_id, be.category",
        tuple(params),
    )
    rows = await cur.fetchall()
    await cur.close()
    return {"items": _rows_to_dicts(rows)}
