"""In-process pub/sub for pipeline events.

Source: REF-SSE-STREAMING-FASTAPI.md:77-189. Phase 1 ships only the bus;
the SSE endpoint that subscribes to it is Phase F. The bus must exist now
because the executor calls `publish_event()` on every node transition.

Design:
- Each `run_id` has a list of subscriber queues (multi-fanout).
- Producers `put_nowait`; on `QueueFull` we silently drop so a slow
  consumer can never block the executor (REF-SSE-STREAMING-FASTAPI.md:138).
- A reaper coroutine drops idle buses past `_BUS_TTL_SECONDS`.
- A dedicated dashboard bus keyed by `_DASHBOARD_BUS_KEY` is exempt from
  the reaper (Phase 2.F dashboard SSE; RealMetaPRD §6.4 envelope events).
"""
from __future__ import annotations

import asyncio
import time
from typing import Any

_DASHBOARD_BUS_KEY = "__dashboard__"

_event_bus: dict[Any, list[asyncio.Queue]] = {}
_bus_expiry: dict[Any, float] = {}
_bus_lock = asyncio.Lock()

_BUS_QUEUE_MAXSIZE = 500
_BUS_TTL_SECONDS = 120
_REAPER_INTERVAL_SECONDS = 60

_TERMINAL_EVENT_TYPES = frozenset({"pipeline_completed", "pipeline_failed"})


async def get_or_create_bus(run_id: Any) -> list[asyncio.Queue]:
    async with _bus_lock:
        if run_id not in _event_bus:
            _event_bus[run_id] = []
            _bus_expiry[run_id] = time.monotonic() + _BUS_TTL_SECONDS
        return _event_bus[run_id]


async def subscribe(run_id: Any) -> asyncio.Queue:
    """Register a subscriber queue for a run. Caller must `remove_subscriber`."""
    q: asyncio.Queue = asyncio.Queue(maxsize=_BUS_QUEUE_MAXSIZE)
    async with _bus_lock:
        bus = _event_bus.setdefault(run_id, [])
        bus.append(q)
        _bus_expiry[run_id] = time.monotonic() + _BUS_TTL_SECONDS
    return q


async def remove_subscriber(run_id: Any, q: asyncio.Queue) -> None:
    async with _bus_lock:
        bus = _event_bus.get(run_id)
        if bus and q in bus:
            bus.remove(q)


async def publish_event(run_id: Any, event: dict) -> None:
    """Fan an event out to all subscribers; drop silently on full queues."""
    async with _bus_lock:
        bus = _event_bus.get(run_id, [])
        _bus_expiry[run_id] = time.monotonic() + _BUS_TTL_SECONDS
        targets = list(bus)
        is_terminal = event.get("event_type") in _TERMINAL_EVENT_TYPES

    for q in targets:
        try:
            q.put_nowait(event)
        except asyncio.QueueFull:
            # Slow consumer; do not block the producer.
            pass

    if is_terminal and run_id != _DASHBOARD_BUS_KEY:
        # Schedule prompt cleanup so completed runs don't accumulate.
        # The dashboard bus is long-lived and never reaped.
        async with _bus_lock:
            _bus_expiry[run_id] = time.monotonic() + 5.0


async def publish_event_dashboard(event: dict) -> None:
    """Publish an event onto the global dashboard bus.

    Used by tools that emit cross-run dashboard signals (envelope debits,
    AI-credit spend, budget reallocation). Phase 2.F dashboard SSE consumes
    via `subscribe_dashboard()`.
    """
    await publish_event(_DASHBOARD_BUS_KEY, event)


async def subscribe_dashboard() -> asyncio.Queue:
    """Subscribe to the long-lived dashboard bus. Caller must
    `remove_dashboard_subscriber` on disconnect."""
    return await subscribe(_DASHBOARD_BUS_KEY)


async def remove_dashboard_subscriber(q: asyncio.Queue) -> None:
    await remove_subscriber(_DASHBOARD_BUS_KEY, q)


async def cleanup_expired_buses() -> None:
    now = time.monotonic()
    async with _bus_lock:
        expired = [rid for rid, t in _bus_expiry.items() if t <= now]
        for rid in expired:
            if rid == _DASHBOARD_BUS_KEY:
                # The dashboard bus is long-lived; never reap it.
                continue
            _event_bus.pop(rid, None)
            _bus_expiry.pop(rid, None)


async def bus_reaper_task() -> None:
    """Long-running reaper. Run as a background task on app startup."""
    while True:
        try:
            await cleanup_expired_buses()
        except Exception:  # noqa: BLE001
            pass
        await asyncio.sleep(_REAPER_INTERVAL_SECONDS)


async def reset_for_tests() -> None:
    async with _bus_lock:
        _event_bus.clear()
        _bus_expiry.clear()
