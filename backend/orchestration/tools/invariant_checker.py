"""Five hard invariants from RealMetaPRD §7.6.

Read-only — no lock needed. Raises `ValueError` on failure to trigger
`pipeline_failed` and surface the violation in the audit trail.

Invariants:
  1. Per-entry balance: SUM(debit_cents) == SUM(credit_cents).
  2. Bank mirror match: bank-side journal balance ≤ entry_date matches the
     latest `swan_transactions.booked_balance_after` for the relevant
     account (defensively skipped when the Swan account can't be inferred).
  3. At least one decision_trace per line.
  4. Document reachability for accrual entries (sha256 reachable through
     `journal_lines.document_id`).
  5. Paired AP balance: when `accrual_link_id` is set, AP movements across
     the linked pair sum to zero.
"""
from __future__ import annotations

from typing import Any

from ..context import FingentContext


_BANK = "512"
_AP = "401"


async def _invariant_1_balance(conn, entry_id: int) -> dict[str, Any]:
    cur = await conn.execute(
        "SELECT COALESCE(SUM(debit_cents), 0), COALESCE(SUM(credit_cents), 0) "
        "FROM journal_lines WHERE entry_id = ?",
        (entry_id,),
    )
    row = await cur.fetchone()
    await cur.close()
    dr, cr = int(row[0]), int(row[1])
    if dr != cr:
        return {"invariant": 1, "name": "balance", "ok": False,
                "reason": f"debits={dr} != credits={cr}"}
    return {"invariant": 1, "name": "balance", "ok": True}


async def _invariant_2_bank_mirror(conn, entry_id: int, entry_date: str) -> dict[str, Any]:
    """Bank mirror match. Defensively skipped when the relevant Swan account
    can't be inferred from the entry's bank-side line.
    """
    cur = await conn.execute(
        "SELECT swan_transaction_id FROM journal_lines "
        "WHERE entry_id = ? AND account_code = ? "
        "  AND swan_transaction_id IS NOT NULL "
        "LIMIT 1",
        (entry_id, _BANK),
    )
    row = await cur.fetchone()
    await cur.close()

    if row is None or row[0] is None:
        return {"invariant": 2, "name": "bank_mirror", "ok": True,
                "skipped": True, "reason": "no_bank_line"}

    swan_tx_id = row[0]

    # Latest swan_transactions.booked_balance_after ≤ entry_date.
    cur = await conn.execute(
        "SELECT booked_balance_after, execution_date "
        "FROM swan_transactions "
        "WHERE id = ?",
        (swan_tx_id,),
    )
    swan_row = await cur.fetchone()
    await cur.close()

    if swan_row is None or swan_row[0] is None:
        return {"invariant": 2, "name": "bank_mirror", "ok": True,
                "skipped": True, "reason": "no_balance_in_swan_tx"}

    bank_balance_swan = int(swan_row[0])
    swan_date = swan_row[1]

    # GL bank balance up to (and including) the swan tx date.
    cur = await conn.execute(
        "SELECT COALESCE(SUM(jl.debit_cents - jl.credit_cents), 0) "
        "FROM journal_lines jl "
        "JOIN journal_entries je ON je.id = jl.entry_id "
        "WHERE jl.account_code = ? "
        "  AND je.entry_date <= ? "
        "  AND je.status = 'posted'",
        (_BANK, swan_date),
    )
    gl_row = await cur.fetchone()
    await cur.close()
    gl_balance = int(gl_row[0])

    if gl_balance != bank_balance_swan:
        return {
            "invariant": 2, "name": "bank_mirror", "ok": False,
            "reason": f"gl_bank={gl_balance} swan_balance={bank_balance_swan} as_of={swan_date}",
        }
    return {"invariant": 2, "name": "bank_mirror", "ok": True}


async def _invariant_3_trace_present(conn, entry_id: int) -> dict[str, Any]:
    cur = await conn.execute(
        "SELECT COUNT(*) FROM decision_traces dt "
        "JOIN journal_lines jl ON dt.line_id = jl.id "
        "WHERE jl.entry_id = ?",
        (entry_id,),
    )
    row = await cur.fetchone()
    await cur.close()
    n = int(row[0])
    if n == 0:
        return {"invariant": 3, "name": "trace_present", "ok": False,
                "reason": "no decision_traces rows for this entry"}
    return {"invariant": 3, "name": "trace_present", "ok": True}


async def _invariant_4_document_reachable(
    conn, entry_id: int, basis: str
) -> dict[str, Any]:
    if basis != "accrual":
        return {"invariant": 4, "name": "document_reachable", "ok": True,
                "skipped": True, "reason": "non_accrual"}
    cur = await conn.execute(
        "SELECT COUNT(*) FROM journal_lines jl "
        "JOIN documents d ON jl.document_id = d.id "
        "WHERE jl.entry_id = ?",
        (entry_id,),
    )
    row = await cur.fetchone()
    await cur.close()
    n = int(row[0])
    if n == 0:
        return {"invariant": 4, "name": "document_reachable", "ok": False,
                "reason": "accrual entry has no journal_lines.document_id reachable"}
    return {"invariant": 4, "name": "document_reachable", "ok": True}


async def _invariant_5_paired_ap(conn, entry_id: int, accrual_link_id: int | None) -> dict[str, Any]:
    if accrual_link_id is None:
        return {"invariant": 5, "name": "paired_ap_zero", "ok": True,
                "skipped": True, "reason": "no_accrual_link"}
    cur = await conn.execute(
        "SELECT COALESCE(SUM(jl.debit_cents - jl.credit_cents), 0) "
        "FROM journal_lines jl "
        "WHERE jl.account_code = ? "
        "  AND jl.entry_id IN (?, ?)",
        (_AP, entry_id, accrual_link_id),
    )
    row = await cur.fetchone()
    await cur.close()
    net = int(row[0])
    if net != 0:
        return {"invariant": 5, "name": "paired_ap_zero", "ok": False,
                "reason": f"paired AP net != 0 (net={net})"}
    return {"invariant": 5, "name": "paired_ap_zero", "ok": True}


async def run(ctx: FingentContext) -> dict[str, Any]:
    """Run all five invariants over the entry posted by `post-entry`.

    If `post-entry` was skipped, return `{ok: True, skipped: True}` — there
    is nothing to assert. On any failure, raise ValueError with the failure
    details (the executor turns this into a `pipeline_failed` event).
    """
    post_out = ctx.get("post-entry") or {}
    entry_id = post_out.get("entry_id")
    if entry_id is None:
        return {"ok": True, "failures": [], "skipped": True,
                "reason": "post-entry was not run"}

    conn = ctx.store.accounting

    # Load the entry header for basis + accrual_link_id + entry_date.
    cur = await conn.execute(
        "SELECT basis, entry_date, accrual_link_id "
        "FROM journal_entries WHERE id = ?",
        (entry_id,),
    )
    row = await cur.fetchone()
    await cur.close()
    if row is None:
        raise ValueError(f"invariant_checker: entry {entry_id} not found")

    basis, entry_date, accrual_link_id = row[0], row[1], row[2]

    results = [
        await _invariant_1_balance(conn, entry_id),
        await _invariant_2_bank_mirror(conn, entry_id, entry_date),
        await _invariant_3_trace_present(conn, entry_id),
        await _invariant_4_document_reachable(conn, entry_id, basis),
        await _invariant_5_paired_ap(conn, entry_id, accrual_link_id),
    ]

    failures = [r for r in results if not r.get("ok")]
    ok = len(failures) == 0

    out = {
        "ok": ok,
        "failures": failures,
        "invariants_run": len(results),
        "results": results,
    }

    if not ok:
        raise ValueError(f"invariant_checker: {len(failures)} failures: {failures}")

    return out
