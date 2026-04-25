"""Tests for `backend.orchestration.tools.gl_poster`.

Coverage:
  - Balanced entry → INSERT into journal_entries + journal_lines + decision_traces
  - Unbalanced entry raises ValueError before any insert
  - Exactly one decision_trace per line
  - `ledger.entry_posted` event arrives on the dashboard bus after commit
"""
from __future__ import annotations

import asyncio

import pytest

from backend.orchestration import event_bus
from backend.orchestration.context import AgnesContext
from backend.orchestration.tools.gl_poster import post


def _ctx(store, *, node_outputs: dict | None = None) -> AgnesContext:
    return AgnesContext(
        run_id=42,
        pipeline_name="test-poster",
        trigger_source="manual",
        trigger_payload={"eventId": "evt_test_001"},
        node_outputs=node_outputs or {},
        store=store,
        employee_id=1,
    )


def _balanced_cash_entry() -> dict:
    """Build entry with NULL swan_transaction_id so we don't need to seed
    swan_transactions in every test (the FK is enforced)."""
    return {
        "lines": [
            {
                "account_code": "626100",
                "debit_cents": 12000, "credit_cents": 0,
                "counterparty_id": None, "swan_transaction_id": None,
                "document_id": None, "description": "Anthropic API",
            },
            {
                "account_code": "512",
                "debit_cents": 0, "credit_cents": 12000,
                "counterparty_id": None, "swan_transaction_id": None,
                "document_id": None, "description": "Anthropic API",
            },
        ],
        "basis": "cash",
        "entry_date": "2026-04-25",
        "description": "Anthropic Card",
        "accrual_link_id": None,
        "reversal_of_id": None,
        "confidence": 1.0,
    }


async def test_balanced_entry_posts(store):
    out = await post(_ctx(
        store,
        node_outputs={"build-cash-entry": _balanced_cash_entry()},
    ))
    assert out["status"] == "posted"
    assert isinstance(out["entry_id"], int)
    assert out["lines"] == 2
    assert out["total_cents"] == 12000

    # Persisted as expected.
    cur = await store.accounting.execute(
        "SELECT basis, entry_date, source_pipeline, source_run_id, status "
        "FROM journal_entries WHERE id = ?",
        (out["entry_id"],),
    )
    row = await cur.fetchone()
    await cur.close()
    assert row[0] == "cash"
    assert row[1] == "2026-04-25"
    assert row[2] == "test-poster"
    assert row[3] == 42
    assert row[4] == "posted"


async def test_unbalanced_entry_raises(store):
    bad = _balanced_cash_entry()
    bad["lines"][0]["debit_cents"] = 13000  # 13000 vs 12000 → unbalanced

    with pytest.raises(ValueError, match="unbalanced"):
        await post(_ctx(store, node_outputs={"build-cash-entry": bad}))

    # And nothing was persisted (write_tx never opened because we raised first).
    cur = await store.accounting.execute("SELECT COUNT(*) FROM journal_entries")
    row = await cur.fetchone()
    await cur.close()
    assert row[0] == 0


async def test_decision_trace_per_line(store):
    out = await post(_ctx(
        store,
        node_outputs={"build-cash-entry": _balanced_cash_entry()},
    ))
    cur = await store.accounting.execute(
        "SELECT COUNT(*) FROM decision_traces dt "
        "JOIN journal_lines jl ON dt.line_id = jl.id "
        "WHERE jl.entry_id = ?",
        (out["entry_id"],),
    )
    row = await cur.fetchone()
    await cur.close()
    assert row[0] == 2  # one per line


async def test_decision_trace_attribution(store):
    """parent_event_id propagates from trigger_payload.eventId."""
    out = await post(_ctx(
        store,
        node_outputs={"build-cash-entry": _balanced_cash_entry()},
    ))
    cur = await store.accounting.execute(
        "SELECT source, parent_event_id FROM decision_traces dt "
        "JOIN journal_lines jl ON dt.line_id = jl.id "
        "WHERE jl.entry_id = ? LIMIT 1",
        (out["entry_id"],),
    )
    row = await cur.fetchone()
    await cur.close()
    assert row[0] == "rule"  # no AgentResult in node_outputs
    assert row[1] == "evt_test_001"


async def test_dashboard_event_emitted(store):
    """ledger.entry_posted lands on the dashboard bus."""
    q = await event_bus.subscribe_dashboard()
    try:
        out = await post(_ctx(
            store,
            node_outputs={"build-cash-entry": _balanced_cash_entry()},
        ))
        evt = await asyncio.wait_for(q.get(), timeout=1.0)
        assert evt["event_type"] == "ledger.entry_posted"
        assert evt["data"]["entry_id"] == out["entry_id"]
        assert evt["data"]["basis"] == "cash"
        assert evt["data"]["total_cents"] == 12000
        assert evt["data"]["lines"] == 2
        assert evt["data"]["run_id"] == 42
        assert evt["data"]["employee_id"] == 1
    finally:
        await event_bus.remove_dashboard_subscriber(q)


async def test_skips_when_no_built_entry(store):
    out = await post(_ctx(store, node_outputs={}))
    assert out["status"] == "skipped"


async def test_skips_when_built_says_skip(store):
    out = await post(_ctx(
        store,
        node_outputs={"build-reversal": {"skip": True, "reason": "no_original"}},
    ))
    assert out["status"] == "skipped"
    assert out["reason"] == "no_original"
