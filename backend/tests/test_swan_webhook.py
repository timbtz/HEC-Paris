"""Tests for `backend/api/swan_webhook.py`.

Covers signature gating, envelope validation, dedupe, employee
resolution, and route dispatch via the routing table. The tests stub
out `_get_routing` so we never try to call live Swan GraphQL or load
unfinished pipeline files.
"""
from __future__ import annotations

import json
from typing import Any

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from backend.api import swan_webhook
from backend.orchestration.executor import wait_for_run


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #

@pytest_asyncio.fixture
async def client(store, monkeypatch):
    monkeypatch.setenv("SWAN_WEBHOOK_SECRET", "test-secret")
    # Reset module cache between tests so prior monkey-patches don't bleed.
    monkeypatch.setattr(swan_webhook, "_routing_cache", None, raising=False)

    app = FastAPI()
    app.state.store = store
    app.include_router(swan_webhook.router)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


def _stub_routing(monkeypatch, mapping: dict[str, list[str]]) -> None:
    """Override `_get_routing` with a deterministic table for the test."""
    routing = {
        "routes": dict(mapping),
        "defaults": {"unknown_event": ["log_and_continue"]},
    }
    monkeypatch.setattr(swan_webhook, "_get_routing", lambda: routing)


def _envelope(**overrides: Any) -> dict[str, Any]:
    base = {
        "eventType": "Test",
        "eventId": "evt_test_001",
        "eventDate": "2026-04-25T10:00:00Z",
        "projectId": "proj_demo",
        "resourceId": "acc_demo_company__tim",
    }
    base.update(overrides)
    return base


# --------------------------------------------------------------------------- #
# Signature / envelope validation
# --------------------------------------------------------------------------- #

async def test_bad_signature_returns_401(client, monkeypatch):
    _stub_routing(monkeypatch, {"swan.Test": ["noop_demo"]})
    resp = await client.post(
        "/swan/webhook",
        content=json.dumps(_envelope()),
        headers={"x-swan-secret": "wrong"},
    )
    assert resp.status_code == 401
    assert resp.json()["detail"] == "invalid signature"


async def test_missing_secret_header_returns_401(client, monkeypatch):
    _stub_routing(monkeypatch, {"swan.Test": ["noop_demo"]})
    resp = await client.post(
        "/swan/webhook",
        content=json.dumps(_envelope()),
    )
    assert resp.status_code == 401


async def test_missing_envelope_field_returns_400(client, monkeypatch):
    _stub_routing(monkeypatch, {"swan.Test": ["noop_demo"]})
    bad = _envelope()
    del bad["projectId"]
    resp = await client.post(
        "/swan/webhook",
        content=json.dumps(bad),
        headers={"x-swan-secret": "test-secret"},
    )
    assert resp.status_code == 400
    assert "projectId" in resp.json()["detail"]


async def test_malformed_json_returns_400(client, monkeypatch):
    _stub_routing(monkeypatch, {"swan.Test": ["noop_demo"]})
    resp = await client.post(
        "/swan/webhook",
        content=b"{not json",
        headers={"x-swan-secret": "test-secret"},
    )
    assert resp.status_code == 400


# --------------------------------------------------------------------------- #
# Dispatch + dedupe
# --------------------------------------------------------------------------- #

async def test_valid_signature_dispatches_pipeline(
    client, store, monkeypatch, fake_anthropic, fake_anthropic_message,
):
    _stub_routing(monkeypatch, {"swan.Test": ["noop_demo"]})
    _, fake = fake_anthropic
    fake.messages._response = fake_anthropic_message(
        tool_input={"answer": "ok"}, tool_name="submit_test",
    )

    envelope = _envelope(eventId="evt_dispatch_001")
    resp = await client.post(
        "/swan/webhook",
        content=json.dumps(envelope),
        headers={"x-swan-secret": "test-secret"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["event_id"] == "evt_dispatch_001"
    assert isinstance(body["run_ids"], list) and len(body["run_ids"]) == 1

    rid = body["run_ids"][0]

    # Row must exist immediately because _insert_run is awaited before the
    # background task is created.
    cur = await store.orchestration.execute(
        "SELECT pipeline_name, trigger_source, employee_id_logical "
        "FROM pipeline_runs WHERE id = ?",
        (rid,),
    )
    row = await cur.fetchone()
    assert row is not None
    assert row[0] == "noop_demo"
    assert row[1] == "swan.Test"
    # Tim's swan_account_id is the resourceId in the default envelope.
    assert row[2] == "1"  # Tim is the first employee seeded.

    # Drain the background task so we don't leak into the next test.
    await wait_for_run(rid)


async def test_duplicate_event_id_returns_duplicate(
    client, monkeypatch, fake_anthropic, fake_anthropic_message,
):
    _stub_routing(monkeypatch, {"swan.Test": ["noop_demo"]})
    _, fake = fake_anthropic
    fake.messages._response = fake_anthropic_message(
        tool_input={"answer": "ok"}, tool_name="submit_test",
    )

    envelope = _envelope(eventId="evt_dup_001")
    headers = {"x-swan-secret": "test-secret"}

    first = await client.post("/swan/webhook", content=json.dumps(envelope), headers=headers)
    assert first.status_code == 200
    assert first.json()["status"] == "ok"
    for rid in first.json()["run_ids"]:
        await wait_for_run(rid)

    second = await client.post("/swan/webhook", content=json.dumps(envelope), headers=headers)
    assert second.status_code == 200
    body = second.json()
    assert body["status"] == "duplicate"
    assert body["event_id"] == "evt_dup_001"


async def test_unknown_event_type_routes_to_default(
    client, store, monkeypatch,
):
    # No matching swan.* route → must fall back to defaults.unknown_event.
    _stub_routing(monkeypatch, {})
    envelope = _envelope(eventType="MysteryThing", eventId="evt_unknown_001")

    resp = await client.post(
        "/swan/webhook",
        content=json.dumps(envelope),
        headers={"x-swan-secret": "test-secret"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    # log_and_continue is a real pipeline; a run row is created for it.
    assert len(body["run_ids"]) == 1

    rid = body["run_ids"][0]
    cur = await store.orchestration.execute(
        "SELECT pipeline_name, trigger_source FROM pipeline_runs WHERE id = ?",
        (rid,),
    )
    row = await cur.fetchone()
    assert row[0] == "log_and_continue"
    assert row[1] == "swan.MysteryThing"

    await wait_for_run(rid)


async def test_employee_resolution_via_swan_iban(
    client, store, monkeypatch, fake_anthropic, fake_anthropic_message,
):
    """The router accepts either swan_account_id OR swan_iban as resourceId."""
    _stub_routing(monkeypatch, {"swan.Test": ["noop_demo"]})
    _, fake = fake_anthropic
    fake.messages._response = fake_anthropic_message(
        tool_input={"answer": "ok"}, tool_name="submit_test",
    )

    # Marie's IBAN — see migrations/audit/0003_seed_swan_links.py.
    marie_iban = "FR7610278060610001020480302"
    envelope = _envelope(
        eventId="evt_marie_001",
        resourceId=marie_iban,
    )
    resp = await client.post(
        "/swan/webhook",
        content=json.dumps(envelope),
        headers={"x-swan-secret": "test-secret"},
    )
    assert resp.status_code == 200

    rid = resp.json()["run_ids"][0]
    cur = await store.orchestration.execute(
        "SELECT employee_id_logical FROM pipeline_runs WHERE id = ?", (rid,),
    )
    row = await cur.fetchone()
    # Marie is the second seeded employee.
    assert row[0] == "2"

    await wait_for_run(rid)


async def test_company_account_event_employee_id_null(
    client, store, monkeypatch, fake_anthropic, fake_anthropic_message,
):
    """Unknown resourceId means no employee row matches; that's allowed."""
    _stub_routing(monkeypatch, {"swan.Test": ["noop_demo"]})
    _, fake = fake_anthropic
    fake.messages._response = fake_anthropic_message(
        tool_input={"answer": "ok"}, tool_name="submit_test",
    )

    envelope = _envelope(
        eventId="evt_company_001",
        resourceId="acc_unknown_company_pool",
    )
    resp = await client.post(
        "/swan/webhook",
        content=json.dumps(envelope),
        headers={"x-swan-secret": "test-secret"},
    )
    assert resp.status_code == 200

    rid = resp.json()["run_ids"][0]
    cur = await store.orchestration.execute(
        "SELECT employee_id_logical FROM pipeline_runs WHERE id = ?", (rid,),
    )
    row = await cur.fetchone()
    assert row[0] is None

    await wait_for_run(rid)


async def test_missing_pipeline_file_logs_and_skips(
    client, store, monkeypatch, caplog,
):
    """If routing names a pipeline file that doesn't exist, we 200 anyway."""
    _stub_routing(monkeypatch, {"swan.Test": ["nonexistent_pipeline_zzz"]})
    envelope = _envelope(eventId="evt_skip_001")

    with caplog.at_level("WARNING"):
        resp = await client.post(
            "/swan/webhook",
            content=json.dumps(envelope),
            headers={"x-swan-secret": "test-secret"},
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["run_ids"] == []
    assert any("pipeline_skipped" in rec.message or "nonexistent_pipeline_zzz" in str(rec.__dict__)
               for rec in caplog.records)
