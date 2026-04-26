"""Bulk export of `agent_decisions` × `agent_costs` for an audit window.

Wires the AuditPack "Decision traces (full year)" download in the Lovable
ReportsPage. JSONL is the primary format — one `agent_decisions` row per
line, with the matching `agent_costs` payload inlined under `cost`.

Routes:
  GET /audit/decision_traces?from=YYYY-MM-DD&to=YYYY-MM-DD&format=jsonl|json

The endpoint caps the result count at 50 000 rows; exceeding the cap
returns 422 with a hint to narrow the date range. Filtering is on
`agent_decisions.started_at` (the `idx_decisions_run` index covers
run-id reads, but the `started_at` filter is small-table-friendly).
"""
from __future__ import annotations

import io
import json
import re
from typing import Annotated, Any, Literal

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import Response


router = APIRouter(prefix="/audit")

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_MAX_ROWS = 50_000


def _validate_date(value: str, field: str) -> None:
    if not _DATE_RE.match(value):
        raise HTTPException(
            status_code=422,
            detail=f"{field} must be ISO 8601 YYYY-MM-DD; got {value!r}",
        )


def _row_to_record(row: Any) -> dict[str, Any]:
    return {
        "decision_id": int(row["decision_id"]),
        "run_id_logical": int(row["run_id_logical"]) if row["run_id_logical"] is not None else None,
        "node_id": row["node_id"],
        "source": row["source"],
        "runner": row["runner"],
        "model": row["model"],
        "response_id": row["response_id"],
        "prompt_hash": row["prompt_hash"],
        "confidence": row["confidence"],
        "line_id_logical": row["line_id_logical"],
        "latency_ms": int(row["latency_ms"]) if row["latency_ms"] is not None else None,
        "finish_reason": row["finish_reason"],
        "wiki_page_id": int(row["wiki_page_id"]) if row["wiki_page_id"] is not None else None,
        "wiki_revision_id": int(row["wiki_revision_id"]) if row["wiki_revision_id"] is not None else None,
        "started_at": row["started_at"],
        "completed_at": row["completed_at"],
        "cost": {
            "provider": row["cost_provider"],
            "model": row["cost_model"],
            "employee_id": int(row["employee_id"]) if row["employee_id"] is not None else None,
            "input_tokens": int(row["input_tokens"]) if row["input_tokens"] is not None else 0,
            "output_tokens": int(row["output_tokens"]) if row["output_tokens"] is not None else 0,
            "cache_read_tokens": int(row["cache_read_tokens"]) if row["cache_read_tokens"] is not None else 0,
            "cache_write_tokens": int(row["cache_write_tokens"]) if row["cache_write_tokens"] is not None else 0,
            "reasoning_tokens": int(row["reasoning_tokens"]) if row["reasoning_tokens"] is not None else 0,
            "cost_micro_usd": int(row["cost_micro_usd"]) if row["cost_micro_usd"] is not None else None,
        } if row["cost_provider"] is not None else None,
    }


@router.get("/decision_traces")
async def decision_traces(
    request: Request,
    from_: Annotated[str, Query(alias="from")],
    to: Annotated[str, Query()],
    format_: Annotated[Literal["jsonl", "json"], Query(alias="format")] = "jsonl",
) -> Response:
    _validate_date(from_, "from")
    _validate_date(to, "to")
    if to < from_:
        raise HTTPException(
            status_code=422, detail="`to` must be on or after `from`",
        )

    store = request.app.state.store

    # Inclusive-end day filter: `started_at < <to+1day>` lets the index
    # take the BETWEEN equivalent without parsing the timestamp suffix.
    end_exclusive = f"{to}T99:99:99"  # lexical-max for 23:59:59 ISO suffixes
    cur = await store.audit.execute(
        "SELECT COUNT(*) FROM agent_decisions "
        "WHERE started_at >= ? AND started_at < ?",
        (from_, end_exclusive),
    )
    count_row = await cur.fetchone()
    await cur.close()
    total = int(count_row[0]) if count_row else 0
    if total > _MAX_ROWS:
        raise HTTPException(
            status_code=422,
            detail=(
                f"result set has {total} rows, exceeds cap of {_MAX_ROWS}; "
                "narrow the date range."
            ),
        )

    cur = await store.audit.execute(
        "SELECT d.id AS decision_id, d.run_id_logical, d.node_id, d.source, "
        "       d.runner, d.model, d.response_id, d.prompt_hash, d.confidence, "
        "       d.line_id_logical, d.latency_ms, d.finish_reason, "
        "       d.wiki_page_id, d.wiki_revision_id, d.started_at, d.completed_at, "
        "       c.provider AS cost_provider, c.model AS cost_model, c.employee_id, "
        "       c.input_tokens, c.output_tokens, c.cache_read_tokens, "
        "       c.cache_write_tokens, c.reasoning_tokens, c.cost_micro_usd "
        "FROM agent_decisions d "
        "LEFT JOIN agent_costs c ON c.decision_id = d.id "
        "WHERE d.started_at >= ? AND d.started_at < ? "
        "ORDER BY d.started_at ASC, d.id ASC",
        (from_, end_exclusive),
    )
    rows = list(await cur.fetchall())
    await cur.close()

    records = [_row_to_record(r) for r in rows]
    headers_filename_stub = f"decision_traces_{from_}_{to}"

    if format_ == "json":
        body = json.dumps(records, separators=(",", ":")).encode("utf-8")
        headers = {
            "Content-Disposition": f'attachment; filename="{headers_filename_stub}.json"',
            "X-Content-Type-Options": "nosniff",
        }
        return Response(content=body, media_type="application/json", headers=headers)

    buf = io.StringIO()
    for rec in records:
        buf.write(json.dumps(rec, separators=(",", ":")))
        buf.write("\n")
    body = buf.getvalue().encode("utf-8")
    headers = {
        "Content-Disposition": f'attachment; filename="{headers_filename_stub}.jsonl"',
        "X-Content-Type-Options": "nosniff",
    }
    return Response(content=body, media_type="application/x-ndjson", headers=headers)
