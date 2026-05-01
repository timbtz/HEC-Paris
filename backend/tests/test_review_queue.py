"""Tests for `tools.review_queue:enqueue`.

Asserts:
  - inserts a row keyed on the failed gate's `kind`.
  - emits `review.enqueued` on the dashboard bus.
  - tolerates a missing `entry_id` (extraction-stage failures have no entry yet).
"""
from __future__ import annotations

import asyncio

import pytest

from backend.orchestration import event_bus
from backend.orchestration.context import FingentContext
from backend.orchestration.tools import review_queue


pytestmark = pytest.mark.asyncio


def _ctx(store, node_outputs: dict) -> FingentContext:
    return FingentContext(
        run_id=1,
        pipeline_name="transaction_booked",
        trigger_source="external_event:swan.Transaction.Booked",
        trigger_payload={},
        node_outputs=node_outputs,
        store=store,
        employee_id=1,
    )


async def test_enqueue_low_confidence_inserts_and_emits(store):
    q = await event_bus.subscribe_dashboard()
    try:
        ctx = _ctx(store, {
            "post-entry": {"status": "posted", "entry_id": None},
            "gate-confidence": {
                "ok": False, "computed_confidence": 0.1, "floor": 0.5
            },
        })
        result = await review_queue.enqueue(ctx)

        assert result["kind"] == "low_confidence"
        assert isinstance(result["review_id"], int) and result["review_id"] > 0

        cur = await store.accounting.execute(
            "SELECT id, entry_id, kind, confidence, reason FROM review_queue"
        )
        rows = list(await cur.fetchall())
        await cur.close()
        assert len(rows) == 1
        assert rows[0][0] == result["review_id"]
        assert rows[0][2] == "low_confidence"
        assert rows[0][3] == 0.1

        evt = await asyncio.wait_for(q.get(), timeout=2.0)
        assert evt["event_type"] == "review.enqueued"
        assert evt["data"]["review_id"] == result["review_id"]
        assert evt["data"]["kind"] == "low_confidence"
        assert evt["data"]["confidence"] == 0.1
    finally:
        await event_bus.remove_dashboard_subscriber(q)


async def test_enqueue_totals_mismatch_path(store):
    """`validate.ok=False` with no failed gate -> kind=totals_mismatch."""
    ctx = _ctx(store, {
        "validate": {"ok": False, "failures": ["subtotal+vat != total"]},
    })
    result = await review_queue.enqueue(ctx)

    assert result["kind"] == "totals_mismatch"

    cur = await store.accounting.execute(
        "SELECT kind FROM review_queue WHERE id = ?", (result["review_id"],),
    )
    row = await cur.fetchone()
    await cur.close()
    assert row[0] == "totals_mismatch"


async def test_enqueue_manual_default(store):
    """No upstream signal -> falls through to 'manual'."""
    ctx = _ctx(store, {})
    result = await review_queue.enqueue(ctx)
    assert result["kind"] == "manual"


async def test_enqueue_with_entry_id(store):
    """Happy path with an actual entry_id stored on the row."""
    # Insert a real journal_entries row so the FK from review_queue resolves.
    from backend.orchestration.store.writes import write_tx
    async with write_tx(store.accounting, store.accounting_lock) as conn:
        cur = await conn.execute(
            "INSERT INTO journal_entries "
            "(basis, entry_date, description, source_pipeline, source_run_id, status) "
            "VALUES ('cash', '2026-04-15', 'test', 'test_pipe', 0, 'posted')"
        )
        entry_id = int(cur.lastrowid)
        await cur.close()

    ctx = _ctx(store, {
        "post-entry": {"status": "posted", "entry_id": entry_id},
        "gate-confidence": {
            "ok": False, "computed_confidence": 0.2, "floor": 0.5
        },
    })
    result = await review_queue.enqueue(ctx)

    cur = await store.accounting.execute(
        "SELECT entry_id FROM review_queue WHERE id = ?", (result["review_id"],),
    )
    row = await cur.fetchone()
    await cur.close()
    assert row[0] == entry_id
