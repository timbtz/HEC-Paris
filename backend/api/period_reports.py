"""List, fetch, and approve `period_reports` rows.

Source: backend-gap-plan §2. Companion to `backend/api/reports.py`
(SQL-only computation) and `tools/report_renderer.py` (which writes the
`period_reports` rows that this module exposes).

Routes:

  GET  /period_reports                      — paginated list with filters
  GET  /period_reports/{id}                 — single row
  GET  /period_reports/{id}/artifact?format=md|pdf|csv
                                            — raw bytes of the rendered blob
  POST /period_reports/{id}/approve         — flip status to 'final',
                                              stamp approver_id + approved_at

`format=md` is shipped today (the renderer writes alongside the JSON
blob). `format=pdf|csv` returns 415 with a "coming soon" detail until
the renderer learns to emit those variants.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, Any, Literal

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import Response

from ..orchestration.store.writes import write_tx
from .runs import _row_to_dict, _rows_to_dicts


router = APIRouter(prefix="/period_reports")


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _parse_payload(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return None


def _project(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": int(row["id"]),
        "period_code": row["period_code"],
        "report_type": row["report_type"],
        "status": row["status"],
        "confidence": row["confidence"],
        "source_run_id": row["source_run_id"],
        "blob_path": row["blob_path"],
        "payload_json": _parse_payload(row.get("payload_json")),
        "created_at": row["created_at"],
        "approved_at": row["approved_at"],
        "approved_by": row["approved_by"],
    }


# --------------------------------------------------------------------------- #
# GET /period_reports
# --------------------------------------------------------------------------- #


@router.get("")
async def list_period_reports(
    request: Request,
    period_code: Annotated[str | None, Query()] = None,
    report_type: Annotated[str | None, Query(alias="type")] = None,
    status_filter: Annotated[str | None, Query(alias="status")] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> dict[str, Any]:
    store = request.app.state.store
    clauses: list[str] = []
    params: list[Any] = []
    if period_code is not None:
        clauses.append("period_code = ?")
        params.append(period_code)
    if report_type is not None:
        clauses.append("report_type = ?")
        params.append(report_type)
    if status_filter is not None:
        clauses.append("status = ?")
        params.append(status_filter)
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""

    cur = await store.accounting.execute(
        f"SELECT id, period_code, report_type, status, confidence, source_run_id, "
        f"       blob_path, payload_json, created_at, approved_at, approved_by "
        f"FROM period_reports {where} "
        f"ORDER BY id DESC LIMIT ? OFFSET ?",
        tuple(params) + (limit, offset),
    )
    rows = _rows_to_dicts(list(await cur.fetchall()))
    await cur.close()

    cur = await store.accounting.execute(
        f"SELECT COUNT(*) FROM period_reports {where}", tuple(params),
    )
    total_row = await cur.fetchone()
    await cur.close()
    total = int(total_row[0]) if total_row else 0

    return {
        "items": [_project(r) for r in rows],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


# --------------------------------------------------------------------------- #
# GET /period_reports/{id}
# --------------------------------------------------------------------------- #


@router.get("/{report_id}")
async def get_period_report(report_id: int, request: Request) -> dict[str, Any]:
    store = request.app.state.store
    cur = await store.accounting.execute(
        "SELECT id, period_code, report_type, status, confidence, source_run_id, "
        "       blob_path, payload_json, created_at, approved_at, approved_by "
        "FROM period_reports WHERE id = ?",
        (report_id,),
    )
    row = await cur.fetchone()
    await cur.close()
    if row is None:
        raise HTTPException(status_code=404, detail=f"period_report {report_id} not found")
    return _project(_row_to_dict(row) or {})


# --------------------------------------------------------------------------- #
# GET /period_reports/{id}/artifact?format=md|pdf|csv
# --------------------------------------------------------------------------- #


def _resolve_blob_root(data_dir: Path) -> Path:
    return (data_dir / "blobs").resolve()


def _safe_under(root: Path, candidate: Path) -> bool:
    try:
        candidate.resolve().relative_to(root)
    except ValueError:
        return False
    return True


@router.get("/{report_id}/artifact")
async def get_period_report_artifact(
    report_id: int,
    request: Request,
    format_: Annotated[Literal["md", "pdf", "csv"], Query(alias="format")] = "md",
) -> Response:
    store = request.app.state.store
    cur = await store.accounting.execute(
        "SELECT blob_path FROM period_reports WHERE id = ?", (report_id,),
    )
    row = await cur.fetchone()
    await cur.close()
    if row is None:
        raise HTTPException(status_code=404, detail=f"period_report {report_id} not found")

    blob_path_raw = row["blob_path"]
    if not blob_path_raw:
        raise HTTPException(status_code=404, detail="report has no blob_path")

    if format_ in ("pdf", "csv"):
        raise HTTPException(
            status_code=415,
            detail=f"format={format_} not yet supported — coming soon",
        )

    # Renderer writes blob_path = .../<report_type>.json next to a
    # .../<report_type>.md sibling. Swap the suffix to fetch the markdown.
    json_path = Path(blob_path_raw)
    md_path = json_path.with_suffix(".md")

    blob_root = _resolve_blob_root(store.data_dir)
    if not _safe_under(blob_root, md_path):
        raise HTTPException(status_code=403, detail="blob path escapes data dir")

    if not md_path.exists():
        raise HTTPException(
            status_code=404,
            detail=f"markdown artifact missing at {md_path}",
        )

    body = md_path.read_bytes()
    headers = {
        "Content-Disposition": f'inline; filename="{md_path.name}"',
        "X-Content-Type-Options": "nosniff",
    }
    return Response(content=body, media_type="text/markdown", headers=headers)


# --------------------------------------------------------------------------- #
# POST /period_reports/{id}/approve
# --------------------------------------------------------------------------- #


@router.post("/{report_id}/approve")
async def approve_period_report(
    report_id: int, request: Request,
) -> dict[str, Any]:
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
    cur = await store.accounting.execute(
        "SELECT id, status FROM period_reports WHERE id = ?", (report_id,),
    )
    row = await cur.fetchone()
    await cur.close()
    if row is None:
        raise HTTPException(status_code=404, detail=f"period_report {report_id} not found")

    approved_at = datetime.now(timezone.utc).isoformat()

    async with write_tx(store.accounting, store.accounting_lock) as conn:
        # Idempotent: only flips draft|flagged → final.
        await conn.execute(
            "UPDATE period_reports "
            "SET status = 'final', approved_at = ?, approved_by = ? "
            "WHERE id = ? AND status IN ('draft', 'flagged')",
            (approved_at, approver_id, report_id),
        )

    return {
        "id": report_id,
        "approved_at": approved_at,
        "approved_by": approver_id,
        "status": "final",
    }
