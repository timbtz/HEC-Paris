"""Phase 3 SQL-only reporting endpoints.

Six GET endpoints under `/reports/*`. All endpoints are pure SQL over the
existing accounting schema — no agents, no pipelines, no writes. Money is
integer cents; the response envelope hard-codes `currency: "EUR"` until
multi-currency support lands (Phase 4).

Routes:
  GET /reports/trial_balance       ?as_of=YYYY-MM-DD&basis=accrual|cash
  GET /reports/balance_sheet       ?as_of=YYYY-MM-DD&basis=accrual|cash
  GET /reports/income_statement    ?from=&to=&basis=
  GET /reports/cashflow            ?from=&to=
  GET /reports/budget_vs_actuals   ?period=YYYY-MM&employee_id=&category=
  GET /reports/vat_return          ?period=YYYY-MM
"""
from __future__ import annotations

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Query, Request

from .runs import _rows_to_dicts


router = APIRouter(prefix="/reports")

_CURRENCY = "EUR"
_CASH_ACCOUNT = "512"  # PCG Banque — single bank account in MVP.


# --------------------------------------------------------------------------- #
# Trial balance
# --------------------------------------------------------------------------- #

@router.get("/trial_balance")
async def trial_balance(
    request: Request,
    as_of: Annotated[str, Query(pattern=r"^\d{4}-\d{2}-\d{2}$")],
    basis: Annotated[Literal["cash", "accrual"], Query()] = "accrual",
) -> dict[str, Any]:
    store = request.app.state.store
    cur = await store.accounting.execute(
        "WITH posted AS ("
        "  SELECT jl.account_code, jl.debit_cents, jl.credit_cents"
        "  FROM journal_lines jl"
        "  JOIN journal_entries je ON je.id = jl.entry_id"
        "  WHERE je.status = 'posted'"
        "    AND je.entry_date <= ?"
        "    AND je.basis = ?"
        ")"
        "SELECT coa.code, coa.name, coa.type,"
        "       COALESCE(SUM(p.debit_cents),0)  AS total_debit_cents,"
        "       COALESCE(SUM(p.credit_cents),0) AS total_credit_cents,"
        "       COALESCE(SUM(p.debit_cents),0) - COALESCE(SUM(p.credit_cents),0) AS balance_cents "
        "FROM chart_of_accounts coa "
        "LEFT JOIN posted p ON p.account_code = coa.code "
        "GROUP BY coa.code "
        "ORDER BY coa.code",
        (as_of, basis),
    )
    rows = _rows_to_dicts(list(await cur.fetchall()))
    await cur.close()

    total_debit = sum(int(r["total_debit_cents"]) for r in rows)
    total_credit = sum(int(r["total_credit_cents"]) for r in rows)
    return {
        "as_of": as_of,
        "basis": basis,
        "currency": _CURRENCY,
        "lines": rows,
        "totals": {
            "total_debit_cents": total_debit,
            "total_credit_cents": total_credit,
            "balanced": total_debit == total_credit,
        },
    }


# --------------------------------------------------------------------------- #
# Balance sheet
# --------------------------------------------------------------------------- #

@router.get("/balance_sheet")
async def balance_sheet(
    request: Request,
    as_of: Annotated[str, Query(pattern=r"^\d{4}-\d{2}-\d{2}$")],
    basis: Annotated[Literal["cash", "accrual"], Query()] = "accrual",
) -> dict[str, Any]:
    """Balance sheet sections.

    Until `year_end_close` posts the retained-earnings entry, the live
    revenue+expense balance is folded into a synthetic
    `provisional_retained_earnings` line and `provisional: true` is set.
    """
    store = request.app.state.store
    cur = await store.accounting.execute(
        "WITH posted AS ("
        "  SELECT jl.account_code, jl.debit_cents, jl.credit_cents"
        "  FROM journal_lines jl"
        "  JOIN journal_entries je ON je.id = jl.entry_id"
        "  WHERE je.status = 'posted'"
        "    AND je.entry_date <= ?"
        "    AND je.basis = ?"
        ") "
        "SELECT coa.code, coa.name, coa.type,"
        "       COALESCE(SUM(p.debit_cents),0)  AS total_debit_cents,"
        "       COALESCE(SUM(p.credit_cents),0) AS total_credit_cents "
        "FROM chart_of_accounts coa "
        "LEFT JOIN posted p ON p.account_code = coa.code "
        "GROUP BY coa.code "
        "ORDER BY coa.code",
        (as_of, basis),
    )
    rows = _rows_to_dicts(list(await cur.fetchall()))
    await cur.close()

    assets: list[dict[str, Any]] = []
    liabilities: list[dict[str, Any]] = []
    equity: list[dict[str, Any]] = []

    revenue_total = 0  # CR-natural, recognized as income (positive net = profit)
    expense_total = 0  # DR-natural

    for r in rows:
        # For assets: balance = debit - credit (DR-natural positive).
        # For liability/equity: present as credit-natural, so flip sign.
        debit = int(r["total_debit_cents"])
        credit = int(r["total_credit_cents"])
        coa_type = r["type"]
        if coa_type in ("asset", "contra"):
            balance = debit - credit
            assets.append({
                "code": r["code"], "name": r["name"], "type": coa_type,
                "balance_cents": balance,
            })
        elif coa_type == "liability":
            balance = credit - debit
            liabilities.append({
                "code": r["code"], "name": r["name"], "type": coa_type,
                "balance_cents": balance,
            })
        elif coa_type == "equity":
            balance = credit - debit
            equity.append({
                "code": r["code"], "name": r["name"], "type": coa_type,
                "balance_cents": balance,
            })
        elif coa_type == "revenue":
            revenue_total += credit - debit
        elif coa_type == "expense":
            expense_total += debit - credit

    net_income = revenue_total - expense_total
    provisional = net_income != 0
    if provisional:
        equity.append({
            "code": "_provisional_re",
            "name": "Provisional retained earnings (P&L not yet closed)",
            "type": "equity",
            "balance_cents": net_income,
        })

    total_assets = sum(int(r["balance_cents"]) for r in assets)
    total_liab = sum(int(r["balance_cents"]) for r in liabilities)
    total_eq = sum(int(r["balance_cents"]) for r in equity)
    total_le = total_liab + total_eq

    return {
        "as_of": as_of,
        "basis": basis,
        "currency": _CURRENCY,
        "sections": {
            "assets": assets,
            "liabilities": liabilities,
            "equity": equity,
        },
        "totals": {
            "total_assets_cents": total_assets,
            "total_liabilities_equity_cents": total_le,
            "balanced": total_assets == total_le,
        },
        "provisional": provisional,
    }


# --------------------------------------------------------------------------- #
# Income statement
# --------------------------------------------------------------------------- #

@router.get("/income_statement")
async def income_statement(
    request: Request,
    from_: Annotated[str, Query(alias="from", pattern=r"^\d{4}-\d{2}-\d{2}$")],
    to: Annotated[str, Query(pattern=r"^\d{4}-\d{2}-\d{2}$")],
    basis: Annotated[Literal["cash", "accrual"], Query()] = "accrual",
) -> dict[str, Any]:
    store = request.app.state.store
    cur = await store.accounting.execute(
        "WITH posted AS ("
        "  SELECT jl.account_code, jl.debit_cents, jl.credit_cents"
        "  FROM journal_lines jl"
        "  JOIN journal_entries je ON je.id = jl.entry_id"
        "  WHERE je.status = 'posted'"
        "    AND je.entry_date BETWEEN ? AND ?"
        "    AND je.basis = ?"
        ") "
        "SELECT coa.code, coa.name, coa.type,"
        "       COALESCE(SUM(p.debit_cents),0)  AS total_debit_cents,"
        "       COALESCE(SUM(p.credit_cents),0) AS total_credit_cents "
        "FROM chart_of_accounts coa "
        "LEFT JOIN posted p ON p.account_code = coa.code "
        "WHERE coa.type IN ('revenue','expense') "
        "GROUP BY coa.code "
        "ORDER BY coa.code",
        (from_, to, basis),
    )
    rows = _rows_to_dicts(list(await cur.fetchall()))
    await cur.close()

    revenue: list[dict[str, Any]] = []
    expense: list[dict[str, Any]] = []
    revenue_total = 0
    expense_total = 0
    for r in rows:
        debit = int(r["total_debit_cents"])
        credit = int(r["total_credit_cents"])
        if r["type"] == "revenue":
            balance = credit - debit
            revenue.append({
                "code": r["code"], "name": r["name"], "balance_cents": balance,
            })
            revenue_total += balance
        else:
            balance = debit - credit
            expense.append({
                "code": r["code"], "name": r["name"], "balance_cents": balance,
            })
            expense_total += balance

    return {
        "from": from_,
        "to": to,
        "basis": basis,
        "currency": _CURRENCY,
        "sections": {"revenue": revenue, "expense": expense},
        "totals": {
            "total_revenue_cents": revenue_total,
            "total_expense_cents": expense_total,
            "net_income_cents": revenue_total - expense_total,
        },
    }


# --------------------------------------------------------------------------- #
# Cashflow
# --------------------------------------------------------------------------- #

@router.get("/cashflow")
async def cashflow(
    request: Request,
    from_: Annotated[str, Query(alias="from", pattern=r"^\d{4}-\d{2}-\d{2}$")],
    to: Annotated[str, Query(pattern=r"^\d{4}-\d{2}-\d{2}$")],
) -> dict[str, Any]:
    """Direct-method cashflow.

    Walks every cash-basis journal_line that hits the cash account (`512`)
    in the range, and bins the contra account into operating / investing /
    financing based on `chart_of_accounts.type`:

      - revenue / expense → operating
      - asset (non-cash)  → investing
      - liability / equity → financing
    """
    store = request.app.state.store

    # Find every entry with at least one cash leg, in the range.
    cur = await store.accounting.execute(
        "SELECT je.id AS entry_id "
        "FROM journal_entries je "
        "JOIN journal_lines jl ON jl.entry_id = je.id "
        "WHERE je.status = 'posted' "
        "  AND je.basis = 'cash' "
        "  AND je.entry_date BETWEEN ? AND ? "
        "  AND jl.account_code = ? "
        "GROUP BY je.id",
        (from_, to, _CASH_ACCOUNT),
    )
    entry_id_rows = list(await cur.fetchall())
    await cur.close()
    entry_ids = [int(r[0]) for r in entry_id_rows]

    operating = 0
    investing = 0
    financing = 0
    if entry_ids:
        placeholders = ",".join("?" for _ in entry_ids)
        cur = await store.accounting.execute(
            f"SELECT jl.account_code, jl.debit_cents, jl.credit_cents, coa.type "
            f"FROM journal_lines jl "
            f"JOIN chart_of_accounts coa ON coa.code = jl.account_code "
            f"WHERE jl.entry_id IN ({placeholders})",
            tuple(entry_ids),
        )
        line_rows = list(await cur.fetchall())
        await cur.close()

        # Each entry has a cash leg + a contra leg. Determine cash delta
        # per entry by summing cash-account debits and credits.
        cash_per_entry: dict[int, int] = {}
        contra_per_entry: dict[int, list[dict[str, Any]]] = {}
        for entry_id in entry_ids:
            cash_per_entry[entry_id] = 0
            contra_per_entry[entry_id] = []

        # Re-query to know which entry each row belongs to.
        cur = await store.accounting.execute(
            f"SELECT jl.entry_id, jl.account_code, jl.debit_cents, jl.credit_cents, coa.type "
            f"FROM journal_lines jl "
            f"JOIN chart_of_accounts coa ON coa.code = jl.account_code "
            f"WHERE jl.entry_id IN ({placeholders})",
            tuple(entry_ids),
        )
        rows2 = list(await cur.fetchall())
        await cur.close()

        for r in rows2:
            entry_id = int(r["entry_id"])
            account_code = r["account_code"]
            debit = int(r["debit_cents"])
            credit = int(r["credit_cents"])
            coa_type = r["type"]
            if account_code == _CASH_ACCOUNT:
                cash_per_entry[entry_id] += debit - credit  # DR-natural for assets
            else:
                contra_per_entry[entry_id].append({"type": coa_type, "amount": debit - credit})

        for entry_id, cash_delta in cash_per_entry.items():
            contras = contra_per_entry.get(entry_id, [])
            # Single-contra entries dominate; use the first contra's type
            # to bin the cash flow. Multi-contra (rare) attributes to the
            # largest contra by absolute amount.
            if not contras:
                continue
            top = max(contras, key=lambda x: abs(int(x["amount"])))
            t = top["type"]
            if t in ("revenue", "expense"):
                operating += cash_delta
            elif t == "asset":
                investing += cash_delta
            elif t in ("liability", "equity"):
                financing += cash_delta

    # Opening / closing cash balance.
    cur = await store.accounting.execute(
        "SELECT COALESCE(SUM(debit_cents - credit_cents),0) "
        "FROM journal_lines jl "
        "JOIN journal_entries je ON je.id = jl.entry_id "
        "WHERE jl.account_code = ? "
        "  AND je.status = 'posted' "
        "  AND je.entry_date < ?",
        (_CASH_ACCOUNT, from_),
    )
    opening_row = await cur.fetchone()
    await cur.close()
    opening = int(opening_row[0]) if opening_row else 0

    net_change = operating + investing + financing
    closing = opening + net_change

    return {
        "from": from_,
        "to": to,
        "currency": _CURRENCY,
        "sections": {
            "operating_cents": operating,
            "investing_cents": investing,
            "financing_cents": financing,
        },
        "totals": {
            "net_change_cents": net_change,
            "opening_balance_cents": opening,
            "closing_balance_cents": closing,
        },
    }


# --------------------------------------------------------------------------- #
# Budget vs actuals
# --------------------------------------------------------------------------- #

@router.get("/budget_vs_actuals")
async def budget_vs_actuals(
    request: Request,
    period: Annotated[str, Query(pattern=r"^\d{4}-\d{2}$")],
    employee_id: Annotated[int | None, Query()] = None,
    category: Annotated[str | None, Query()] = None,
) -> dict[str, Any]:
    store = request.app.state.store
    clauses = ["be.period = ?"]
    params: list[Any] = [period]
    if employee_id is not None:
        clauses.append("(be.scope_kind = 'employee' AND be.scope_id = ?)")
        params.append(employee_id)
    if category is not None:
        clauses.append("be.category = ?")
        params.append(category)
    where = "WHERE " + " AND ".join(clauses)
    cur = await store.accounting.execute(
        f"SELECT be.id AS envelope_id, be.scope_kind, be.scope_id, be.category,"
        f"       be.period, be.cap_cents, be.soft_threshold_pct,"
        f"       COALESCE(SUM(ba.amount_cents), 0) AS used_cents,"
        f"       COUNT(ba.id) AS allocation_count "
        f"FROM budget_envelopes be "
        f"LEFT JOIN budget_allocations ba ON ba.envelope_id = be.id "
        f"{where} "
        f"GROUP BY be.id "
        f"ORDER BY be.scope_kind, be.scope_id, be.category",
        tuple(params),
    )
    rows = _rows_to_dicts(list(await cur.fetchall()))
    await cur.close()

    lines: list[dict[str, Any]] = []
    total_cap = 0
    total_used = 0
    for r in rows:
        cap = int(r["cap_cents"])
        used = int(r["used_cents"])
        remaining = cap - used
        pct_used = round(100.0 * used / cap, 2) if cap > 0 else 0.0
        total_cap += cap
        total_used += used
        lines.append({
            "envelope_id": r["envelope_id"],
            "scope_kind": r["scope_kind"],
            "scope_id": r["scope_id"],
            "category": r["category"],
            "cap_cents": cap,
            "used_cents": used,
            "remaining_cents": remaining,
            "pct_used": pct_used,
            "allocation_count": int(r["allocation_count"]),
        })

    return {
        "period": period,
        "currency": _CURRENCY,
        "lines": lines,
        "totals": {
            "total_cap_cents": total_cap,
            "total_used_cents": total_used,
            "total_remaining_cents": total_cap - total_used,
        },
    }


# --------------------------------------------------------------------------- #
# VAT return
# --------------------------------------------------------------------------- #

@router.get("/vat_return")
async def vat_return(
    request: Request,
    period: Annotated[str, Query(pattern=r"^\d{4}-\d{2}$")],
) -> dict[str, Any]:
    """VAT return for a YYYY-MM period.

    Walks `journal_lines × vat_rates` where `vat_rates.valid_from <= entry_date
    AND (vat_rates.valid_to IS NULL OR vat_rates.valid_to > entry_date)`,
    grouping by GL account and rate. Collected VAT (`445`) and deductible
    VAT (`4456`) are summed separately; net due = collected - deductible.
    """
    store = request.app.state.store
    period_start = f"{period}-01"
    # Last day of the month (works for any month; SQLite `date('start of month','+1 month','-1 day')`).
    cur = await store.accounting.execute(
        "SELECT date(?, 'start of month', '+1 month', '-1 day')",
        (period_start,),
    )
    end_row = await cur.fetchone()
    await cur.close()
    period_end = end_row[0] if end_row else period_start

    cur = await store.accounting.execute(
        "SELECT vr.gl_account, vr.rate_bp,"
        "       COALESCE(SUM(jl.debit_cents - jl.credit_cents), 0) AS net_cents,"
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
        (period_start, period_end, period_start, period_end),
    )
    rows = _rows_to_dicts(list(await cur.fetchall()))
    await cur.close()

    lines: list[dict[str, Any]] = []
    collected = 0
    deductible = 0
    for r in rows:
        gl_account = r["gl_account"]
        debit = int(r["debit_cents"])
        credit = int(r["credit_cents"])
        # Output VAT (445): credit-natural; the row's CR-DR is the period
        # collected VAT. Input VAT (4456): debit-natural; DR-CR is deductible.
        if gl_account == "445":
            vat_amount = credit - debit
            collected += vat_amount
        else:
            vat_amount = debit - credit
            deductible += vat_amount
        lines.append({
            "gl_account": gl_account,
            "rate_bp": int(r["rate_bp"]),
            "vat_cents": vat_amount,
            "debit_cents": debit,
            "credit_cents": credit,
        })

    return {
        "period": period,
        "currency": _CURRENCY,
        "lines": lines,
        "totals": {
            "collected_cents": collected,
            "deductible_cents": deductible,
            "net_due_cents": collected - deductible,
        },
    }
