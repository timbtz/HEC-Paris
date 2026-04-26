"""Gamification endpoints — coins, leaderboard, rewards, redemptions.

Source: ported from TACL-GROUP/pulse-ai-grow `.lovable/plan.md`,
single-tenant flavour. Schema lives in `audit.db` (migration
audit/0005_gamification). Auto-credit hook lives in
`backend.orchestration.audit.propose_checkpoint_commit`.

Auth: same chokepoint as wiki — `x-agnes-author` header carries the
acting employee email; manager-only routes look up `employees.is_manager`.
There is no JWT/session — single-tenant + small team = the lightest
possible auth seam.

Routes:

  GET   /gamification/tasks
  POST  /gamification/tasks               (manager)
  PATCH /gamification/tasks/{id}          (manager)

  GET   /gamification/completions?status=&employee_id=&source=
  POST  /gamification/completions
  POST  /gamification/completions/{id}/approve   (manager)
  POST  /gamification/completions/{id}/reject    (manager)

  GET   /gamification/rewards
  POST  /gamification/rewards             (manager)

  POST  /gamification/redemptions
  POST  /gamification/redemptions/{id}/approve   (manager)
  POST  /gamification/redemptions/{id}/reject    (manager)

  POST  /gamification/coin_adjustments    (manager)

  GET   /gamification/leaderboard?period=month|week|all
  GET   /gamification/today/{employee_id}
  GET   /gamification/balance/{employee_id}
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from backend.orchestration.gamification import (
    AUTO_COIN_REWARD,
    DAILY_COIN_TARGET,
    coin_balance,
    employee_id_for_email,
    is_manager,
    leaderboard,
    today_summary,
)
from backend.orchestration.store.writes import write_tx


router = APIRouter(prefix="/gamification")


def _author_email(request: Request) -> str | None:
    """`x-agnes-author` header. Same convention as wiki write surface."""
    return request.headers.get("x-agnes-author")


async def _require_manager(request: Request) -> tuple[str, int]:
    """Resolve author → (email, employee_id). 403 if not a manager."""
    store = request.app.state.store
    email = _author_email(request)
    if not await is_manager(store.audit, email):
        raise HTTPException(status_code=403, detail="manager role required")
    eid = await employee_id_for_email(store.audit, email)
    if eid is None:
        raise HTTPException(status_code=403, detail="author email not in employees")
    assert email is not None
    return email, eid


async def _require_acting_employee(request: Request) -> tuple[str, int]:
    """Same as _require_manager but role check waived; just resolves the actor."""
    store = request.app.state.store
    email = _author_email(request)
    eid = await employee_id_for_email(store.audit, email)
    if eid is None:
        raise HTTPException(
            status_code=400,
            detail="x-agnes-author header missing or unknown",
        )
    assert email is not None
    return email, eid


# ──────────────────────────────────────────────────────────────────────────
# Tasks (catalogue of AI-adoption challenges)
# ──────────────────────────────────────────────────────────────────────────

class _TaskCreate(BaseModel):
    title: str = Field(..., min_length=1)
    description: str | None = None
    department: str = "All"
    coin_value: int = Field(..., ge=0)


class _TaskUpdate(BaseModel):
    title: str | None = None
    description: str | None = None
    department: str | None = None
    coin_value: int | None = Field(None, ge=0)
    is_active: bool | None = None


def _project_task(row: Any) -> dict[str, Any]:
    return {
        "id": int(row[0]),
        "title": row[1],
        "description": row[2],
        "department": row[3],
        "coin_value": int(row[4]),
        "is_active": bool(row[5]),
        "created_by_employee_id": row[6],
        "created_at": row[7],
        "updated_at": row[8],
    }


@router.get("/tasks")
async def list_tasks(
    request: Request,
    department: Annotated[str | None, Query()] = None,
    active: Annotated[bool | None, Query()] = None,
) -> dict[str, Any]:
    store = request.app.state.store
    where: list[str] = []
    params: list[Any] = []
    if department:
        where.append("department = ?")
        params.append(department)
    if active is not None:
        where.append("is_active = ?")
        params.append(1 if active else 0)
    sql = (
        "SELECT id, title, description, department, coin_value, is_active, "
        "       created_by_employee_id, created_at, updated_at "
        "FROM gamification_tasks "
        + ("WHERE " + " AND ".join(where) + " " if where else "")
        + "ORDER BY id"
    )
    cur = await store.audit.execute(sql, tuple(params))
    rows = await cur.fetchall()
    await cur.close()
    return {"items": [_project_task(r) for r in rows]}


@router.post("/tasks")
async def create_task(
    body: _TaskCreate, request: Request,
) -> dict[str, Any]:
    store = request.app.state.store
    _, manager_id = await _require_manager(request)
    async with write_tx(store.audit, store.audit_lock) as conn:
        cur = await conn.execute(
            "INSERT INTO gamification_tasks "
            "(title, description, department, coin_value, created_by_employee_id) "
            "VALUES (?, ?, ?, ?, ?)",
            (body.title, body.description, body.department, body.coin_value, manager_id),
        )
        task_id = cur.lastrowid
    return {"id": task_id}


@router.patch("/tasks/{task_id}")
async def update_task(
    task_id: int, body: _TaskUpdate, request: Request,
) -> dict[str, Any]:
    store = request.app.state.store
    await _require_manager(request)
    fields: list[str] = []
    params: list[Any] = []
    for col, val in (
        ("title", body.title),
        ("description", body.description),
        ("department", body.department),
        ("coin_value", body.coin_value),
    ):
        if val is not None:
            fields.append(f"{col} = ?")
            params.append(val)
    if body.is_active is not None:
        fields.append("is_active = ?")
        params.append(1 if body.is_active else 0)
    if not fields:
        raise HTTPException(status_code=400, detail="no fields to update")
    fields.append("updated_at = CURRENT_TIMESTAMP")
    params.append(task_id)
    async with write_tx(store.audit, store.audit_lock) as conn:
        cur = await conn.execute(
            f"UPDATE gamification_tasks SET {', '.join(fields)} WHERE id = ?",
            tuple(params),
        )
        if cur.rowcount == 0:
            raise HTTPException(status_code=404, detail=f"task {task_id} not found")
    return {"id": task_id, "updated": True}


# ──────────────────────────────────────────────────────────────────────────
# Completions (manual + auto)
# ──────────────────────────────────────────────────────────────────────────

class _CompletionCreate(BaseModel):
    task_id: int
    note: str | None = None


def _project_completion(row: Any) -> dict[str, Any]:
    return {
        "id": int(row[0]),
        "task_id": int(row[1]) if row[1] is not None else None,
        "task_title": row[2],
        "employee_id": int(row[3]),
        "employee_full_name": row[4],
        "note": row[5],
        "status": row[6],
        "coins_awarded": int(row[7]),
        "source": row[8],
        "agent_decision_id": int(row[9]) if row[9] is not None else None,
        "reviewed_by_employee_id": row[10],
        "reviewed_at": row[11],
        "created_at": row[12],
    }


@router.get("/completions")
async def list_completions(
    request: Request,
    status: Annotated[str | None, Query()] = None,
    employee_id: Annotated[int | None, Query()] = None,
    source: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(le=500)] = 100,
) -> dict[str, Any]:
    store = request.app.state.store
    where: list[str] = []
    params: list[Any] = []
    if status:
        where.append("tc.status = ?")
        params.append(status)
    if employee_id is not None:
        where.append("tc.employee_id = ?")
        params.append(employee_id)
    if source:
        where.append("tc.source = ?")
        params.append(source)
    sql = (
        "SELECT tc.id, tc.task_id, gt.title, tc.employee_id, e.full_name, "
        "       tc.note, tc.status, tc.coins_awarded, tc.source, "
        "       tc.agent_decision_id, tc.reviewed_by_employee_id, "
        "       tc.reviewed_at, tc.created_at "
        "FROM task_completions tc "
        "LEFT JOIN gamification_tasks gt ON gt.id = tc.task_id "
        "LEFT JOIN employees e ON e.id = tc.employee_id "
        + ("WHERE " + " AND ".join(where) + " " if where else "")
        + "ORDER BY tc.created_at DESC LIMIT ?"
    )
    params.append(limit)
    cur = await store.audit.execute(sql, tuple(params))
    rows = await cur.fetchall()
    await cur.close()
    return {"items": [_project_completion(r) for r in rows]}


@router.post("/completions")
async def submit_completion(
    body: _CompletionCreate, request: Request,
) -> dict[str, Any]:
    store = request.app.state.store
    _, employee_id = await _require_acting_employee(request)
    async with write_tx(store.audit, store.audit_lock) as conn:
        cur = await conn.execute(
            "SELECT id FROM gamification_tasks WHERE id = ? AND is_active = 1",
            (body.task_id,),
        )
        if (await cur.fetchone()) is None:
            await cur.close()
            raise HTTPException(status_code=404, detail="task not found or inactive")
        await cur.close()
        cur = await conn.execute(
            "INSERT INTO task_completions "
            "(task_id, employee_id, note, status, source) "
            "VALUES (?, ?, ?, 'pending', 'manual')",
            (body.task_id, employee_id, body.note),
        )
        completion_id = cur.lastrowid
    return {"id": completion_id, "status": "pending"}


@router.post("/completions/{completion_id}/approve")
async def approve_completion(
    completion_id: int, request: Request,
) -> dict[str, Any]:
    store = request.app.state.store
    _, manager_id = await _require_manager(request)
    async with write_tx(store.audit, store.audit_lock) as conn:
        cur = await conn.execute(
            "SELECT tc.status, tc.task_id, gt.coin_value "
            "FROM task_completions tc "
            "LEFT JOIN gamification_tasks gt ON gt.id = tc.task_id "
            "WHERE tc.id = ?",
            (completion_id,),
        )
        row = await cur.fetchone()
        await cur.close()
        if row is None:
            raise HTTPException(status_code=404, detail="completion not found")
        if row[0] != "pending":
            raise HTTPException(status_code=409, detail=f"already {row[0]}")
        coins = int(row[2]) if row[2] is not None else 0
        await conn.execute(
            "UPDATE task_completions SET status='approved', coins_awarded=?, "
            "reviewed_by_employee_id=?, reviewed_at=CURRENT_TIMESTAMP "
            "WHERE id = ?",
            (coins, manager_id, completion_id),
        )
    return {"id": completion_id, "status": "approved", "coins_awarded": coins}


@router.post("/completions/{completion_id}/reject")
async def reject_completion(
    completion_id: int, request: Request,
) -> dict[str, Any]:
    store = request.app.state.store
    _, manager_id = await _require_manager(request)
    async with write_tx(store.audit, store.audit_lock) as conn:
        cur = await conn.execute(
            "SELECT status FROM task_completions WHERE id = ?",
            (completion_id,),
        )
        row = await cur.fetchone()
        await cur.close()
        if row is None:
            raise HTTPException(status_code=404, detail="completion not found")
        if row[0] != "pending":
            raise HTTPException(status_code=409, detail=f"already {row[0]}")
        await conn.execute(
            "UPDATE task_completions SET status='rejected', "
            "reviewed_by_employee_id=?, reviewed_at=CURRENT_TIMESTAMP "
            "WHERE id = ?",
            (manager_id, completion_id),
        )
    return {"id": completion_id, "status": "rejected"}


# ──────────────────────────────────────────────────────────────────────────
# Rewards + redemptions
# ──────────────────────────────────────────────────────────────────────────

class _RewardCreate(BaseModel):
    name: str = Field(..., min_length=1)
    description: str | None = None
    emoji: str = "🎁"
    coin_cost: int = Field(..., gt=0)


class _RedemptionCreate(BaseModel):
    reward_id: int


def _project_reward(row: Any) -> dict[str, Any]:
    return {
        "id": int(row[0]),
        "name": row[1],
        "description": row[2],
        "emoji": row[3],
        "coin_cost": int(row[4]),
        "is_active": bool(row[5]),
    }


@router.get("/rewards")
async def list_rewards(request: Request) -> dict[str, Any]:
    store = request.app.state.store
    cur = await store.audit.execute(
        "SELECT id, name, description, emoji, coin_cost, is_active "
        "FROM rewards WHERE is_active = 1 ORDER BY coin_cost ASC"
    )
    rows = await cur.fetchall()
    await cur.close()
    return {"items": [_project_reward(r) for r in rows]}


@router.post("/rewards")
async def create_reward(
    body: _RewardCreate, request: Request,
) -> dict[str, Any]:
    store = request.app.state.store
    _, manager_id = await _require_manager(request)
    async with write_tx(store.audit, store.audit_lock) as conn:
        cur = await conn.execute(
            "INSERT INTO rewards (name, description, emoji, coin_cost, "
            "created_by_employee_id) VALUES (?, ?, ?, ?, ?)",
            (body.name, body.description, body.emoji, body.coin_cost, manager_id),
        )
        reward_id = cur.lastrowid
    return {"id": reward_id}


@router.post("/redemptions")
async def submit_redemption(
    body: _RedemptionCreate, request: Request,
) -> dict[str, Any]:
    store = request.app.state.store
    _, employee_id = await _require_acting_employee(request)
    async with write_tx(store.audit, store.audit_lock) as conn:
        cur = await conn.execute(
            "SELECT id, coin_cost FROM rewards WHERE id = ? AND is_active = 1",
            (body.reward_id,),
        )
        row = await cur.fetchone()
        await cur.close()
        if row is None:
            raise HTTPException(status_code=404, detail="reward not available")
        cost = int(row[1])
        balance = await coin_balance(conn, employee_id)
        if balance < cost:
            raise HTTPException(
                status_code=409,
                detail=f"insufficient coins (have {balance}, need {cost})",
            )
        cur = await conn.execute(
            "INSERT INTO reward_redemptions (reward_id, employee_id, coin_cost, status) "
            "VALUES (?, ?, ?, 'pending')",
            (body.reward_id, employee_id, cost),
        )
        rid = cur.lastrowid
    return {"id": rid, "status": "pending", "coin_cost": cost}


@router.post("/redemptions/{redemption_id}/approve")
async def approve_redemption(
    redemption_id: int, request: Request,
) -> dict[str, Any]:
    store = request.app.state.store
    _, manager_id = await _require_manager(request)
    async with write_tx(store.audit, store.audit_lock) as conn:
        cur = await conn.execute(
            "SELECT status FROM reward_redemptions WHERE id = ?",
            (redemption_id,),
        )
        row = await cur.fetchone()
        await cur.close()
        if row is None:
            raise HTTPException(status_code=404, detail="redemption not found")
        if row[0] != "pending":
            raise HTTPException(status_code=409, detail=f"already {row[0]}")
        await conn.execute(
            "UPDATE reward_redemptions SET status='approved', "
            "reviewed_by_employee_id=?, reviewed_at=CURRENT_TIMESTAMP WHERE id = ?",
            (manager_id, redemption_id),
        )
    return {"id": redemption_id, "status": "approved"}


@router.post("/redemptions/{redemption_id}/reject")
async def reject_redemption(
    redemption_id: int, request: Request,
) -> dict[str, Any]:
    """Reject (refund). Pending redemptions lock coins; rejecting unlocks them
    by setting status='rejected' so they're excluded from `coin_balance`'s
    `IN ('pending','approved')` filter."""
    store = request.app.state.store
    _, manager_id = await _require_manager(request)
    async with write_tx(store.audit, store.audit_lock) as conn:
        cur = await conn.execute(
            "SELECT status FROM reward_redemptions WHERE id = ?",
            (redemption_id,),
        )
        row = await cur.fetchone()
        await cur.close()
        if row is None:
            raise HTTPException(status_code=404, detail="redemption not found")
        if row[0] != "pending":
            raise HTTPException(status_code=409, detail=f"already {row[0]}")
        await conn.execute(
            "UPDATE reward_redemptions SET status='rejected', "
            "reviewed_by_employee_id=?, reviewed_at=CURRENT_TIMESTAMP WHERE id = ?",
            (manager_id, redemption_id),
        )
    return {"id": redemption_id, "status": "rejected"}


@router.get("/redemptions")
async def list_redemptions(
    request: Request,
    status: Annotated[str | None, Query()] = None,
    employee_id: Annotated[int | None, Query()] = None,
) -> dict[str, Any]:
    store = request.app.state.store
    where: list[str] = []
    params: list[Any] = []
    if status:
        where.append("rr.status = ?")
        params.append(status)
    if employee_id is not None:
        where.append("rr.employee_id = ?")
        params.append(employee_id)
    sql = (
        "SELECT rr.id, rr.reward_id, r.name, r.emoji, rr.employee_id, "
        "       e.full_name, rr.coin_cost, rr.status, rr.reviewed_at, rr.created_at "
        "FROM reward_redemptions rr "
        "LEFT JOIN rewards r ON r.id = rr.reward_id "
        "LEFT JOIN employees e ON e.id = rr.employee_id "
        + ("WHERE " + " AND ".join(where) + " " if where else "")
        + "ORDER BY rr.created_at DESC"
    )
    cur = await store.audit.execute(sql, tuple(params))
    rows = await cur.fetchall()
    await cur.close()
    return {
        "items": [
            {
                "id": int(r[0]), "reward_id": int(r[1]), "reward_name": r[2],
                "emoji": r[3], "employee_id": int(r[4]),
                "employee_full_name": r[5], "coin_cost": int(r[6]),
                "status": r[7], "reviewed_at": r[8], "created_at": r[9],
            }
            for r in rows
        ]
    }


# ──────────────────────────────────────────────────────────────────────────
# Coin adjustments (manager-only)
# ──────────────────────────────────────────────────────────────────────────

class _AdjustmentCreate(BaseModel):
    employee_id: int
    amount: int = Field(..., description="positive = credit, negative = debit; nonzero")
    reason: str | None = None


@router.get("/coin_adjustments")
async def list_coin_adjustments(
    request: Request,
    employee_id: Annotated[int | None, Query()] = None,
    limit: Annotated[int, Query(le=200)] = 10,
) -> dict[str, Any]:
    """Recent coin adjustments. Used by the manager AdjustCoinsDialog
    to show inline ± history (lifted from pulse-ai-grow Manage.tsx)."""
    store = request.app.state.store
    where: list[str] = []
    params: list[Any] = []
    if employee_id is not None:
        where.append("ca.employee_id = ?")
        params.append(employee_id)
    sql = (
        "SELECT ca.id, ca.employee_id, e.full_name, ca.adjusted_by_employee_id, "
        "       adj.full_name AS adjusted_by_name, ca.amount, ca.reason, ca.created_at "
        "FROM coin_adjustments ca "
        "LEFT JOIN employees e   ON e.id   = ca.employee_id "
        "LEFT JOIN employees adj ON adj.id = ca.adjusted_by_employee_id "
        + ("WHERE " + " AND ".join(where) + " " if where else "")
        + "ORDER BY ca.created_at DESC LIMIT ?"
    )
    params.append(limit)
    cur = await store.audit.execute(sql, tuple(params))
    rows = await cur.fetchall()
    await cur.close()
    return {
        "items": [
            {
                "id": int(r[0]),
                "employee_id": int(r[1]),
                "employee_full_name": r[2],
                "adjusted_by_employee_id": int(r[3]) if r[3] is not None else None,
                "adjusted_by_full_name": r[4],
                "amount": int(r[5]),
                "reason": r[6],
                "created_at": r[7],
            }
            for r in rows
        ]
    }


@router.post("/coin_adjustments")
async def adjust_coins(
    body: _AdjustmentCreate, request: Request,
) -> dict[str, Any]:
    if body.amount == 0:
        raise HTTPException(status_code=400, detail="amount cannot be zero")
    store = request.app.state.store
    _, manager_id = await _require_manager(request)
    async with write_tx(store.audit, store.audit_lock) as conn:
        cur = await conn.execute(
            "SELECT id FROM employees WHERE id = ?",
            (body.employee_id,),
        )
        if (await cur.fetchone()) is None:
            await cur.close()
            raise HTTPException(status_code=404, detail="employee not found")
        await cur.close()
        balance = await coin_balance(conn, body.employee_id)
        if balance + body.amount < 0:
            raise HTTPException(
                status_code=409,
                detail=f"would push balance below zero (have {balance}, "
                       f"adjusting by {body.amount})",
            )
        cur = await conn.execute(
            "INSERT INTO coin_adjustments (employee_id, adjusted_by_employee_id, "
            "amount, reason) VALUES (?, ?, ?, ?)",
            (body.employee_id, manager_id, body.amount, body.reason),
        )
        adj_id = cur.lastrowid
    return {"id": adj_id, "new_balance": balance + body.amount}


# ──────────────────────────────────────────────────────────────────────────
# Aggregates: leaderboard / today / balance
# ──────────────────────────────────────────────────────────────────────────

def _period_since(period: str) -> str | None:
    if period == "all":
        return None
    now = datetime.now(timezone.utc)
    if period == "month":
        return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0).isoformat()
    if period == "week":
        return (now - timedelta(days=now.weekday())).replace(
            hour=0, minute=0, second=0, microsecond=0,
        ).isoformat()
    if period == "today":
        return now.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
    raise HTTPException(status_code=400, detail=f"unknown period: {period}")


@router.get("/leaderboard")
async def get_leaderboard(
    request: Request,
    period: Annotated[str, Query()] = "month",
    limit: Annotated[int, Query(le=200)] = 50,
) -> dict[str, Any]:
    store = request.app.state.store
    since = _period_since(period)
    items = await leaderboard(store.audit, since=since, limit=limit)
    return {
        "period": period,
        "since": since,
        "items": items,
        "auto_coin_reward": AUTO_COIN_REWARD,
    }


@router.get("/today/{employee_id}")
async def get_today(employee_id: int, request: Request) -> dict[str, Any]:
    store = request.app.state.store
    cur = await store.audit.execute(
        "SELECT id FROM employees WHERE id = ?", (employee_id,),
    )
    if (await cur.fetchone()) is None:
        await cur.close()
        raise HTTPException(status_code=404, detail="employee not found")
    await cur.close()
    summary = await today_summary(store.audit, employee_id)
    summary["employee_id"] = employee_id
    return summary


@router.get("/balance/{employee_id}")
async def get_balance(employee_id: int, request: Request) -> dict[str, Any]:
    store = request.app.state.store
    cur = await store.audit.execute(
        "SELECT id, full_name, email, department FROM employees WHERE id = ?",
        (employee_id,),
    )
    row = await cur.fetchone()
    await cur.close()
    if row is None:
        raise HTTPException(status_code=404, detail="employee not found")
    bal = await coin_balance(store.audit, employee_id)
    return {
        "employee_id": int(row[0]),
        "full_name": row[1],
        "email": row[2],
        "department": row[3],
        "coins_balance": bal,
        "daily_target": DAILY_COIN_TARGET,
    }
