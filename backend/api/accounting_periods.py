"""Read-only listing of `accounting_periods` rows.

Source: backend-gap-plan §11 follow-up. Powers the frontend's period
picker so closing-pipeline triggers can pass an explicit `period_code`
and the dashboard can render `2026-Q1 closed | 2026-Q2 closing |
2026-Q3 open` chips.

Routes:

  GET /accounting_periods   — list, ordered by start_date DESC
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request


router = APIRouter()


@router.get("/accounting_periods")
async def list_accounting_periods(request: Request) -> list[dict[str, Any]]:
    store = request.app.state.store
    cur = await store.accounting.execute(
        "SELECT id, code, start_date, end_date, status, closed_at, closed_by "
        "FROM accounting_periods "
        "ORDER BY start_date DESC"
    )
    rows = await cur.fetchall()
    await cur.close()
    return [
        {
            "id": int(r["id"]),
            "code": r["code"],
            "start_date": r["start_date"],
            "end_date": r["end_date"],
            "status": r["status"],
            "closed_at": r["closed_at"],
            "closed_by": int(r["closed_by"]) if r["closed_by"] is not None else None,
        }
        for r in rows
    ]
