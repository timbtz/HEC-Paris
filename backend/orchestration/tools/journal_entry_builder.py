"""Booking-pattern builders + accrual matcher + reversal builder.

Source: RealMetaPRD §7.4 booking patterns + reversal pattern.

Money is integer cents end to end. NO floats on the money path.

Six async functions live here. Every one returns a *dict shape* — they
never write to the GL themselves. The single chokepoint that turns a
built shape into rows is `tools.gl_poster:post`.

`mark_reversed` is the one legitimate UPDATE outside `pipeline_runs.status`:
flipping `journal_entries.status` from 'posted' to 'reversed' once the
opposite-sign reversal entry has been posted via `gl_poster`.
"""
from __future__ import annotations

from datetime import date
from typing import Any

from ..context import AgnesContext
from ..store.writes import write_tx


# ---------------------------------------------------------------------------
# Account codes (PCG subset; see migrations/accounting/0002_chart_of_accounts.py)
# ---------------------------------------------------------------------------
_BANK = "512"          # Banque
_AP = "401"            # Fournisseurs
_AR = "411"            # Clients
_VAT_DEDUCTIBLE = "4456"
_BANK_FEES = "627"
_REVENUE = "706000"
_DEFAULT_EXPENSE = "626200"  # Abonnements SaaS — generic fallback


def _today() -> str:
    return date.today().isoformat()


def _line(
    *,
    account_code: str,
    debit_cents: int = 0,
    credit_cents: int = 0,
    counterparty_id: int | None = None,
    swan_transaction_id: str | None = None,
    document_id: int | None = None,
    description: str | None = None,
) -> dict[str, Any]:
    """Construct a journal_lines row dict. Either debit or credit, never both
    (the schema CHECK rejects both > 0)."""
    return {
        "account_code": account_code,
        "debit_cents": int(debit_cents),
        "credit_cents": int(credit_cents),
        "counterparty_id": counterparty_id,
        "swan_transaction_id": swan_transaction_id,
        "document_id": document_id,
        "description": description,
    }


def _vat_split(amount_cents: int, vat_rate_bp: int) -> tuple[int, int]:
    """Return (subtotal_cents, vat_cents) given gross + rate in basis points.

    Rounded half-up via integer math. NO floats (PRD hard rule).
    """
    vat_cents = (amount_cents * vat_rate_bp + 5000) // 10000
    subtotal_cents = amount_cents - vat_cents
    return subtotal_cents, vat_cents


# ---------------------------------------------------------------------------
# build_cash — Swan transaction → cash-basis journal entry
# ---------------------------------------------------------------------------
async def build_cash(ctx: AgnesContext) -> dict[str, Any]:
    """Build a cash-basis journal entry from a Swan transaction.

    Reads:
      - `fetch-transaction`: the canonical Swan tx dict
      - `resolve-counterparty` or `ai-counterparty-fallback`: counterparty info
      - `classify-gl-account` or `ai-account-fallback`: GL classification
      - `match-accrual`: optional `{accrual_link_id}` to pair with prior accrual
    """
    tx = ctx.get("fetch-transaction") or {}
    cp = ctx.get("resolve-counterparty") or ctx.get("ai-counterparty-fallback") or {}
    gl = ctx.get("classify-gl-account") or ctx.get("ai-account-fallback") or {}
    accrual = ctx.get("match-accrual") or {}

    tx_type = tx.get("type", "") or ""
    side = tx.get("side")
    swan_tx_id = tx.get("id") or tx.get("__local_id")
    amount_cents = int(tx.get("amount_cents") or tx.get("amountCents") or 0)
    if not amount_cents:
        # Defensive: amount in {value, currency} dict shape (Swan canonical)
        amt = tx.get("amount")
        if isinstance(amt, dict) and amt.get("value") is not None:
            try:
                amount_cents = int(round(float(amt["value"]) * 100))
            except (TypeError, ValueError):
                amount_cents = 0

    cp_id = cp.get("counterparty_id") if isinstance(cp, dict) else None
    cp_kind = cp.get("kind") if isinstance(cp, dict) else None
    cp_legal_name = cp.get("counterparty_legal_name") if isinstance(cp, dict) else None
    gl_account = (gl.get("gl_account") if isinstance(gl, dict) else None) or _DEFAULT_EXPENSE
    vat_rate_bp = gl.get("vat_rate_bp") if isinstance(gl, dict) else None

    description = f"{tx_type or 'tx'} {cp_legal_name or ''}".strip()
    accrual_link_id = accrual.get("accrual_link_id") if isinstance(accrual, dict) else None

    is_card = "Card" in tx_type or tx_type == "CardOutDebit"
    is_sepa_in = tx_type == "SepaCreditTransferIn" or (side == "Credit" and not is_card)
    is_sepa_out = tx_type == "SepaCreditTransferOut" or (side == "Debit" and not is_card)
    is_fee = "Fees" in tx_type

    lines: list[dict[str, Any]] = []

    if is_card:
        # Card spend: Dr Expense, Cr Bank. Optional VAT split.
        if vat_rate_bp:
            subtotal, vat = _vat_split(amount_cents, int(vat_rate_bp))
            lines.append(_line(
                account_code=gl_account,
                debit_cents=subtotal,
                counterparty_id=cp_id,
                swan_transaction_id=swan_tx_id,
                description=description,
            ))
            if vat > 0:
                lines.append(_line(
                    account_code=_VAT_DEDUCTIBLE,
                    debit_cents=vat,
                    counterparty_id=cp_id,
                    swan_transaction_id=swan_tx_id,
                    description=f"VAT {vat_rate_bp}bp",
                ))
            lines.append(_line(
                account_code=_BANK,
                credit_cents=amount_cents,
                counterparty_id=cp_id,
                swan_transaction_id=swan_tx_id,
                description=description,
            ))
        else:
            lines.append(_line(
                account_code=gl_account,
                debit_cents=amount_cents,
                counterparty_id=cp_id,
                swan_transaction_id=swan_tx_id,
                description=description,
            ))
            lines.append(_line(
                account_code=_BANK,
                credit_cents=amount_cents,
                counterparty_id=cp_id,
                swan_transaction_id=swan_tx_id,
                description=description,
            ))
    elif is_fee:
        # Bank fees: Dr 627, Cr 512.
        lines.append(_line(
            account_code=_BANK_FEES,
            debit_cents=amount_cents,
            counterparty_id=cp_id,
            swan_transaction_id=swan_tx_id,
            description=description,
        ))
        lines.append(_line(
            account_code=_BANK,
            credit_cents=amount_cents,
            counterparty_id=cp_id,
            swan_transaction_id=swan_tx_id,
            description=description,
        ))
    elif is_sepa_in:
        # SEPA-in: Dr Bank; Cr AR if customer, else Revenue.
        credit_account = _AR if cp_kind == "customer" else _REVENUE
        lines.append(_line(
            account_code=_BANK,
            debit_cents=amount_cents,
            counterparty_id=cp_id,
            swan_transaction_id=swan_tx_id,
            description=description,
        ))
        lines.append(_line(
            account_code=credit_account,
            credit_cents=amount_cents,
            counterparty_id=cp_id,
            swan_transaction_id=swan_tx_id,
            description=description,
        ))
    elif is_sepa_out:
        # SEPA-out: Dr AP if matched against prior accrual, else Dr Expense.
        debit_account = _AP if accrual_link_id else gl_account
        lines.append(_line(
            account_code=debit_account,
            debit_cents=amount_cents,
            counterparty_id=cp_id,
            swan_transaction_id=swan_tx_id,
            description=description,
        ))
        lines.append(_line(
            account_code=_BANK,
            credit_cents=amount_cents,
            counterparty_id=cp_id,
            swan_transaction_id=swan_tx_id,
            description=description,
        ))
    else:
        # Internal-transfer placeholder. Lands in review (zero-amount lines
        # mean the entry is balanced but conveys no economic effect).
        lines.append(_line(
            account_code=_BANK,
            debit_cents=0,
            counterparty_id=cp_id,
            swan_transaction_id=swan_tx_id,
            description=description or "internal_transfer_placeholder",
        ))
        lines.append(_line(
            account_code=_BANK,
            credit_cents=0,
            counterparty_id=cp_id,
            swan_transaction_id=swan_tx_id,
            description=description or "internal_transfer_placeholder",
        ))

    entry_date = tx.get("execution_date") or tx.get("executionDate") or _today()

    return {
        "lines": lines,
        "basis": "cash",
        "entry_date": entry_date,
        "description": description or f"{tx_type or 'tx'}",
        "accrual_link_id": accrual_link_id,
        "reversal_of_id": None,
        "confidence": gl.get("confidence", 1.0) if isinstance(gl, dict) else 1.0,
    }


# ---------------------------------------------------------------------------
# build_accrual — extracted invoice → accrual-basis journal entry
# ---------------------------------------------------------------------------
async def build_accrual(ctx: AgnesContext) -> dict[str, Any]:
    """Build an accrual-basis entry from a document-extractor output.

    Reads:
      - `extract`: extraction dict with subtotal_cents / vat_cents / total_cents
      - `resolve-counterparty` / `ai-counterparty-fallback`
      - `classify-gl-account` / `ai-account-fallback`

    Produces: Dr Expense (subtotal), Dr VAT (4456), Cr AP (401, total).
    """
    extraction = ctx.get("extract") or {}
    cp = ctx.get("resolve-counterparty") or ctx.get("ai-counterparty-fallback") or {}
    gl = ctx.get("classify-gl-account") or ctx.get("ai-account-fallback") or {}

    subtotal_cents = int(extraction.get("subtotal_cents", 0) or 0)
    vat_cents = int(extraction.get("vat_cents", 0) or 0)
    total_cents = int(
        extraction.get("total_cents", subtotal_cents + vat_cents) or 0
    )

    cp_id = cp.get("counterparty_id") if isinstance(cp, dict) else None
    cp_legal_name = cp.get("counterparty_legal_name") if isinstance(cp, dict) else None
    gl_account = (gl.get("gl_account") if isinstance(gl, dict) else None) or _DEFAULT_EXPENSE

    document_id = (ctx.trigger_payload or {}).get("document_id")
    description = f"Accrual {cp_legal_name or ''}".strip()

    lines: list[dict[str, Any]] = [
        _line(
            account_code=gl_account,
            debit_cents=subtotal_cents,
            counterparty_id=cp_id,
            document_id=document_id,
            description=description,
        ),
    ]
    if vat_cents > 0:
        lines.append(_line(
            account_code=_VAT_DEDUCTIBLE,
            debit_cents=vat_cents,
            counterparty_id=cp_id,
            document_id=document_id,
            description=f"VAT on {cp_legal_name or ''}".strip(),
        ))
    lines.append(_line(
        account_code=_AP,
        credit_cents=total_cents,
        counterparty_id=cp_id,
        document_id=document_id,
        description=description,
    ))

    entry_date = extraction.get("date") or extraction.get("issue_date") or _today()

    return {
        "lines": lines,
        "basis": "accrual",
        "entry_date": entry_date,
        "description": description or "accrual",
        "accrual_link_id": None,
        "reversal_of_id": None,
        "confidence": gl.get("confidence", 1.0) if isinstance(gl, dict) else 1.0,
    }


# ---------------------------------------------------------------------------
# match_accrual — pair a SEPA-out with an earlier supplier accrual
# ---------------------------------------------------------------------------
async def match_accrual(ctx: AgnesContext) -> dict[str, Any]:
    """For SEPA-out + supplier counterparty: find an unpaired accrual entry
    on the same counterparty whose AP credit equals the SEPA amount.

    Returns `{accrual_link_id}` on hit, `{}` on miss / non-applicable.
    """
    tx = ctx.get("fetch-transaction") or {}
    cp = ctx.get("resolve-counterparty") or ctx.get("ai-counterparty-fallback") or {}

    tx_type = tx.get("type", "") or ""
    side = tx.get("side")
    is_card = "Card" in tx_type or tx_type == "CardOutDebit"
    is_sepa_out = tx_type == "SepaCreditTransferOut" or (side == "Debit" and not is_card)

    if not is_sepa_out:
        return {}

    cp_id = cp.get("counterparty_id") if isinstance(cp, dict) else None
    if cp_id is None:
        return {}

    amount_cents = int(tx.get("amount_cents") or tx.get("amountCents") or 0)
    if not amount_cents:
        amt = tx.get("amount")
        if isinstance(amt, dict) and amt.get("value") is not None:
            try:
                amount_cents = int(round(float(amt["value"]) * 100))
            except (TypeError, ValueError):
                amount_cents = 0

    cur = await ctx.store.accounting.execute(
        "SELECT je.id FROM journal_entries je "
        "JOIN journal_lines jl ON jl.entry_id = je.id "
        "WHERE je.basis = 'accrual' "
        "  AND je.status = 'posted' "
        "  AND jl.counterparty_id = ? "
        "  AND jl.account_code = ? "
        "  AND jl.credit_cents = ? "
        "  AND je.id NOT IN ("
        "       SELECT accrual_link_id FROM journal_entries "
        "        WHERE accrual_link_id IS NOT NULL"
        "  ) "
        "ORDER BY je.created_at DESC LIMIT 1",
        (cp_id, _AP, amount_cents),
    )
    row = await cur.fetchone()
    await cur.close()

    if row is None:
        return {}
    return {"accrual_link_id": int(row[0])}


# ---------------------------------------------------------------------------
# find_original — locate the prior entry to reverse (Released / Canceled)
# ---------------------------------------------------------------------------
async def find_original(ctx: AgnesContext) -> dict[str, Any]:
    """Locate the previously-posted journal entry that this Released/Canceled
    event should reverse. Keys off `swan_transaction_id` on the lines.

    Returns:
      - `{original_entry_id, basis}` if found and not yet reversed.
      - `{skip: True, reason: 'already_reversed', original_entry_id}` if the
         entry is already in 'reversed' status.
      - `{skip: True, reason: 'no_original'}` if no prior entry exists.
    """
    tx_id = (ctx.trigger_payload or {}).get("resourceId") \
        or (ctx.trigger_payload or {}).get("id")
    if not tx_id:
        return {"skip": True, "reason": "no_tx_id"}

    cur = await ctx.store.accounting.execute(
        "SELECT je.id, je.basis, je.status FROM journal_entries je "
        "JOIN journal_lines jl ON jl.entry_id = je.id "
        "WHERE jl.swan_transaction_id = ? "
        "ORDER BY je.created_at DESC LIMIT 1",
        (tx_id,),
    )
    row = await cur.fetchone()
    await cur.close()

    if row is None:
        return {"skip": True, "reason": "no_original"}

    entry_id, basis, status = int(row[0]), row[1], row[2]
    if status == "reversed":
        return {
            "skip": True,
            "reason": "already_reversed",
            "original_entry_id": entry_id,
        }
    return {"original_entry_id": entry_id, "basis": basis}


# ---------------------------------------------------------------------------
# build_reversal — flip Dr/Cr on every line of an existing entry
# ---------------------------------------------------------------------------
async def build_reversal(ctx: AgnesContext) -> dict[str, Any]:
    """Build a reversal entry for the original found by `find-original-entry`.

    Each new line preserves `account_code`, `counterparty_id`,
    `swan_transaction_id`, `document_id`, and `description`, but SWAPS
    `debit_cents` ↔ `credit_cents`. Negative amounts are NOT used (the schema
    CHECK forbids them).
    """
    find = ctx.get("find-original-entry") or {}
    if find.get("skip"):
        return {"skip": True, "reason": find.get("reason")}

    original_id = find.get("original_entry_id")
    if not original_id:
        return {"skip": True, "reason": "no_original_entry_id"}

    # Load original entry's basis.
    cur = await ctx.store.accounting.execute(
        "SELECT basis FROM journal_entries WHERE id = ?",
        (original_id,),
    )
    je_row = await cur.fetchone()
    await cur.close()
    if je_row is None:
        return {"skip": True, "reason": "original_missing"}
    original_basis = je_row[0]

    # Load original lines.
    cur = await ctx.store.accounting.execute(
        "SELECT account_code, debit_cents, credit_cents, counterparty_id, "
        "       swan_transaction_id, document_id, description "
        "FROM journal_lines WHERE entry_id = ?",
        (original_id,),
    )
    rows = await cur.fetchall()
    await cur.close()

    new_lines: list[dict[str, Any]] = []
    for r in rows:
        new_lines.append(_line(
            account_code=r[0],
            debit_cents=int(r[2]),    # was credit
            credit_cents=int(r[1]),   # was debit
            counterparty_id=r[3],
            swan_transaction_id=r[4],
            document_id=r[5],
            description=r[6],
        ))

    return {
        "lines": new_lines,
        "basis": original_basis,
        "entry_date": _today(),
        "description": f"Reversal of entry {original_id}",
        "accrual_link_id": None,
        "reversal_of_id": original_id,
        "confidence": 1.0,
    }


# ---------------------------------------------------------------------------
# mark_reversed — flip the original entry's status to 'reversed'
# ---------------------------------------------------------------------------
async def mark_reversed(ctx: AgnesContext) -> dict[str, Any]:
    """After the reversal entry has been posted via `gl_poster`, flip the
    original's `status` from 'posted' to 'reversed'.

    NOTE: This is the ONLY legitimate UPDATE outside `pipeline_runs.status`
    in the entire codebase. Source: RealMetaPRD §7.4 reversal pattern.
    """
    find = ctx.get("find-original-entry") or {}
    original_id = find.get("original_entry_id")
    if not original_id:
        return {"skipped": True}

    async with write_tx(ctx.store.accounting, ctx.store.accounting_lock) as conn:
        await conn.execute(
            "UPDATE journal_entries SET status = 'reversed' WHERE id = ?",
            (original_id,),
        )

    return {"original_id": int(original_id), "status": "reversed"}
