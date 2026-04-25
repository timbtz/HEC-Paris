"""Internal API: pipeline trigger, run inspection, SSE, trace, review approve.

Source: RealMetaPRD §10.

Five endpoints:

  POST /pipelines/run/{name}             — manual trigger
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
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from ..orchestration import event_bus
from ..orchestration.executor import execute_pipeline
from ..orchestration.store.writes import write_tx


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

@router.get("/runs/{run_id}/stream")
async def stream_run(run_id: int) -> StreamingResponse:
    async def event_stream():
        q = await event_bus.subscribe(run_id)
        try:
            yield ": heartbeat\n\n"
            while True:
                try:
                    event = await asyncio.wait_for(q.get(), timeout=15.0)
                except asyncio.TimeoutError:
                    yield ": heartbeat\n\n"
                    continue
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
