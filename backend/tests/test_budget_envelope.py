"""Tests for `tools.budget_envelope:decrement`.

Cases covered:
  - happy path: expense entry decrements the employee envelope and emits
    `envelope.decremented` on the dashboard bus.
  - uncategorized: counterparty.envelope_category=None => skipped + emits
    `envelope.skipped`.
  - employee→company fallback: only company-scope envelope exists.
  - reversal: entry with `reversal_of_id` set => negative allocation.
"""
from __future__ import annotations

import asyncio

import pytest

from backend.orchestration import event_bus
from backend.orchestration.context import FingentContext
from backend.orchestration.store.writes import write_tx
from backend.orchestration.tools import budget_envelope


pytestmark = pytest.mark.asyncio


# ---- helpers ----------------------------------------------------------------

async def _insert_entry(
    store,
    *,
    entry_date: str,
    expense_account: str = "626100",
    expense_cents: int = 12_000,
    bank_cents: int | None = None,
    reversal_of_id: int | None = None,
    counterparty_id: int | None = None,
) -> tuple[int, int]:
    """Insert a balanced journal entry; return (entry_id, expense_line_id).

    For a normal forward entry: Dr expense / Cr bank.
    For a reversal:             Cr expense / Dr bank (Dr/Cr swapped).
    """
    if bank_cents is None:
        bank_cents = expense_cents

    async with write_tx(store.accounting, store.accounting_lock) as conn:
        cur = await conn.execute(
            "INSERT INTO journal_entries "
            "(basis, entry_date, description, source_pipeline, source_run_id, "
            " status, reversal_of_id) "
            "VALUES ('cash', ?, 'test', 'test_pipeline', 0, 'posted', ?)",
            (entry_date, reversal_of_id),
        )
        entry_id = cur.lastrowid
        await cur.close()

        if reversal_of_id is None:
            # Forward: Dr expense, Cr bank
            cur = await conn.execute(
                "INSERT INTO journal_lines "
                "(entry_id, account_code, debit_cents, credit_cents, counterparty_id) "
                "VALUES (?, ?, ?, 0, ?)",
                (entry_id, expense_account, expense_cents, counterparty_id),
            )
            expense_line_id = cur.lastrowid
            await cur.close()

            await conn.execute(
                "INSERT INTO journal_lines "
                "(entry_id, account_code, debit_cents, credit_cents) "
                "VALUES (?, '512', 0, ?)",
                (entry_id, bank_cents),
            )
        else:
            # Reversal: Cr expense, Dr bank
            cur = await conn.execute(
                "INSERT INTO journal_lines "
                "(entry_id, account_code, debit_cents, credit_cents, counterparty_id) "
                "VALUES (?, ?, 0, ?, ?)",
                (entry_id, expense_account, expense_cents, counterparty_id),
            )
            expense_line_id = cur.lastrowid
            await cur.close()

            await conn.execute(
                "INSERT INTO journal_lines "
                "(entry_id, account_code, debit_cents, credit_cents) "
                "VALUES (?, '512', ?, 0)",
                (entry_id, bank_cents),
            )

    return int(entry_id), int(expense_line_id)


def _ctx_for(
    store,
    *,
    entry_id: int,
    envelope_category: str | None = "ai_tokens",
    employee_id: int | None = 1,
) -> FingentContext:
    cp_node: dict = {"counterparty_id": 1, "envelope_category": envelope_category}
    return FingentContext(
        run_id=1,
        pipeline_name="transaction_booked",
        trigger_source="external_event:swan.Transaction.Booked",
        trigger_payload={},
        node_outputs={
            "post-entry": {"status": "posted", "entry_id": entry_id},
            "resolve-counterparty": cp_node,
        },
        store=store,
        employee_id=employee_id,
    )


async def _seed_employee_envelope(
    store, *, employee_id: int, category: str, period: str, cap_cents: int = 200_00
) -> int:
    async with write_tx(store.accounting, store.accounting_lock) as conn:
        cur = await conn.execute(
            "INSERT INTO budget_envelopes "
            "(scope_kind, scope_id, category, period, cap_cents, soft_threshold_pct) "
            "VALUES ('employee', ?, ?, ?, ?, 80)",
            (employee_id, category, period, cap_cents),
        )
        env_id = cur.lastrowid
        await cur.close()
    return int(env_id)


async def _seed_company_envelope(
    store, *, category: str, period: str, cap_cents: int = 500_00
) -> int:
    async with write_tx(store.accounting, store.accounting_lock) as conn:
        cur = await conn.execute(
            "INSERT INTO budget_envelopes "
            "(scope_kind, scope_id, category, period, cap_cents, soft_threshold_pct) "
            "VALUES ('company', NULL, ?, ?, ?, 80)",
            (category, period, cap_cents),
        )
        env_id = cur.lastrowid
        await cur.close()
    return int(env_id)


# ---- tests ------------------------------------------------------------------

async def test_decrement_happy_path_employee_envelope(store):
    """Forward expense entry hits the employee envelope; dashboard event emitted."""
    # Seed migration 0007 already created envelopes for employee 1, category
    # ai_tokens, period 2026-04. Use that.
    entry_id, line_id = await _insert_entry(
        store, entry_date="2026-04-15", expense_cents=12_000, counterparty_id=1
    )

    q = await event_bus.subscribe_dashboard()
    try:
        ctx = _ctx_for(store, entry_id=entry_id, envelope_category="ai_tokens",
                       employee_id=1)
        result = await budget_envelope.decrement(ctx)

        assert result.get("envelope_id") is not None
        assert result["used_cents"] == 12_000
        assert result["cap_cents"] > 0
        assert len(result["allocations"]) == 1
        assert result["allocations"][0]["amount_cents"] == 12_000

        # DB state matches.
        cur = await store.accounting.execute(
            "SELECT envelope_id, line_id, amount_cents FROM budget_allocations"
        )
        rows = list(await cur.fetchall())
        await cur.close()
        assert len(rows) == 1
        assert rows[0][0] == result["envelope_id"]
        assert rows[0][1] == line_id
        assert rows[0][2] == 12_000

        # Dashboard event arrived.
        evt = await asyncio.wait_for(q.get(), timeout=2.0)
        assert evt["event_type"] == "envelope.decremented"
        data = evt["data"]
        assert data["envelope_id"] == result["envelope_id"]
        assert data["used_cents"] == 12_000
        assert data["category"] == "ai_tokens"
        assert data["period"] == "2026-04"
        assert data["ledger_entry_id"] == entry_id
        assert data["employee_id"] == 1
    finally:
        await event_bus.remove_dashboard_subscriber(q)


async def test_decrement_uncategorized_skips_and_emits_skipped(store):
    """envelope_category=None => returns skipped; emits envelope.skipped."""
    entry_id, _ = await _insert_entry(
        store, entry_date="2026-04-15", expense_cents=5_000
    )

    q = await event_bus.subscribe_dashboard()
    try:
        ctx = _ctx_for(store, entry_id=entry_id, envelope_category=None)
        result = await budget_envelope.decrement(ctx)

        assert result == {"skipped": True, "reason": "uncategorized"}

        evt = await asyncio.wait_for(q.get(), timeout=2.0)
        assert evt["event_type"] == "envelope.skipped"
        assert evt["data"]["reason"] == "uncategorized"
        assert evt["data"]["entry_id"] == entry_id

        # No allocation was written.
        cur = await store.accounting.execute(
            "SELECT COUNT(*) FROM budget_allocations"
        )
        row = await cur.fetchone()
        await cur.close()
        assert row[0] == 0
    finally:
        await event_bus.remove_dashboard_subscriber(q)


async def test_decrement_employee_to_company_fallback(store):
    """No employee envelope for this category/period — falls back to company."""
    # Use an obscure category + period not covered by the seed.
    period = "2099-12"
    category = "phase2_test_category"

    company_env_id = await _seed_company_envelope(
        store, category=category, period=period, cap_cents=99_999_00
    )

    entry_id, _ = await _insert_entry(
        store, entry_date=f"{period}-01", expense_cents=4_200
    )

    ctx = _ctx_for(store, entry_id=entry_id, envelope_category=category,
                   employee_id=1)
    result = await budget_envelope.decrement(ctx)

    assert result["envelope_id"] == company_env_id
    assert result["used_cents"] == 4_200


async def test_decrement_reversal_allocates_negative(store):
    """Reversal entries flow through the envelope as negative allocations."""
    # Forward entry first.
    fwd_id, _ = await _insert_entry(
        store, entry_date="2026-04-10", expense_cents=10_000, counterparty_id=1
    )
    # Reversal pointing back at the forward entry.
    rev_id, _ = await _insert_entry(
        store, entry_date="2026-04-12", expense_cents=10_000,
        counterparty_id=1, reversal_of_id=fwd_id,
    )

    # Decrement the forward first.
    ctx_fwd = _ctx_for(store, entry_id=fwd_id, envelope_category="ai_tokens",
                       employee_id=1)
    fwd_result = await budget_envelope.decrement(ctx_fwd)
    assert fwd_result["used_cents"] == 10_000

    # Then the reversal — used_cents must net back to 0.
    ctx_rev = _ctx_for(store, entry_id=rev_id, envelope_category="ai_tokens",
                       employee_id=1)
    rev_result = await budget_envelope.decrement(ctx_rev)

    assert rev_result["envelope_id"] == fwd_result["envelope_id"]
    assert rev_result["allocations"][0]["amount_cents"] == -10_000
    assert rev_result["used_cents"] == 0
