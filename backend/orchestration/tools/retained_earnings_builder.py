"""Year-end closing entry builder.

Constructs the journal entry that zeroes every revenue/expense account
into the equity-side retained-earnings account (`120` per French PCG).
Returns the built shape (no DB write); the YAML pipeline's downstream
`tools.gl_poster:post` performs the chokepoint write.

Idempotency guard: the year_end_close pipeline can be invoked twice for
the same fiscal year; we check `period_reports` for an existing `final`
year-end-close row and emit `{skip: True, reason: ...}` if found.
"""
from __future__ import annotations

from typing import Any

from ..context import AgnesContext

_RETAINED_EARNINGS_CODE = "120"


async def _existing_close(ctx: AgnesContext, period_code: str) -> bool:
    cur = await ctx.store.accounting.execute(
        "SELECT 1 FROM period_reports "
        "WHERE period_code = ? AND report_type = 'year_end_close' "
        "  AND status = 'final' LIMIT 1",
        (period_code,),
    )
    row = await cur.fetchone()
    await cur.close()
    return row is not None


async def build_closing_entry(ctx: AgnesContext) -> dict[str, Any]:
    """Build the year-end closing journal entry.

    Reads the trial balance from `ctx.get('compute-trial-balance')`. For
    every revenue account with a credit balance, post a debit; for every
    expense account with a debit balance, post a credit. Net (revenue -
    expense) flows into account `120` retained earnings.

    Returns the shape `gl_poster.post` consumes:
      {basis, entry_date, description, lines: [...], confidence}
    Or `{skip: True, reason: ...}` if the close already happened.
    """
    period_code = (ctx.trigger_payload or {}).get("period_code")
    if not period_code:
        raise ValueError(
            "retained_earnings_builder: trigger_payload missing 'period_code'"
        )

    if await _existing_close(ctx, period_code):
        return {"skip": True, "reason": f"year_end_close already final for {period_code}"}

    # Pull period end_date as the entry_date.
    cur = await ctx.store.accounting.execute(
        "SELECT end_date FROM accounting_periods WHERE code = ?",
        (period_code,),
    )
    row = await cur.fetchone()
    await cur.close()
    if row is None:
        raise ValueError(
            f"retained_earnings_builder: unknown period_code {period_code!r}"
        )
    entry_date: str = row[0]

    # Read revenue & expense balances from the trial balance node output.
    trial = ctx.get("compute-trial-balance") or {}
    tb_lines: list[dict[str, Any]] = trial.get("trial_balance") or []

    # Resolve account types from chart_of_accounts in one query.
    codes = [l["account_code"] for l in tb_lines]
    coa_types: dict[str, str] = {}
    if codes:
        placeholders = ",".join("?" for _ in codes)
        cur = await ctx.store.accounting.execute(
            f"SELECT code, type FROM chart_of_accounts WHERE code IN ({placeholders})",
            tuple(codes),
        )
        coa_types = {r[0]: r[1] for r in await cur.fetchall()}
        await cur.close()

    lines: list[dict[str, Any]] = []
    net_credit_cents = 0  # positive = profit (CR retained earnings)

    for tbl in tb_lines:
        code = tbl["account_code"]
        coa_type = coa_types.get(code)
        debit = int(tbl["debit_cents"])
        credit = int(tbl["credit_cents"])
        balance = debit - credit  # debit-natural

        if coa_type == "revenue":
            # Revenue is credit-natural; balance = debit - credit ≤ 0.
            cr_balance = credit - debit
            if cr_balance > 0:
                # Post a debit to zero it out.
                lines.append({
                    "account_code": code,
                    "debit_cents": cr_balance,
                    "credit_cents": 0,
                    "description": f"Year-end close: zero {code}",
                })
                net_credit_cents += cr_balance
        elif coa_type == "expense":
            # Expense is debit-natural; balance ≥ 0.
            if balance > 0:
                lines.append({
                    "account_code": code,
                    "debit_cents": 0,
                    "credit_cents": balance,
                    "description": f"Year-end close: zero {code}",
                })
                net_credit_cents -= balance

    if not lines:
        return {"skip": True, "reason": "no revenue/expense to close"}

    # Offset the net into the retained-earnings account.
    if net_credit_cents > 0:
        lines.append({
            "account_code": _RETAINED_EARNINGS_CODE,
            "debit_cents": 0,
            "credit_cents": net_credit_cents,
            "description": "Year-end close: net income → retained earnings",
        })
    elif net_credit_cents < 0:
        lines.append({
            "account_code": _RETAINED_EARNINGS_CODE,
            "debit_cents": -net_credit_cents,
            "credit_cents": 0,
            "description": "Year-end close: net loss → retained earnings",
        })

    return {
        "basis": "accrual",
        "entry_date": entry_date,
        "description": f"Year-end close {period_code}",
        "lines": lines,
        "confidence": 1.0,
    }
