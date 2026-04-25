"""Tests for `backend.orchestration.event_bus`.

Focus: the dashboard bus wiring (Phase 2 Task 48). Per-run-bus behavior is
covered indirectly by the executor tests; here we just pin the dashboard
contract — round-trip publish/subscribe and reaper exemption.
"""
from __future__ import annotations

import asyncio

import pytest

from backend.orchestration import event_bus


pytestmark = pytest.mark.asyncio


async def test_publish_dashboard_round_trips_to_subscribe_dashboard():
    """publish_event_dashboard delivers to subscribe_dashboard subscribers."""
    q = await event_bus.subscribe_dashboard()
    try:
        await event_bus.publish_event_dashboard({"event_type": "envelope.decremented", "amount_cents": 1234})
        evt = await asyncio.wait_for(q.get(), timeout=1.0)
        assert evt == {"event_type": "envelope.decremented", "amount_cents": 1234}
    finally:
        await event_bus.remove_dashboard_subscriber(q)


async def test_dashboard_bus():
    """Dashboard subscribe + publish; multi-fanout to multiple subscribers."""
    q1 = await event_bus.subscribe_dashboard()
    q2 = await event_bus.subscribe_dashboard()
    try:
        await event_bus.publish_event_dashboard({"event_type": "ai_credit.spent", "cost_cents": 42})
        evt1 = await asyncio.wait_for(q1.get(), timeout=1.0)
        evt2 = await asyncio.wait_for(q2.get(), timeout=1.0)
        assert evt1 == {"event_type": "ai_credit.spent", "cost_cents": 42}
        assert evt2 == evt1
    finally:
        await event_bus.remove_dashboard_subscriber(q1)
        await event_bus.remove_dashboard_subscriber(q2)


async def test_dashboard_bus_survives_reaper():
    """Reaper never evicts the dashboard bus, even if its expiry has elapsed."""
    q = await event_bus.subscribe_dashboard()
    try:
        # Force the dashboard bus's expiry into the past so the reaper would
        # normally collect it.
        async with event_bus._bus_lock:  # noqa: SLF001 — direct access is intentional in this test
            event_bus._bus_expiry[event_bus._DASHBOARD_BUS_KEY] = 0.0  # noqa: SLF001

        await event_bus.cleanup_expired_buses()

        # Bus key still present; subscriber queue still wired up.
        assert event_bus._DASHBOARD_BUS_KEY in event_bus._event_bus  # noqa: SLF001
        assert q in event_bus._event_bus[event_bus._DASHBOARD_BUS_KEY]  # noqa: SLF001

        # And it still routes events.
        await event_bus.publish_event_dashboard({"event_type": "ping"})
        evt = await asyncio.wait_for(q.get(), timeout=1.0)
        assert evt == {"event_type": "ping"}
    finally:
        await event_bus.remove_dashboard_subscriber(q)


async def test_dashboard_terminal_event_does_not_schedule_reap():
    """A terminal event_type on the dashboard bus must not shorten its expiry."""
    q = await event_bus.subscribe_dashboard()
    try:
        await event_bus.publish_event_dashboard({"event_type": "pipeline_completed"})
        await asyncio.wait_for(q.get(), timeout=1.0)

        # Force the reaper to run.
        await event_bus.cleanup_expired_buses()

        # Dashboard bus must still exist.
        assert event_bus._DASHBOARD_BUS_KEY in event_bus._event_bus  # noqa: SLF001
    finally:
        await event_bus.remove_dashboard_subscriber(q)


async def test_reset_for_tests_clears_dashboard_bus():
    """reset_for_tests must wipe everything — including the dashboard bus."""
    await event_bus.subscribe_dashboard()
    assert event_bus._DASHBOARD_BUS_KEY in event_bus._event_bus  # noqa: SLF001

    await event_bus.reset_for_tests()
    assert event_bus._DASHBOARD_BUS_KEY not in event_bus._event_bus  # noqa: SLF001
    assert event_bus._DASHBOARD_BUS_KEY not in event_bus._bus_expiry  # noqa: SLF001
