"""Tests for `backend.orchestration.tools.invariant_checker`.

Coverage:
  - Posting a balanced entry → invariant_checker returns ok=True
  - Tampering a line outside write_tx → invariant_checker raises
  - Skips cleanly when post-entry was not run
"""
from __future__ import annotations

import pytest

from backend.orchestration.context import FingentContext
from backend.orchestration.tools.gl_poster import post
from backend.orchestration.tools.invariant_checker import run as check_invariants


def _ctx(store, *, node_outputs: dict | None = None,
         trigger_payload: dict | None = None) -> FingentContext:
    return FingentContext(
        run_id=99,
        pipeline_name="test-invariants",
        trigger_source="manual",
        trigger_payload=trigger_payload or {"eventId": "evt_inv_001"},
        node_outputs=node_outputs or {},
        store=store,
        employee_id=1,
    )


def _balanced_cash_entry() -> dict:
    return {
        "lines": [
            {
                "account_code": "626100",
                "debit_cents": 5000, "credit_cents": 0,
                "counterparty_id": None, "swan_transaction_id": None,
                "document_id": None, "description": "test",
            },
            {
                "account_code": "512",
                "debit_cents": 0, "credit_cents": 5000,
                "counterparty_id": None, "swan_transaction_id": None,
                "document_id": None, "description": "test",
            },
        ],
        "basis": "cash",
        "entry_date": "2026-04-25",
        "description": "balanced for inv test",
        "accrual_link_id": None,
        "reversal_of_id": None,
        "confidence": 1.0,
    }


async def test_balanced_entry_passes_invariants(store):
    ctx = _ctx(store, node_outputs={"build-cash-entry": _balanced_cash_entry()})
    posted = await post(ctx)

    ctx2 = _ctx(store, node_outputs={"post-entry": posted})
    out = await check_invariants(ctx2)
    assert out["ok"] is True
    assert out["failures"] == []
    assert out["invariants_run"] == 5


async def test_skipped_post_returns_ok(store):
    """If post-entry wasn't run (no entry_id), invariant_checker is a no-op."""
    ctx = _ctx(store, node_outputs={"post-entry": {"status": "skipped"}})
    out = await check_invariants(ctx)
    assert out["ok"] is True
    assert out.get("skipped") is True


async def test_tampered_balance_raises(store):
    """Direct UPDATE outside write_tx that breaks SUM(D)==SUM(C) → raises."""
    ctx = _ctx(store, node_outputs={"build-cash-entry": _balanced_cash_entry()})
    posted = await post(ctx)
    entry_id = posted["entry_id"]

    # Tamper: bump one line's debit by 100 cents. (Direct execute, not via
    # write_tx — we're simulating an out-of-band corruption to prove the
    # invariant fires.)
    cur = await store.accounting.execute(
        "SELECT id FROM journal_lines WHERE entry_id = ? AND debit_cents > 0 LIMIT 1",
        (entry_id,),
    )
    row = await cur.fetchone()
    await cur.close()
    line_id = row[0]

    await store.accounting.execute(
        "UPDATE journal_lines SET debit_cents = debit_cents + 100 WHERE id = ?",
        (line_id,),
    )
    await store.accounting.commit()

    ctx2 = _ctx(store, node_outputs={"post-entry": posted})
    with pytest.raises(ValueError, match="invariant_checker"):
        await check_invariants(ctx2)


async def test_accrual_without_document_fails_invariant_4(store):
    """Accrual entry whose lines carry no document_id → invariant 4 fails."""
    accrual = {
        "lines": [
            {
                "account_code": "626100",
                "debit_cents": 10000, "credit_cents": 0,
                "counterparty_id": None, "swan_transaction_id": None,
                "document_id": None, "description": "naked accrual",
            },
            {
                "account_code": "401",
                "debit_cents": 0, "credit_cents": 10000,
                "counterparty_id": None, "swan_transaction_id": None,
                "document_id": None, "description": "naked accrual",
            },
        ],
        "basis": "accrual",
        "entry_date": "2026-04-25",
        "description": "accrual without document",
        "accrual_link_id": None,
        "reversal_of_id": None,
        "confidence": 1.0,
    }

    ctx = _ctx(store, node_outputs={"build-accrual-entry": accrual})
    posted = await post(ctx)

    ctx2 = _ctx(store, node_outputs={"post-entry": posted})
    with pytest.raises(ValueError, match="document_reachable|invariant_checker"):
        await check_invariants(ctx2)
