"""Unit tests for `SwanOAuthClient`.

Source: Phase 2 plan §Task 5; SWAN_API_REFERENCE.md:27-46
(client_credentials flow, no `scope` param, 3600s tokens).

Strategy: drive `httpx.AsyncClient` via `httpx.MockTransport`. We monkey-
patch `time.time` inside `oauth.py` to walk the clock past the refresh
window without sleeping.
"""
from __future__ import annotations

from typing import Any

import httpx
import pytest

from backend.orchestration.swan import oauth as oauth_module
from backend.orchestration.swan.oauth import SwanOAuthClient


def _make_handler(responses: list[dict[str, Any]], calls: list[httpx.Request]):
    """Return a MockTransport handler that walks `responses` in order."""
    cursor = {"i": 0}

    async def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        body = responses[min(cursor["i"], len(responses) - 1)]
        cursor["i"] += 1
        return httpx.Response(200, json=body)

    return handler


async def test_get_token_refreshes_on_first_call():
    calls: list[httpx.Request] = []
    handler = _make_handler(
        [{"access_token": "tok-A", "expires_in": 3600}],
        calls,
    )
    transport = httpx.MockTransport(handler)
    http = httpx.AsyncClient(transport=transport)

    client = SwanOAuthClient(
        client_id="SANDBOX_abc",
        client_secret="shh",
        oauth_url="https://oauth.swan.io/oauth2/token",
        http_client=http,
    )

    token = await client.get_token()

    assert token == "tok-A"
    assert len(calls) == 1
    # Verify form-encoded body and the GOTCHA: no `scope` parameter.
    body_text = calls[0].content.decode()
    assert "grant_type=client_credentials" in body_text
    assert "client_id=SANDBOX_abc" in body_text
    assert "client_secret=shh" in body_text
    assert "scope" not in body_text
    await http.aclose()


async def test_get_token_returns_cache_on_second_call_within_window():
    calls: list[httpx.Request] = []
    handler = _make_handler(
        [{"access_token": "tok-A", "expires_in": 3600}],
        calls,
    )
    transport = httpx.MockTransport(handler)
    http = httpx.AsyncClient(transport=transport)
    client = SwanOAuthClient("id", "sec", "https://x", http_client=http)

    first = await client.get_token()
    second = await client.get_token()

    assert first == second == "tok-A"
    assert len(calls) == 1, "second call must hit the cache, not the network"
    await http.aclose()


async def test_get_token_refreshes_after_expiry(monkeypatch: pytest.MonkeyPatch):
    calls: list[httpx.Request] = []
    handler = _make_handler(
        [
            {"access_token": "tok-A", "expires_in": 3600},
            {"access_token": "tok-B", "expires_in": 3600},
        ],
        calls,
    )
    transport = httpx.MockTransport(handler)
    http = httpx.AsyncClient(transport=transport)
    client = SwanOAuthClient("id", "sec", "https://x", http_client=http)

    # First call at t=1000 — fresh.
    fake_now = {"t": 1000.0}
    monkeypatch.setattr(oauth_module.time, "time", lambda: fake_now["t"])
    first = await client.get_token()
    assert first == "tok-A"
    assert len(calls) == 1

    # Walk past the refresh window (token expires at 1000 + 3600 = 4600;
    # refresh lead is 60s, so any t >= 4540 should refresh).
    fake_now["t"] = 5000.0
    second = await client.get_token()

    assert second == "tok-B"
    assert len(calls) == 2
    await http.aclose()


async def test_invalidate_forces_refresh():
    calls: list[httpx.Request] = []
    handler = _make_handler(
        [
            {"access_token": "tok-A", "expires_in": 3600},
            {"access_token": "tok-B", "expires_in": 3600},
        ],
        calls,
    )
    transport = httpx.MockTransport(handler)
    http = httpx.AsyncClient(transport=transport)
    client = SwanOAuthClient("id", "sec", "https://x", http_client=http)

    first = await client.get_token()
    assert first == "tok-A"

    await client.invalidate()
    second = await client.get_token()

    assert second == "tok-B"
    assert len(calls) == 2
    await http.aclose()
