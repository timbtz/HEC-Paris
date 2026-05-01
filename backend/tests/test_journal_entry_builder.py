"""Tests for `backend.orchestration.tools.journal_entry_builder`.

Coverage:
  - Card spend (with + without VAT) → balanced expense / bank lines
  - SEPA-in to a customer → AR + Bank
  - SEPA-out matched against an existing accrual → AP + Bank
  - SEPA-out unmatched → Expense + Bank
  - build_reversal flips Dr ↔ Cr per line
  - mark_reversed flips status to 'reversed'
"""
from __future__ import annotations

from backend.orchestration.context import FingentContext
from backend.orchestration.tools.journal_entry_builder import (
    build_accrual,
    build_cash,
    build_reversal,
    find_original,
    match_accrual,
    mark_reversed,
)


def _ctx(store, *, node_outputs: dict | None = None,
         trigger_payload: dict | None = None) -> FingentContext:
    return FingentContext(
        run_id=1,
        pipeline_name="test-builder",
        trigger_source="manual",
        trigger_payload=trigger_payload or {},
        node_outputs=node_outputs or {},
        store=store,
    )


# ---------------------------------------------------------------------------
# build_cash — card spend
# ---------------------------------------------------------------------------
async def test_card_spend_no_vat_balanced(store):
    out = await build_cash(_ctx(
        store,
        node_outputs={
            "fetch-transaction": {
                "id": "swan_tx_1",
                "type": "CardOutDebit",
                "side": "Debit",
                "amount_cents": 12000,
                "execution_date": "2026-04-25",
            },
            "resolve-counterparty": {
                "counterparty_id": 7, "counterparty_legal_name": "Anthropic",
                "kind": "supplier",
            },
            "classify-gl-account": {"gl_account": "626100", "confidence": 1.0},
        },
    ))
    assert out["basis"] == "cash"
    assert out["entry_date"] == "2026-04-25"
    debits = sum(line["debit_cents"] for line in out["lines"])
    credits = sum(line["credit_cents"] for line in out["lines"])
    assert debits == credits == 12000
    accounts = sorted((line["account_code"], line["debit_cents"], line["credit_cents"])
                      for line in out["lines"])
    assert ("512", 0, 12000) in accounts
    assert ("626100", 12000, 0) in accounts


async def test_card_spend_with_vat_split(store):
    """VAT split per spec formula: vat = (amount * rate_bp + 5000) // 10000.

    For amount=12000, rate=2000bp → vat=2400, subtotal=9600.
    Three lines must sum balanced: Dr 9600 + Dr 2400 + Cr 12000.
    """
    out = await build_cash(_ctx(
        store,
        node_outputs={
            "fetch-transaction": {
                "id": "swan_tx_2",
                "type": "CardOutDebit",
                "side": "Debit",
                "amount_cents": 12000,
                "execution_date": "2026-04-25",
            },
            "resolve-counterparty": {
                "counterparty_id": 7, "counterparty_legal_name": "Anthropic",
                "kind": "supplier",
            },
            "classify-gl-account": {
                "gl_account": "626100", "confidence": 1.0,
                "vat_rate_bp": 2000,
            },
        },
    ))
    debits = sum(line["debit_cents"] for line in out["lines"])
    credits = sum(line["credit_cents"] for line in out["lines"])
    assert debits == credits == 12000
    by_account = {line["account_code"]: line for line in out["lines"]}
    assert by_account["626100"]["debit_cents"] == 9600
    assert by_account["4456"]["debit_cents"] == 2400
    assert by_account["512"]["credit_cents"] == 12000


# ---------------------------------------------------------------------------
# build_cash — SEPA in
# ---------------------------------------------------------------------------
async def test_sepa_in_to_customer_routes_to_ar(store):
    out = await build_cash(_ctx(
        store,
        node_outputs={
            "fetch-transaction": {
                "id": "swan_tx_in_1",
                "type": "SepaCreditTransferIn",
                "side": "Credit",
                "amount_cents": 50000,
                "execution_date": "2026-04-20",
            },
            "resolve-counterparty": {
                "counterparty_id": 33, "counterparty_legal_name": "Acme SAS",
                "kind": "customer",
            },
        },
    ))
    debits = sum(line["debit_cents"] for line in out["lines"])
    credits = sum(line["credit_cents"] for line in out["lines"])
    assert debits == credits == 50000
    accounts = {line["account_code"]: line for line in out["lines"]}
    assert accounts["512"]["debit_cents"] == 50000
    assert accounts["411"]["credit_cents"] == 50000


async def test_sepa_in_no_customer_routes_to_revenue(store):
    out = await build_cash(_ctx(
        store,
        node_outputs={
            "fetch-transaction": {
                "id": "swan_tx_in_2",
                "type": "SepaCreditTransferIn",
                "side": "Credit",
                "amount_cents": 25000,
                "execution_date": "2026-04-20",
            },
            "resolve-counterparty": {
                "counterparty_id": None, "counterparty_legal_name": None,
                "kind": None,
            },
        },
    ))
    accounts = {line["account_code"]: line for line in out["lines"]}
    assert accounts["706000"]["credit_cents"] == 25000


# ---------------------------------------------------------------------------
# build_cash — SEPA out
# ---------------------------------------------------------------------------
async def test_sepa_out_unmatched_uses_expense(store):
    out = await build_cash(_ctx(
        store,
        node_outputs={
            "fetch-transaction": {
                "id": "swan_tx_out_1",
                "type": "SepaCreditTransferOut",
                "side": "Debit",
                "amount_cents": 8000,
                "execution_date": "2026-04-21",
            },
            "resolve-counterparty": {
                "counterparty_id": 7, "counterparty_legal_name": "Anthropic",
                "kind": "supplier",
            },
            "classify-gl-account": {"gl_account": "626100", "confidence": 1.0},
            "match-accrual": {},  # explicit miss
        },
    ))
    debits = sum(line["debit_cents"] for line in out["lines"])
    credits = sum(line["credit_cents"] for line in out["lines"])
    assert debits == credits == 8000
    accounts = {line["account_code"]: line for line in out["lines"]}
    assert accounts["626100"]["debit_cents"] == 8000  # Expense, not AP
    assert accounts["512"]["credit_cents"] == 8000


async def test_sepa_out_matched_uses_ap(store):
    out = await build_cash(_ctx(
        store,
        node_outputs={
            "fetch-transaction": {
                "id": "swan_tx_out_2",
                "type": "SepaCreditTransferOut",
                "side": "Debit",
                "amount_cents": 8000,
                "execution_date": "2026-04-21",
            },
            "resolve-counterparty": {
                "counterparty_id": 7, "counterparty_legal_name": "Anthropic",
                "kind": "supplier",
            },
            "classify-gl-account": {"gl_account": "626100", "confidence": 1.0},
            "match-accrual": {"accrual_link_id": 99},
        },
    ))
    accounts = {line["account_code"]: line for line in out["lines"]}
    # AP-side debit (the matched accrual unwinds), not Expense.
    assert "401" in accounts
    assert accounts["401"]["debit_cents"] == 8000
    assert "626100" not in accounts  # Expense was already booked at accrual time
    assert accounts["512"]["credit_cents"] == 8000
    assert out["accrual_link_id"] == 99


# ---------------------------------------------------------------------------
# build_accrual
# ---------------------------------------------------------------------------
async def test_build_accrual_with_vat(store):
    out = await build_accrual(_ctx(
        store,
        node_outputs={
            "extract": {
                "subtotal_cents": 10000,
                "vat_cents": 2000,
                "total_cents": 12000,
                "date": "2026-04-15",
            },
            "resolve-counterparty": {
                "counterparty_id": 7, "counterparty_legal_name": "Anthropic",
                "kind": "supplier",
            },
            "classify-gl-account": {"gl_account": "626100", "confidence": 1.0},
        },
        trigger_payload={"document_id": 42},
    ))
    assert out["basis"] == "accrual"
    assert out["entry_date"] == "2026-04-15"
    debits = sum(line["debit_cents"] for line in out["lines"])
    credits = sum(line["credit_cents"] for line in out["lines"])
    assert debits == credits == 12000
    accounts = {line["account_code"]: line for line in out["lines"]}
    assert accounts["626100"]["debit_cents"] == 10000
    assert accounts["4456"]["debit_cents"] == 2000
    assert accounts["401"]["credit_cents"] == 12000
    # Document reachability: every line carries the document_id.
    for line in out["lines"]:
        assert line["document_id"] == 42


# ---------------------------------------------------------------------------
# match_accrual
# ---------------------------------------------------------------------------
async def test_match_accrual_skips_non_sepa_out(store):
    out = await match_accrual(_ctx(
        store,
        node_outputs={
            "fetch-transaction": {"type": "CardOutDebit", "side": "Debit"},
            "resolve-counterparty": {"counterparty_id": 1},
        },
    ))
    assert out == {}


async def test_match_accrual_finds_existing_accrual(store):
    """Post an accrual via direct INSERT, then match it."""
    # Insert an accrual entry + line via gl_poster equivalent. We use the
    # bare connection here for test setup only; production code goes through
    # gl_poster (the chokepoint).
    from backend.orchestration.store.writes import write_tx
    async with write_tx(store.accounting, store.accounting_lock) as conn:
        cur = await conn.execute(
            "INSERT INTO journal_entries "
            "(basis, entry_date, description, source_pipeline, source_run_id, status) "
            "VALUES ('accrual', '2026-04-15', 'test accrual', 'test', 1, 'posted')"
        )
        accrual_id = cur.lastrowid
        await conn.execute(
            "INSERT INTO journal_lines "
            "(entry_id, account_code, debit_cents, credit_cents, counterparty_id) "
            "VALUES (?, '626100', 10000, 0, 7)",
            (accrual_id,),
        )
        await conn.execute(
            "INSERT INTO journal_lines "
            "(entry_id, account_code, debit_cents, credit_cents, counterparty_id) "
            "VALUES (?, '401', 0, 10000, 7)",
            (accrual_id,),
        )

    out = await match_accrual(_ctx(
        store,
        node_outputs={
            "fetch-transaction": {
                "id": "swan_tx_pay_1",
                "type": "SepaCreditTransferOut",
                "side": "Debit",
                "amount_cents": 10000,
            },
            "resolve-counterparty": {"counterparty_id": 7, "kind": "supplier"},
        },
    ))
    assert out == {"accrual_link_id": accrual_id}


# ---------------------------------------------------------------------------
# build_reversal — flips Dr/Cr per line
# ---------------------------------------------------------------------------
async def test_build_reversal_flips_debit_credit(store):
    """Insert an entry, then build a reversal of it; verify Dr/Cr swap."""
    from backend.orchestration.store.writes import write_tx
    async with write_tx(store.accounting, store.accounting_lock) as conn:
        # Stage a swan_transactions row so the FK on journal_lines holds.
        await conn.execute(
            "INSERT INTO swan_transactions "
            "(id, swan_event_id, side, type, status, amount_cents, currency, "
            " execution_date, raw) "
            "VALUES ('swan_tx_orig', 'evt_orig', 'Debit', 'CardOutDebit', 'Booked', "
            "         8000, 'EUR', '2026-04-22', '{}')"
        )
        cur = await conn.execute(
            "INSERT INTO journal_entries "
            "(basis, entry_date, description, source_pipeline, source_run_id, status) "
            "VALUES ('cash', '2026-04-22', 'orig', 'test', 1, 'posted')"
        )
        orig_id = cur.lastrowid
        # Original: Dr 626100 8000 / Cr 512 8000
        await conn.execute(
            "INSERT INTO journal_lines "
            "(entry_id, account_code, debit_cents, credit_cents, swan_transaction_id) "
            "VALUES (?, '626100', 8000, 0, 'swan_tx_orig')",
            (orig_id,),
        )
        await conn.execute(
            "INSERT INTO journal_lines "
            "(entry_id, account_code, debit_cents, credit_cents, swan_transaction_id) "
            "VALUES (?, '512', 0, 8000, 'swan_tx_orig')",
            (orig_id,),
        )

    out = await build_reversal(_ctx(
        store,
        node_outputs={
            "find-original-entry": {"original_entry_id": orig_id, "basis": "cash"},
        },
    ))
    assert out["basis"] == "cash"
    assert out["reversal_of_id"] == orig_id
    assert len(out["lines"]) == 2
    by_account = {line["account_code"]: line for line in out["lines"]}
    # 626100 was Dr in original, must be Cr in reversal
    assert by_account["626100"]["credit_cents"] == 8000
    assert by_account["626100"]["debit_cents"] == 0
    # 512 was Cr in original, must be Dr in reversal
    assert by_account["512"]["debit_cents"] == 8000
    assert by_account["512"]["credit_cents"] == 0
    # Identity preserved
    assert by_account["626100"]["swan_transaction_id"] == "swan_tx_orig"


async def test_build_reversal_skips_when_find_skips(store):
    out = await build_reversal(_ctx(
        store,
        node_outputs={
            "find-original-entry": {"skip": True, "reason": "no_original"},
        },
    ))
    assert out == {"skip": True, "reason": "no_original"}


# ---------------------------------------------------------------------------
# find_original
# ---------------------------------------------------------------------------
async def test_find_original_returns_no_original_when_missing(store):
    out = await find_original(_ctx(
        store,
        trigger_payload={"resourceId": "nonexistent_swan_tx"},
    ))
    assert out == {"skip": True, "reason": "no_original"}


async def test_find_original_returns_already_reversed(store):
    """An already-reversed entry must short-circuit."""
    from backend.orchestration.store.writes import write_tx
    async with write_tx(store.accounting, store.accounting_lock) as conn:
        await conn.execute(
            "INSERT INTO swan_transactions "
            "(id, swan_event_id, side, type, status, amount_cents, currency, "
            " execution_date, raw) "
            "VALUES ('swan_tx_already_rev', 'evt_ar', 'Debit', 'CardOutDebit', "
            "         'Booked', 100, 'EUR', '2026-04-22', '{}')"
        )
        cur = await conn.execute(
            "INSERT INTO journal_entries "
            "(basis, entry_date, description, source_pipeline, source_run_id, status) "
            "VALUES ('cash', '2026-04-22', 'orig', 'test', 1, 'reversed')"
        )
        orig_id = cur.lastrowid
        await conn.execute(
            "INSERT INTO journal_lines "
            "(entry_id, account_code, debit_cents, credit_cents, swan_transaction_id) "
            "VALUES (?, '512', 0, 100, 'swan_tx_already_rev')",
            (orig_id,),
        )

    out = await find_original(_ctx(
        store,
        trigger_payload={"resourceId": "swan_tx_already_rev"},
    ))
    assert out["skip"] is True
    assert out["reason"] == "already_reversed"
    assert out["original_entry_id"] == orig_id


# ---------------------------------------------------------------------------
# mark_reversed
# ---------------------------------------------------------------------------
async def test_mark_reversed_flips_status(store):
    from backend.orchestration.store.writes import write_tx
    async with write_tx(store.accounting, store.accounting_lock) as conn:
        cur = await conn.execute(
            "INSERT INTO journal_entries "
            "(basis, entry_date, description, source_pipeline, source_run_id, status) "
            "VALUES ('cash', '2026-04-22', 'orig', 'test', 1, 'posted')"
        )
        orig_id = cur.lastrowid

    out = await mark_reversed(_ctx(
        store,
        node_outputs={"find-original-entry": {"original_entry_id": orig_id}},
    ))
    assert out == {"original_id": orig_id, "status": "reversed"}

    cur = await store.accounting.execute(
        "SELECT status FROM journal_entries WHERE id = ?",
        (orig_id,),
    )
    row = await cur.fetchone()
    await cur.close()
    assert row[0] == "reversed"
