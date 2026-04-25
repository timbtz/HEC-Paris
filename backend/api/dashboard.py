"""Dashboard SSE — top-level event bus for cross-cutting events.

Source: RealMetaPRD §10.

Long-lived SSE: the connection never auto-closes, even on
`pipeline_completed`-shaped events (a finished pipeline run is a
data point on the dashboard, not a reason to disconnect every viewer).
The reaper is also configured to skip the dashboard bus key
(`event_bus.cleanup_expired_buses`).
"""
from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from ..orchestration import event_bus


router = APIRouter(prefix="/dashboard")

# Short polling so the generator notices a client disconnect within
# ~1 s instead of holding on for the full heartbeat window.
_POLL_INTERVAL_S = 1.0
_HEARTBEAT_INTERVAL_S = 15.0


@router.get("/stream")
async def stream_dashboard(request: Request) -> StreamingResponse:
    async def event_stream():
        q = await event_bus.subscribe_dashboard()
        try:
            yield ": heartbeat\n\n"
            since_heartbeat = 0.0
            while True:
                if await request.is_disconnected():
                    return
                try:
                    event = await asyncio.wait_for(q.get(), timeout=_POLL_INTERVAL_S)
                except asyncio.TimeoutError:
                    since_heartbeat += _POLL_INTERVAL_S
                    if since_heartbeat >= _HEARTBEAT_INTERVAL_S:
                        yield ": heartbeat\n\n"
                        since_heartbeat = 0.0
                    continue
                since_heartbeat = 0.0
                yield f"data: {json.dumps(event)}\n\n"
                # Dashboard SSE never auto-closes; terminal events are passed
                # through but the loop continues.
        finally:
            await event_bus.remove_dashboard_subscriber(q)

    headers = {
        "Cache-Control": "no-cache",
        "X-Accel-Buffering": "no",
        "Connection": "keep-alive",
    }
    return StreamingResponse(
        event_stream(), media_type="text/event-stream", headers=headers
    )
