"""Gamification: coins, leaderboard, rewards.

Source: ported from TACL-GROUP/pulse-ai-grow (DailyAI). Single-tenant
flavour — see migration audit/0005_gamification.

The auto-credit hook (`auto_credit_for_decision`) is the wedge that
makes this version different from the original: every real
`agent_decisions` row inserted by `audit.write_decision` produces an
approved `task_completion` row with `source='auto'` and a fixed
`AUTO_COIN_REWARD` payout. The leaderboard therefore reflects actual
API usage, not self-declared clicks. Manual completions still work in
parallel (`source='manual'`, manager-approved) — those cover AI use
that bypasses the Agnes backend (Claude desktop, ChatGPT browser, …).

All read helpers compute `coins_balance` on the fly:

    SUM(approved manual + auto completions)
  + SUM(coin_adjustments)
  − SUM(approved redemptions)
  − SUM(pending redemptions)   -- coins are *locked* on submit, refunded on reject

Storing the balance as a column would drift; computing it is two
indexed scans and stays correct under partial failures.
"""
from __future__ import annotations

from typing import Any

import aiosqlite


# Per-call payout for the auto-credit hook. Flat reward (not scaled by
# cost) so a single Sonnet thinking call doesn't dominate the leaderboard.
AUTO_COIN_REWARD = 5

# Daily target shown on the Today widget — same number their plan picked.
DAILY_COIN_TARGET = 100


# ──────────────────────────────────────────────────────────────────────────
# Auto-credit hook (called from inside audit.write_decision's write_tx)
# ──────────────────────────────────────────────────────────────────────────

async def auto_credit_for_decision(
    conn: aiosqlite.Connection,
    *,
    employee_id: int | None,
    agent_decision_id: int,
    runner: str,
) -> int | None:
    """Insert a `task_completions` row tagged to one `agent_decisions`.

    Returns the new completion id, or None if `employee_id` was missing
    (system-attributed calls don't credit anyone). Idempotent on
    `agent_decision_id` — replaying a decision row never double-credits.

    MUST be called from inside the same `write_tx` block as the
    decision insert. Don't open a nested transaction here.
    """
    if employee_id is None:
        return None
    cur = await conn.execute(
        "SELECT id FROM task_completions WHERE agent_decision_id = ?",
        (agent_decision_id,),
    )
    existing = await cur.fetchone()
    await cur.close()
    if existing is not None:
        return int(existing[0])
    cur = await conn.execute(
        "INSERT INTO task_completions ("
        "  task_id, employee_id, status, coins_awarded, source, "
        "  agent_decision_id, reviewed_at, note"
        ") VALUES (NULL, ?, 'approved', ?, 'auto', ?, CURRENT_TIMESTAMP, ?)",
        (employee_id, AUTO_COIN_REWARD, agent_decision_id, f"runner={runner}"),
    )
    rid = cur.lastrowid
    return int(rid) if rid is not None else None


# ──────────────────────────────────────────────────────────────────────────
# Balance + leaderboard reads
# ──────────────────────────────────────────────────────────────────────────

async def coin_balance(
    conn: aiosqlite.Connection, employee_id: int
) -> int:
    """Computed coin balance for one employee.

    earned        = SUM(coins_awarded) WHERE status='approved'
    adjustments   = SUM(amount) FROM coin_adjustments
    spent_locked  = SUM(coin_cost) FROM reward_redemptions
                    WHERE status IN ('pending','approved')   -- pending locks
    """
    cur = await conn.execute(
        "SELECT COALESCE(SUM(coins_awarded), 0) FROM task_completions "
        "WHERE employee_id = ? AND status = 'approved'",
        (employee_id,),
    )
    earned = int((await cur.fetchone())[0])
    await cur.close()

    cur = await conn.execute(
        "SELECT COALESCE(SUM(amount), 0) FROM coin_adjustments "
        "WHERE employee_id = ?",
        (employee_id,),
    )
    adj = int((await cur.fetchone())[0])
    await cur.close()

    cur = await conn.execute(
        "SELECT COALESCE(SUM(coin_cost), 0) FROM reward_redemptions "
        "WHERE employee_id = ? AND status IN ('pending','approved')",
        (employee_id,),
    )
    spent = int((await cur.fetchone())[0])
    await cur.close()

    return earned + adj - spent


async def is_manager(conn: aiosqlite.Connection, email: str | None) -> bool:
    """`x-agnes-author` → manager check. Empty/unknown emails are not managers."""
    if not email:
        return False
    cur = await conn.execute(
        "SELECT is_manager FROM employees WHERE email = ?",
        (email,),
    )
    row = await cur.fetchone()
    await cur.close()
    return bool(row[0]) if row else False


async def employee_id_for_email(
    conn: aiosqlite.Connection, email: str | None
) -> int | None:
    if not email:
        return None
    cur = await conn.execute(
        "SELECT id FROM employees WHERE email = ?",
        (email,),
    )
    row = await cur.fetchone()
    await cur.close()
    return int(row[0]) if row else None


async def leaderboard(
    conn: aiosqlite.Connection,
    *,
    since: str | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """Rank employees by approved coins earned (auto + manual) since `since`.

    `since` is an ISO timestamp string; pass None for all-time. Includes
    coin-adjustment delta and call count. Ordered by coins descending,
    then call-count descending.
    """
    where_completion = ""
    where_adj = ""
    completion_params: list[Any] = []
    adj_params: list[Any] = []
    if since is not None:
        where_completion = "AND tc.created_at >= ?"
        where_adj = "AND ca.created_at >= ?"
        completion_params.append(since)
        adj_params.append(since)

    sql = (
        "SELECT e.id, e.email, e.full_name, e.department, "
        "       COALESCE(SUM(CASE WHEN tc.status = 'approved' "
        "                         THEN tc.coins_awarded ELSE 0 END), 0) "
        "         AS earned, "
        "       COALESCE(SUM(CASE WHEN tc.status = 'approved' "
        "                              AND tc.source = 'auto' "
        "                         THEN tc.coins_awarded ELSE 0 END), 0) "
        "         AS earned_auto, "
        "       COUNT(CASE WHEN tc.status = 'approved' THEN 1 END) AS call_count "
        "FROM employees e "
        f"LEFT JOIN task_completions tc ON tc.employee_id = e.id {where_completion} "
        "WHERE e.active = 1 "
        "GROUP BY e.id "
        "ORDER BY earned DESC, call_count DESC "
        "LIMIT ?"
    )
    completion_params.append(limit)
    cur = await conn.execute(sql, tuple(completion_params))
    rows = await cur.fetchall()
    await cur.close()

    sql_adj = (
        "SELECT employee_id, COALESCE(SUM(amount), 0) AS adj "
        f"FROM coin_adjustments ca WHERE 1=1 {where_adj} "
        "GROUP BY employee_id"
    )
    cur = await conn.execute(sql_adj, tuple(adj_params))
    adj_rows = await cur.fetchall()
    await cur.close()
    adj_by_employee: dict[int, int] = {int(r[0]): int(r[1]) for r in adj_rows}

    out: list[dict[str, Any]] = []
    for r in rows:
        eid = int(r[0])
        adj = adj_by_employee.get(eid, 0)
        out.append({
            "employee_id": eid,
            "email": r[1],
            "full_name": r[2],
            "department": r[3],
            "earned": int(r[4]),
            "earned_auto": int(r[5]),
            "earned_manual": int(r[4]) - int(r[5]),
            "adjustments": adj,
            "coins": int(r[4]) + adj,
            "call_count": int(r[6]),
        })
    out.sort(key=lambda d: (d["coins"], d["call_count"]), reverse=True)
    return out


async def today_summary(
    conn: aiosqlite.Connection,
    employee_id: int,
) -> dict[str, Any]:
    """Coins-today + 14-day streak for the Today widget."""
    cur = await conn.execute(
        "SELECT COALESCE(SUM(coins_awarded), 0), COUNT(*) "
        "FROM task_completions "
        "WHERE employee_id = ? AND status = 'approved' "
        "  AND DATE(created_at) = DATE('now')",
        (employee_id,),
    )
    row = await cur.fetchone()
    await cur.close()
    coins_today = int(row[0]) if row else 0
    completions_today = int(row[1]) if row else 0

    cur = await conn.execute(
        "SELECT DATE(created_at) AS d, COUNT(*) AS n "
        "FROM task_completions "
        "WHERE employee_id = ? AND status = 'approved' "
        "  AND created_at >= DATE('now', '-13 days') "
        "GROUP BY d ORDER BY d ASC",
        (employee_id,),
    )
    daily = [(str(r[0]), int(r[1])) for r in await cur.fetchall()]
    await cur.close()

    # Streak: number of consecutive days ending today with ≥1 approved
    # completion. Walk back from today.
    by_date = dict(daily)
    streak = 0
    from datetime import date, timedelta
    cursor = date.today()
    while True:
        if by_date.get(cursor.isoformat(), 0) > 0:
            streak += 1
            cursor -= timedelta(days=1)
        else:
            break

    return {
        "coins_today": coins_today,
        "completions_today": completions_today,
        "daily_target": DAILY_COIN_TARGET,
        "streak_days": streak,
        "daily_history": [
            {"date": d, "completions": n} for d, n in daily
        ],
        "coins_balance": await coin_balance(conn, employee_id),
    }
