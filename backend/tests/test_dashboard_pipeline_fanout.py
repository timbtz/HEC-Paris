"""Tests that pipeline lifecycle events fan out to the dashboard bus.

Source: backend-gap-plan §11. Verifies the one-line patch in
`executor.write_event` so the Today dashboard's "recent runs" card
updates without polling.
"""
from __future__ import annotations

from backend.orchestration import event_bus
from backend.orchestration.context import FingentContext
from backend.orchestration.executor import write_event


async def _drain_dashboard(q, *, max_take: int = 4) -> list[dict]:
    out: list[dict] = []
    while not q.empty() and len(out) < max_take:
        out.append(q.get_nowait())
    return out


async def test_pipeline_started_completed_failed_fan_out_to_dashboard(store):
    ctx = FingentContext(
        run_id=1,
        pipeline_name="transaction_booked",
        trigger_source="manual",
        trigger_payload={},
        node_outputs={},
        store=store,
        employee_id=None,
    )
    # Insert a run row so the FK on pipeline_events is satisfied.
    from backend.orchestration.store.writes import write_tx
    async with write_tx(store.orchestration, store.orchestration_lock) as conn:
        await conn.execute(
            "INSERT INTO pipeline_runs "
            "(id, pipeline_name, pipeline_version, trigger_source, trigger_payload, status) "
            "VALUES (1, 'transaction_booked', 1, 'manual', '{}', 'running')",
        )

    q = await event_bus.subscribe_dashboard()
    try:
        await write_event(ctx, "pipeline_started", None, {})
        await write_event(ctx, "node_started", "fetch", {})
        await write_event(ctx, "pipeline_completed", None, {})

        events = await _drain_dashboard(q, max_take=10)
        seen_types = [e["event_type"] for e in events]
        assert "pipeline_started" in seen_types
        assert "pipeline_completed" in seen_types
        # node-level events do NOT fan out to the dashboard.
        assert "node_started" not in seen_types
        for e in events:
            if e["event_type"].startswith("pipeline_"):
                assert e["pipeline_name"] == "transaction_booked"
    finally:
        await event_bus.remove_dashboard_subscriber(q)


async def test_pipeline_failed_also_fans_out(store):
    ctx = FingentContext(
        run_id=2,
        pipeline_name="document_ingested",
        trigger_source="manual",
        trigger_payload={},
        node_outputs={},
        store=store,
        employee_id=None,
    )
    from backend.orchestration.store.writes import write_tx
    async with write_tx(store.orchestration, store.orchestration_lock) as conn:
        await conn.execute(
            "INSERT INTO pipeline_runs "
            "(id, pipeline_name, pipeline_version, trigger_source, trigger_payload, status) "
            "VALUES (2, 'document_ingested', 1, 'manual', '{}', 'running')",
        )

    q = await event_bus.subscribe_dashboard()
    try:
        await write_event(ctx, "pipeline_failed", None, {"error": "boom"})
        events = await _drain_dashboard(q)
        assert any(e["event_type"] == "pipeline_failed" for e in events)
    finally:
        await event_bus.remove_dashboard_subscriber(q)
