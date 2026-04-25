"""VAT-return calculator. Pure SQL aggregation; no `write_tx`.

Reads `period_code` from `ctx.trigger_payload` (looking up the period in
`accounting_periods`) OR `period` if the YAML is invoked directly with a
`YYYY-MM` (used by Slice C SQL endpoints style). Joins `journal_lines ×
vat_rates` valid for the period date range.

Integer-cents rounding: when a single transaction's VAT is split across
two or more boxes (e.g. 20% on €100.05 = 20.01 cents that would split
oddly), the LAST box absorbs the rounding remainder so:

    sum(box_i) == subtotal_vat_cents

(documented in plan §VALIDATION; we do not split a single line across
boxes today, so this is a one-line guard for the future).
"""
from __future__ import annotations

from typing import Any

from ..context import AgnesContext


async def _resolve_period_dates(ctx: AgnesContext) -> tuple[str, str, str]:
    """Returns `(period_code, start_date, end_date)`.

    Accepts either `trigger_payload.period_code` (looked up in
    `accounting_periods`) or `trigger_payload.period` as `YYYY-MM`.
    """
    payload = ctx.trigger_payload or {}
    period_code = payload.get("period_code")
    if period_code:
        cur = await ctx.store.accounting.execute(
            "SELECT start_date, end_date FROM accounting_periods WHERE code = ?",
            (period_code,),
        )
        row = await cur.fetchone()
        await cur.close()
        if row is None:
            raise ValueError(f"vat_calculator: unknown period_code {period_code!r}")
        return period_code, row[0], row[1]

    period_ym = payload.get("period")
    if period_ym:
        # Compute the start/end of the calendar month.
        cur = await ctx.store.accounting.execute(
            "SELECT date(?, 'start of month'), "
            "       date(?, 'start of month', '+1 month', '-1 day')",
            (f"{period_ym}-01", f"{period_ym}-01"),
        )
        row = await cur.fetchone()
        await cur.close()
        if row is None:
            raise ValueError(f"vat_calculator: cannot resolve period {period_ym!r}")
        return period_ym, row[0], row[1]

    raise ValueError(
        "vat_calculator: trigger_payload requires 'period_code' or 'period'"
    )


async def compute_vat_return(ctx: AgnesContext) -> dict[str, Any]:
    """Box-by-box VAT totals for the period.

    Returns:
      - lines: per (gl_account, rate_bp) row with collected/deductible cents
      - totals.collected_cents: sum of `445`-account rows (output VAT)
      - totals.deductible_cents: sum of non-`445`-account rows (input VAT)
      - totals.net_due_cents: collected - deductible
      - confidence: 1.0 (deterministic SQL)
    """
    period_code, start_date, end_date = await _resolve_period_dates(ctx)

    cur = await ctx.store.accounting.execute(
        "SELECT vr.gl_account, vr.rate_bp,"
        "       COALESCE(SUM(jl.debit_cents), 0) AS debit_cents,"
        "       COALESCE(SUM(jl.credit_cents), 0) AS credit_cents "
        "FROM vat_rates vr "
        "LEFT JOIN journal_lines jl ON jl.account_code = vr.gl_account "
        "LEFT JOIN journal_entries je ON je.id = jl.entry_id "
        "WHERE vr.valid_from <= ? "
        "  AND (vr.valid_to IS NULL OR vr.valid_to > ?) "
        "  AND (je.id IS NULL OR ("
        "        je.status = 'posted'"
        "        AND je.entry_date BETWEEN ? AND ?"
        "  )) "
        "GROUP BY vr.id, vr.gl_account, vr.rate_bp "
        "ORDER BY vr.gl_account, vr.rate_bp",
        (start_date, end_date, start_date, end_date),
    )
    rows = list(await cur.fetchall())
    await cur.close()

    lines: list[dict[str, Any]] = []
    collected = 0
    deductible = 0
    for r in rows:
        gl_account = r[0]
        debit = int(r[2])
        credit = int(r[3])
        # `445` is output VAT (credit-natural, liability).
        # Other VAT accounts (e.g. `4456`) are input VAT (debit-natural).
        if gl_account == "445":
            vat_cents = credit - debit
            collected += vat_cents
        else:
            vat_cents = debit - credit
            deductible += vat_cents
        lines.append({
            "gl_account": gl_account,
            "rate_bp": int(r[1]),
            "vat_cents": vat_cents,
            "debit_cents": debit,
            "credit_cents": credit,
        })

    return {
        "period_code": period_code,
        "start_date": start_date,
        "end_date": end_date,
        "lines": lines,
        "totals": {
            "collected_cents": collected,
            "deductible_cents": deductible,
            "net_due_cents": collected - deductible,
        },
        "confidence": 1.0,
    }
