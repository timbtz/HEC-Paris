"""Tests for `backend/api/external_webhook.py`.

Stripe HMAC verifier path; the dispatch path mirrors swan_webhook.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import time
from typing import Any

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from backend.api import external_webhook
from backend.orchestration.executor import wait_for_run


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #

_SECRET = "whsec_test"


@pytest_asyncio.fixture
async def client(store, monkeypatch):
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", _SECRET)
    monkeypatch.setattr(external_webhook, "_routing_cache", None, raising=False)

    app = FastAPI()
    app.state.store = store
    app.include_router(external_webhook.router)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


def _stub_routing(monkeypatch, mapping: dict[str, list[str]]) -> None:
    routing = {
        "routes": dict(mapping),
        "defaults": {"unknown_event": ["log_and_continue"]},
    }
    monkeypatch.setattr(external_webhook, "_get_routing", lambda: routing)


def _stripe_signed(body: bytes, secret: str = _SECRET) -> dict[str, str]:
    ts = str(int(time.time()))
    sig = hmac.new(
        secret.encode(),
        f"{ts}.{body.decode()}".encode(),
        hashlib.sha256,
    ).hexdigest()
    return {"stripe-signature": f"t={ts},v1={sig}"}


def _stripe_body(event_id: str = "evt_test_001",
                 event_type: str = "external.crm.invoice_paid") -> bytes:
    return json.dumps({
        "id": event_id,
        "type": event_type,
        "data": {"object": {"amount": 4200, "currency": "usd"}},
    }).encode()


# --------------------------------------------------------------------------- #
# Cases
# --------------------------------------------------------------------------- #

async def test_unknown_provider_returns_404(client, monkeypatch):
    _stub_routing(monkeypatch, {})
    resp = await client.post("/external/webhook/unknown_xyz", content=b"{}")
    assert resp.status_code == 404
    assert resp.json()["detail"] == "unknown provider"


async def test_tampered_body_returns_401(client, monkeypatch):
    _stub_routing(monkeypatch, {})
    body = _stripe_body()
    headers = _stripe_signed(body)
    # Tamper the body after signing.
    tampered = body.replace(b"4200", b"9999")
    resp = await client.post(
        "/external/webhook/stripe",
        content=tampered,
        headers=headers,
    )
    assert resp.status_code == 401
    assert resp.json()["detail"] == "invalid signature"


async def test_missing_signature_header_returns_401(client, monkeypatch):
    _stub_routing(monkeypatch, {})
    resp = await client.post(
        "/external/webhook/stripe",
        content=_stripe_body(),
    )
    assert resp.status_code == 401


async def test_valid_stripe_signature_dispatches(
    client, store, monkeypatch,
):
    _stub_routing(monkeypatch, {
        "external.stripe.external.crm.invoice_paid": ["log_and_continue"],
    })
    body = _stripe_body(event_id="evt_ok_001")
    headers = _stripe_signed(body)

    resp = await client.post(
        "/external/webhook/stripe",
        content=body,
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    out = resp.json()
    assert out["status"] == "ok"
    assert out["event_id"] == "evt_ok_001"
    assert len(out["run_ids"]) == 1

    rid = out["run_ids"][0]
    cur = await store.orchestration.execute(
        "SELECT pipeline_name, trigger_source FROM pipeline_runs WHERE id = ?",
        (rid,),
    )
    row = await cur.fetchone()
    assert row[0] == "log_and_continue"
    assert row[1] == "external.stripe.external.crm.invoice_paid"

    await wait_for_run(rid)


async def test_duplicate_event_id_returns_duplicate(client, monkeypatch):
    _stub_routing(monkeypatch, {
        "external.stripe.external.crm.invoice_paid": ["log_and_continue"],
    })
    body = _stripe_body(event_id="evt_dup_001")
    headers = _stripe_signed(body)

    first = await client.post("/external/webhook/stripe", content=body, headers=headers)
    assert first.status_code == 200
    assert first.json()["status"] == "ok"
    for rid in first.json()["run_ids"]:
        await wait_for_run(rid)

    # Re-send the same event. Build a fresh signature (different t allowed
    # because the dedupe is based on event_id, not signature).
    body2 = _stripe_body(event_id="evt_dup_001")
    headers2 = _stripe_signed(body2)
    second = await client.post("/external/webhook/stripe", content=body2, headers=headers2)
    assert second.status_code == 200
    assert second.json()["status"] == "duplicate"
    assert second.json()["event_id"] == "evt_dup_001"


async def test_unknown_event_type_routes_to_default(client, store, monkeypatch):
    """No specific route for the event_type → defaults.unknown_event fires."""
    _stub_routing(monkeypatch, {})  # No matching routes at all
    body = _stripe_body(event_id="evt_unk_001", event_type="some.unknown.thing")
    headers = _stripe_signed(body)

    resp = await client.post(
        "/external/webhook/stripe",
        content=body,
        headers=headers,
    )
    assert resp.status_code == 200
    out = resp.json()
    assert out["status"] == "ok"
    assert len(out["run_ids"]) == 1

    rid = out["run_ids"][0]
    cur = await store.orchestration.execute(
        "SELECT pipeline_name FROM pipeline_runs WHERE id = ?", (rid,),
    )
    row = await cur.fetchone()
    assert row[0] == "log_and_continue"

    await wait_for_run(rid)


async def test_multiple_v1_signatures_accept_first_match(client, monkeypatch):
    """Stripe rotates keys with a grace window; we accept the matching v1."""
    _stub_routing(monkeypatch, {
        "external.stripe.external.crm.invoice_paid": ["log_and_continue"],
    })
    body = _stripe_body(event_id="evt_rot_001")
    ts = str(int(time.time()))
    sig_good = hmac.new(
        _SECRET.encode(),
        f"{ts}.{body.decode()}".encode(),
        hashlib.sha256,
    ).hexdigest()
    sig_bad = "f" * 64
    headers = {"stripe-signature": f"t={ts},v1={sig_bad},v1={sig_good}"}

    resp = await client.post(
        "/external/webhook/stripe",
        content=body,
        headers=headers,
    )
    assert resp.status_code == 200
    for rid in resp.json()["run_ids"]:
        await wait_for_run(rid)
