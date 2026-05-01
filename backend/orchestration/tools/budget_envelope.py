"""Per-employee budget envelope decrement — gated on gating.posted.

Source: RealMetaPRD §7.5 (budget tables); phase-1-critical-gap-remediation.md
Phase 4. The pipeline only invokes this after `post-entry` succeeds, so we
trust `entry_id` exists when the gate fires; we still defensive-check.

Logic:
  1. Pull `entry_id` from `post-entry`. Skip if absent.
  2. Pull `envelope_category` from the counterparty resolver (deterministic
     or AI fallback). 'uncategorized' / None → emit `envelope.skipped` and
     return without writing.
  3. Resolve envelope: employee scope first, company scope fallback.
  4. For every expense line of the entry (`account_code` LIKE '6%'),
     INSERT one `budget_allocations` row. For reversal entries, allocate
     a NEGATIVE amount so the running used_cents nets out.
  5. Recompute `used_cents` and emit `envelope.decremented` on the
     dashboard bus.

Note: the canonical decision_traces row per line was already written by
`gl_poster.post`; we deliberately do NOT double-trace here.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from ..context import FingentContext
from ..event_bus import publish_event_dashboard
from ..store.writes import write_tx


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


async def _fetch_entry(
    ctx: FingentContext, entry_id: int
) -> tuple[str | None, int | None]:
    cur = await ctx.store.accounting.execute(
        "SELECT entry_date, reversal_of_id FROM journal_entries WHERE id = ?",
        (entry_id,),
    )
    row = await cur.fetchone()
    await cur.close()
    if row is None:
        return None, None
    return row[0], row[1]


async def _fetch_envelope(
    ctx: FingentContext,
    employee_id: int | None,
    category: str,
    period: str,
) -> tuple[int, int, int] | None:
    """Lookup (envelope_id, cap_cents, soft_threshold_pct).

    Employee scope first; falls back to company scope (scope_id IS NULL).
    """
    if employee_id is not None:
        cur = await ctx.store.accounting.execute(
            "SELECT id, cap_cents, soft_threshold_pct FROM budget_envelopes "
            "WHERE scope_kind = 'employee' AND scope_id = ? "
            "  AND category = ? AND period = ?",
            (employee_id, category, period),
        )
        row = await cur.fetchone()
        await cur.close()
        if row is not None:
            return int(row[0]), int(row[1]), int(row[2] or 0)

    cur = await ctx.store.accounting.execute(
        "SELECT id, cap_cents, soft_threshold_pct FROM budget_envelopes "
        "WHERE scope_kind = 'company' AND scope_id IS NULL "
        "  AND category = ? AND period = ?",
        (category, period),
    )
    row = await cur.fetchone()
    await cur.close()
    if row is None:
        return None
    return int(row[0]), int(row[1]), int(row[2] or 0)


async def _expense_lines(
    ctx: FingentContext, entry_id: int
) -> list[tuple[int, int, int]]:
    cur = await ctx.store.accounting.execute(
        "SELECT id, debit_cents, credit_cents FROM journal_lines "
        "WHERE entry_id = ? AND account_code LIKE '6%'",
        (entry_id,),
    )
    rows = await cur.fetchall()
    await cur.close()
    return [(int(r[0]), int(r[1]), int(r[2])) for r in rows]


async def _used_cents(ctx: FingentContext, envelope_id: int) -> int:
    cur = await ctx.store.accounting.execute(
        "SELECT COALESCE(SUM(amount_cents), 0) FROM budget_allocations "
        "WHERE envelope_id = ?",
        (envelope_id,),
    )
    row = await cur.fetchone()
    await cur.close()
    return int(row[0]) if row is not None else 0


async def decrement(ctx: FingentContext) -> dict[str, Any]:
    posted = ctx.get("post-entry") or {}
    entry_id = posted.get("entry_id")
    if entry_id is None:
        return {"skipped": True, "reason": "no_entry"}

    cp = ctx.get("resolve-counterparty") or ctx.get("ai-counterparty-fallback") or {}
    category = cp.get("envelope_category") or "uncategorized"
    employee_id = ctx.employee_id

    if category in (None, "uncategorized"):
        await publish_event_dashboard({
            "event_type": "envelope.skipped",
            "data": {
                "entry_id": entry_id,
                "reason": "uncategorized",
                "employee_id": employee_id,
            },
            "ts": _iso_now(),
        })
        return {"skipped": True, "reason": "uncategorized"}

    entry_date, reversal_of_id = await _fetch_entry(ctx, entry_id)
    if entry_date is None:
        return {"skipped": True, "reason": "entry_not_found"}
    period = entry_date[:7]  # YYYY-MM

    env = await _fetch_envelope(ctx, employee_id, category, period)
    if env is None:
        await publish_event_dashboard({
            "event_type": "envelope.no_envelope",
            "data": {
                "entry_id": entry_id,
                "category": category,
                "period": period,
                "employee_id": employee_id,
            },
            "ts": _iso_now(),
        })
        return {"skipped": True, "reason": "no_envelope"}

    envelope_id, cap_cents, soft_threshold_pct = env

    lines = await _expense_lines(ctx, entry_id)
    if not lines:
        return {"skipped": True, "reason": "no_expense_lines"}

    is_reversal = reversal_of_id is not None
    allocations: list[dict[str, int]] = []

    async with write_tx(ctx.store.accounting, ctx.store.accounting_lock) as conn:
        for line_id, debit_cents, credit_cents in lines:
            if is_reversal:
                # Reversal swaps Dr->Cr on the expense line; whichever side is
                # populated, allocate the negation.
                base = debit_cents if debit_cents > 0 else credit_cents
                amount_cents = -base
            else:
                amount_cents = debit_cents
            await conn.execute(
                "INSERT INTO budget_allocations "
                "(envelope_id, line_id, amount_cents) VALUES (?, ?, ?)",
                (envelope_id, line_id, amount_cents),
            )
            allocations.append({
                "line_id": line_id,
                "amount_cents": amount_cents,
            })
            # Note: gl_poster.post already wrote the canonical decision_traces
            # row for this line (source='rule', confidence=1.0). We skip a
            # second trace here to avoid double-tracing.

    used_cents = await _used_cents(ctx, envelope_id)

    await publish_event_dashboard({
        "event_type": "envelope.decremented",
        "data": {
            "envelope_id": envelope_id,
            "employee_id": employee_id,
            "category": category,
            "period": period,
            "used_cents": used_cents,
            "cap_cents": cap_cents,
            "soft_threshold_pct": soft_threshold_pct,
            "ledger_entry_id": entry_id,
        },
        "ts": _iso_now(),
    })

    return {
        "envelope_id": envelope_id,
        "allocations": allocations,
        "used_cents": used_cents,
        "cap_cents": cap_cents,
    }
