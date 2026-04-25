"""Employee directory endpoints.

Source: backend-gap-plan §3. Reads `audit.employees` (seeded by migration
audit/0002_seed_employees.py) so the frontend can render employee chips
on ledger rows, populate the Budgets matrix, and pivot AI spend.

Routes:

  GET /employees?active=true
  GET /employees/{id}    — single row + envelope summary + 30-day spend

The detail variant cross-DB joins (no ATTACH; RealMetaPRD §6.5):
  - `accounting.budget_envelopes` for current-period envelope summary
  - `audit.agent_costs` for the rolling-30-day cost aggregate
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query, Request

from .runs import _row_to_dict, _rows_to_dicts


router = APIRouter(prefix="/employees")


def _project(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": int(row["id"]),
        "email": row["email"],
        "full_name": row["full_name"],
        "swan_iban": row["swan_iban"],
        "swan_account_id": row["swan_account_id"],
        "manager_employee_id": row["manager_employee_id"],
        "department": row["department"],
        "active": bool(row["active"]),
        "created_at": row["created_at"],
    }


@router.get("")
async def list_employees(
    request: Request,
    active: Annotated[bool | None, Query()] = None,
) -> dict[str, Any]:
    store = request.app.state.store
    where = ""
    params: tuple[Any, ...] = ()
    if active is not None:
        where = "WHERE active = ?"
        params = (1 if active else 0,)
    cur = await store.audit.execute(
        f"SELECT id, email, full_name, swan_iban, swan_account_id, "
        f"       manager_employee_id, department, active, created_at "
        f"FROM employees {where} "
        f"ORDER BY id",
        params,
    )
    rows = _rows_to_dicts(list(await cur.fetchall()))
    await cur.close()
    return {"items": [_project(r) for r in rows]}


@router.get("/{employee_id}")
async def get_employee(employee_id: int, request: Request) -> dict[str, Any]:
    store = request.app.state.store
    cur = await store.audit.execute(
        "SELECT id, email, full_name, swan_iban, swan_account_id, "
        "       manager_employee_id, department, active, created_at "
        "FROM employees WHERE id = ?",
        (employee_id,),
    )
    row = await cur.fetchone()
    await cur.close()
    if row is None:
        raise HTTPException(status_code=404, detail=f"employee {employee_id} not found")
    out = _project(_row_to_dict(row) or {})

    # Current-month envelope summary.
    period = datetime.now(timezone.utc).strftime("%Y-%m")
    cur = await store.accounting.execute(
        "SELECT be.id, be.category, be.period, be.cap_cents, be.soft_threshold_pct, "
        "       COALESCE(SUM(ba.amount_cents), 0) AS used_cents, "
        "       COUNT(ba.id) AS allocation_count "
        "FROM budget_envelopes be "
        "LEFT JOIN budget_allocations ba ON ba.envelope_id = be.id "
        "WHERE be.scope_kind = 'employee' AND be.scope_id = ? AND be.period = ? "
        "GROUP BY be.id "
        "ORDER BY be.category",
        (employee_id, period),
    )
    envelope_rows = _rows_to_dicts(list(await cur.fetchall()))
    await cur.close()

    # Rolling 30-day cost aggregate.
    since = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    cur = await store.audit.execute(
        "SELECT COALESCE(SUM(cost_micro_usd), 0) AS total, "
        "       COUNT(*) AS calls "
        "FROM agent_costs "
        "WHERE employee_id = ? AND created_at >= ?",
        (employee_id, since),
    )
    cost_row = await cur.fetchone()
    await cur.close()

    out["envelopes_current_period"] = {
        "period": period,
        "items": envelope_rows,
    }
    out["spend_30d"] = {
        "since": since,
        "cost_micro_usd": int(cost_row["total"]) if cost_row else 0,
        "calls": int(cost_row["calls"]) if cost_row else 0,
    }
    return out
