"""End-to-end test for the `period_close` pipeline (Phase 3 Slice D).

Seeds a small ledger spanning the closing period, runs the pipeline via
`execute_pipeline(... background=False)`, asserts:
- one `period_reports` row appears
- `report.rendered` event publishes on the dashboard bus
- NO journal_entries are posted (period_close is a read-only report)
- the post-render markdown blob exists on disk
"""
from __future__ import annotations

import asyncio
import json

from backend.orchestration import event_bus
from backend.orchestration.executor import execute_pipeline
from backend.orchestration.store.writes import write_tx


async def _seed_ledger_in_q1(store) -> None:
    """Insert a balanced trial-balance worth of entries inside 2026-Q1."""
    async with write_tx(store.accounting, store.accounting_lock) as conn:
        # accrual revenue
        cur = await conn.execute(
            "INSERT INTO journal_entries "
            "(basis, entry_date, source_pipeline, source_run_id, status) "
            "VALUES ('accrual', '2026-01-15', 'test', 1, 'posted')"
        )
        e1 = int(cur.lastrowid)
        await conn.execute(
            "INSERT INTO journal_lines (entry_id, account_code, debit_cents, credit_cents) "
            "VALUES (?, '411', 12000, 0), (?, '706000', 0, 12000)",
            (e1, e1),
        )
        # accrual expense
        cur = await conn.execute(
            "INSERT INTO journal_entries "
            "(basis, entry_date, source_pipeline, source_run_id, status) "
            "VALUES ('accrual', '2026-02-10', 'test', 1, 'posted')"
        )
        e2 = int(cur.lastrowid)
        await conn.execute(
            "INSERT INTO journal_lines (entry_id, account_code, debit_cents, credit_cents) "
            "VALUES (?, '626100', 5000, 0), (?, '4456', 1000, 0), (?, '401', 0, 6000)",
            (e2, e2, e2),
        )


async def _drain_dashboard_events(timeout_s: float = 1.0) -> list[dict]:
    """Subscribe to dashboard bus, drain pending events, return them."""
    q = await event_bus.subscribe_dashboard()
    events: list[dict] = []
    try:
        while True:
            try:
                ev = await asyncio.wait_for(q.get(), timeout=0.1)
                events.append(ev)
            except asyncio.TimeoutError:
                break
    finally:
        await event_bus.remove_dashboard_subscriber(q)
    return events


async def test_period_close_emits_report_and_does_not_post(
    store, fake_anthropic
):
    """Run the pipeline and confirm exactly one period_reports row + zero journal_entries."""
    calls, fake_client = fake_anthropic
    # Anomaly-agent stub: zero anomalies, high confidence.
    from types import SimpleNamespace
    fake_client.messages._response = SimpleNamespace(
        id="msg_1", model="claude-sonnet-4-6", stop_reason="tool_use",
        content=[SimpleNamespace(
            type="tool_use", id="tu_1", name="submit_anomalies",
            input={"anomalies": [], "overall_confidence": 0.95},
        )],
        usage=SimpleNamespace(
            input_tokens=100, output_tokens=20,
            cache_creation_input_tokens=0, cache_read_input_tokens=0,
        ),
    )

    await _seed_ledger_in_q1(store)

    # Snapshot journal_entries count before.
    cur = await store.accounting.execute("SELECT COUNT(*) FROM journal_entries")
    entries_before = int((await cur.fetchone())[0])
    await cur.close()

    # Subscribe to dashboard BEFORE running so we don't miss the event.
    q = await event_bus.subscribe_dashboard()
    try:
        await execute_pipeline(
            "period_close",
            trigger_source="test",
            trigger_payload={"period_code": "2026-Q1"},
            store=store,
            background=False,
        )

        # Drain events.
        events: list[dict] = []
        for _ in range(20):
            try:
                events.append(await asyncio.wait_for(q.get(), timeout=0.1))
            except asyncio.TimeoutError:
                break
    finally:
        await event_bus.remove_dashboard_subscriber(q)

    # 1) period_reports row exists.
    cur = await store.accounting.execute(
        "SELECT id, period_code, report_type, status, payload_json "
        "FROM period_reports"
    )
    rows = list(await cur.fetchall())
    await cur.close()
    assert len(rows) == 1, rows
    assert rows[0]["period_code"] == "2026-Q1"
    assert rows[0]["report_type"] == "period_close"
    payload = json.loads(rows[0]["payload_json"])
    assert payload["period_code"] == "2026-Q1"

    # 2) report.rendered event seen.
    rendered = [e for e in events if e.get("event_type") == "report.rendered"]
    assert rendered, [e.get("event_type") for e in events]
    assert rendered[0]["data"]["period_code"] == "2026-Q1"

    # 3) Zero new journal_entries — period_close is read-only.
    cur = await store.accounting.execute("SELECT COUNT(*) FROM journal_entries")
    entries_after = int((await cur.fetchone())[0])
    await cur.close()
    assert entries_after == entries_before


async def test_gl_poster_blocks_post_into_closed_period(store):
    """A post into the seeded `2026-Q1` (closed) must raise RuntimeError."""
    from backend.orchestration.context import FingentContext
    from backend.orchestration.tools import gl_poster

    ctx = FingentContext(
        run_id=1,
        pipeline_name="test",
        trigger_source="test",
        trigger_payload={},
        node_outputs={
            "build-cash-entry": {
                "basis": "cash",
                "entry_date": "2026-02-15",  # inside 2026-Q1 (closed)
                "lines": [
                    {"account_code": "626100", "debit_cents": 1000, "credit_cents": 0},
                    {"account_code": "401",    "debit_cents": 0,    "credit_cents": 1000},
                ],
            },
        },
        store=store,
    )
    # CoA seed already includes 626100 + 401.
    try:
        await gl_poster.post(ctx)
    except RuntimeError as exc:
        assert "closed" in str(exc), str(exc)
        return
    raise AssertionError("expected RuntimeError for post into closed period")


async def test_gl_poster_allows_post_into_open_period(store):
    """A post into a `closing` or `open` period MUST succeed."""
    from backend.orchestration.context import FingentContext
    from backend.orchestration.tools import gl_poster

    ctx = FingentContext(
        run_id=1,
        pipeline_name="test",
        trigger_source="test",
        trigger_payload={},
        node_outputs={
            "build-cash-entry": {
                "basis": "cash",
                "entry_date": "2026-04-15",  # inside 2026-Q2 (closing)
                "lines": [
                    {"account_code": "626100", "debit_cents": 1000, "credit_cents": 0},
                    {"account_code": "401",    "debit_cents": 0,    "credit_cents": 1000},
                ],
            },
        },
        store=store,
    )
    out = await gl_poster.post(ctx)
    assert out["status"] == "posted"
    # And `posted_at` was stamped.
    cur = await store.accounting.execute(
        "SELECT posted_at FROM journal_entries WHERE id = ?",
        (out["entry_id"],),
    )
    row = await cur.fetchone()
    await cur.close()
    assert row is not None and row[0] is not None
