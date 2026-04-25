"""Tests for `backend.api.dashboard` — SSE generator behaviour.

Source: RealMetaPRD §10.

httpx ``ASGITransport`` buffers the full response body before exposing it
(see ``httpx/_transports/asgi.py``), so it cannot be used to test
streaming responses. We therefore invoke the route's ``StreamingResponse``
generator directly via a fake ``Request``: that exercises the same code
path the live server runs, while letting us drive subscribe → publish →
read deterministically.
"""
from __future__ import annotations

import asyncio
import json

import pytest

from backend.api.dashboard import stream_dashboard
from backend.orchestration import event_bus


class _FakeRequest:
    """Minimal stand-in for ``starlette.requests.Request`` for SSE tests."""

    def __init__(self) -> None:
        self._disconnected = False

    async def is_disconnected(self) -> bool:
        return self._disconnected

    def disconnect(self) -> None:
        self._disconnected = True


async def _take_frame(gen) -> str:
    """Return the next chunk yielded by an SSE generator (string-typed)."""
    chunk = await gen.__anext__()
    return chunk if isinstance(chunk, str) else chunk.decode("utf-8")


async def test_dashboard_stream_emits_initial_heartbeat():
    request = _FakeRequest()
    response = await stream_dashboard(request)
    gen = response.body_iterator
    try:
        first = await asyncio.wait_for(_take_frame(gen), timeout=2.0)
        assert first.startswith(": heartbeat")
    finally:
        request.disconnect()
        await gen.aclose()


async def test_dashboard_stream_forwards_published_event():
    request = _FakeRequest()
    response = await stream_dashboard(request)
    gen = response.body_iterator
    try:
        # Drain the initial handshake heartbeat.
        first = await asyncio.wait_for(_take_frame(gen), timeout=2.0)
        assert first.startswith(": heartbeat")

        await event_bus.publish_event_dashboard(
            {"event_type": "envelope.decremented", "amount_cents": 4242}
        )

        # Pull frames until a `data:` frame arrives (skip stray heartbeats).
        for _ in range(10):
            frame = await asyncio.wait_for(_take_frame(gen), timeout=3.0)
            if frame.startswith("data:"):
                payload = json.loads(frame[len("data:"):].strip())
                assert payload["event_type"] == "envelope.decremented"
                assert payload["amount_cents"] == 4242
                return
        pytest.fail("no data frame arrived after publish")
    finally:
        request.disconnect()
        await gen.aclose()


async def test_dashboard_stream_returns_response_headers():
    request = _FakeRequest()
    response = await stream_dashboard(request)
    try:
        assert response.media_type == "text/event-stream"
        assert response.headers["cache-control"] == "no-cache"
        assert response.headers["x-accel-buffering"] == "no"
    finally:
        request.disconnect()
        await response.body_iterator.aclose()
