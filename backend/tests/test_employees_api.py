"""Tests for `backend.api.employees`.

Source: backend-gap-plan §3. Validates that the seeded `audit.employees`
rows surface through the list endpoint, that the active filter works,
and that the detail endpoint includes the envelope summary + 30-day
spend aggregate.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from backend.api.employees import router as employees_router
from backend.orchestration.store.writes import write_tx


@pytest_asyncio.fixture
async def app(store):
    a = FastAPI()
    a.state.store = store
    a.include_router(employees_router)
    return a


@pytest_asyncio.fixture
async def client(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac


async def test_list_employees_returns_seeded_three(client):
    resp = await client.get("/employees")
    assert resp.status_code == 200, resp.text
    items = resp.json()["items"]
    # Migration audit/0002 seeds three rows: Tim, Marie, Paul.
    names = {it["full_name"] for it in items}
    assert {"Tim", "Marie", "Paul"} <= names
    for it in items:
        assert isinstance(it["active"], bool)
        assert it["active"] is True


async def test_list_employees_active_filter(store, client):
    async with write_tx(store.audit, store.audit_lock) as conn:
        await conn.execute(
            "UPDATE employees SET active = 0 WHERE email = 'paul@hec.example'"
        )
    resp = await client.get("/employees?active=true")
    items = resp.json()["items"]
    assert all(it["active"] is True for it in items)
    resp = await client.get("/employees?active=false")
    items = resp.json()["items"]
    assert all(it["active"] is False for it in items)
    assert len(items) == 1


async def test_get_employee_includes_envelopes_and_spend(store, client):
    # Seed a couple of agent_costs rows for employee 1 (rolled-up over 30d).
    async with write_tx(store.audit, store.audit_lock) as conn:
        cur = await conn.execute(
            "INSERT INTO agent_decisions "
            "(run_id_logical, node_id, source, runner, model) "
            "VALUES (1, 'x', 'agent', 'anthropic', 'claude-haiku-4-5')"
        )
        d1 = int(cur.lastrowid)
        cur = await conn.execute(
            "INSERT INTO agent_decisions "
            "(run_id_logical, node_id, source, runner, model) "
            "VALUES (2, 'x', 'agent', 'anthropic', 'claude-haiku-4-5')"
        )
        d2 = int(cur.lastrowid)
        await conn.execute(
            "INSERT INTO agent_costs "
            "(decision_id, employee_id, provider, model, cost_micro_usd) "
            "VALUES (?, 1, 'anthropic', 'claude-haiku-4-5', 1500)",
            (d1,),
        )
        await conn.execute(
            "INSERT INTO agent_costs "
            "(decision_id, employee_id, provider, model, cost_micro_usd) "
            "VALUES (?, 1, 'anthropic', 'claude-haiku-4-5', 2500)",
            (d2,),
        )

    resp = await client.get("/employees/1")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["id"] == 1
    assert body["full_name"] == "Tim"

    # 30-day spend rollup
    assert body["spend_30d"]["cost_micro_usd"] == 4000
    assert body["spend_30d"]["calls"] == 2

    # Envelopes block exists for the current month.
    period = datetime.now(timezone.utc).strftime("%Y-%m")
    assert body["envelopes_current_period"]["period"] == period


async def test_get_employee_404(client):
    resp = await client.get("/employees/9999")
    assert resp.status_code == 404
