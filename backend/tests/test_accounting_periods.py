"""Tests for `backend.api.accounting_periods` and the period_code default
applied to closing-pipeline triggers in `backend.api.runs`.

Source: backend-gap-plan §11 follow-up. The frontend's "Run period close"
buttons fire with an empty payload, so the API has to look up the most-
recent non-closed period. Demo seed (migration 0009) ships:

    2026-Q1 closed | 2026-Q2 closing | 2026-Q3 open

ORDER BY start_date DESC over `status != 'closed'` therefore yields
2026-Q3 first.
"""
from __future__ import annotations

import json

import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

import backend.orchestration  # noqa: F401 — registers production tools/agents
from backend.api.accounting_periods import router as accounting_periods_router
from backend.api.runs import router as runs_router
from backend.orchestration.store.writes import write_tx


@pytest_asyncio.fixture
async def app(store, fake_anthropic, fake_anthropic_message):
    _, fake = fake_anthropic
    fake.messages._response = fake_anthropic_message(
        tool_input={"answer": "ok", "confidence": 0.9},
        tool_name="submit_test",
    )
    a = FastAPI()
    a.state.store = store
    a.include_router(accounting_periods_router)
    a.include_router(runs_router)
    return a


@pytest_asyncio.fixture
async def client(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac


# --------------------------------------------------------------------------- #
# GET /accounting_periods
# --------------------------------------------------------------------------- #


async def test_list_accounting_periods_returns_seeded_rows_desc(client):
    resp = await client.get("/accounting_periods")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert isinstance(body, list)
    codes = [p["code"] for p in body]
    # Migration 0009 seeds these three; ORDER BY start_date DESC.
    assert codes[:3] == ["2026-Q3", "2026-Q2", "2026-Q1"]

    by_code = {p["code"]: p for p in body}
    assert by_code["2026-Q1"]["status"] == "closed"
    assert by_code["2026-Q2"]["status"] == "closing"
    assert by_code["2026-Q3"]["status"] == "open"

    for p in body:
        assert isinstance(p["id"], int)
        assert "start_date" in p and "end_date" in p
        assert p["closed_at"] is None or isinstance(p["closed_at"], str)
        assert p["closed_by"] is None or isinstance(p["closed_by"], int)


async def test_list_accounting_periods_picks_up_new_open_period(store, client):
    async with write_tx(store.accounting, store.accounting_lock) as conn:
        await conn.execute(
            "INSERT INTO accounting_periods (code, start_date, end_date, status) "
            "VALUES (?, ?, ?, ?)",
            ("2026-Q4", "2026-10-01", "2026-12-31", "open"),
        )
    resp = await client.get("/accounting_periods")
    assert resp.status_code == 200
    codes = [p["code"] for p in resp.json()]
    assert codes[0] == "2026-Q4"


# --------------------------------------------------------------------------- #
# POST /pipelines/run/{name} — period_code defaulting
# --------------------------------------------------------------------------- #


async def _trigger_payload_for_run(store, run_id: int) -> dict:
    cur = await store.orchestration.execute(
        "SELECT trigger_payload FROM pipeline_runs WHERE id = ?", (run_id,)
    )
    row = await cur.fetchone()
    await cur.close()
    assert row is not None
    return json.loads(row["trigger_payload"])


async def test_period_close_defaults_period_code_to_latest_open(client, store):
    resp = await client.post(
        "/pipelines/run/period_close",
        json={"trigger_payload": {}},
    )
    assert resp.status_code == 200, resp.text
    run_id = resp.json()["run_id"]

    stored = await _trigger_payload_for_run(store, run_id)
    # 2026-Q3 (open) wins over 2026-Q2 (closing) because of ORDER BY start_date DESC.
    assert stored.get("period_code") == "2026-Q3"


async def test_period_close_keeps_explicit_period_code(client, store):
    resp = await client.post(
        "/pipelines/run/period_close",
        json={"trigger_payload": {"period_code": "2026-Q2"}},
    )
    assert resp.status_code == 200, resp.text
    run_id = resp.json()["run_id"]

    stored = await _trigger_payload_for_run(store, run_id)
    assert stored.get("period_code") == "2026-Q2"


async def test_period_close_422_when_no_open_period(client, store):
    async with write_tx(store.accounting, store.accounting_lock) as conn:
        await conn.execute("UPDATE accounting_periods SET status = 'closed'")

    resp = await client.post(
        "/pipelines/run/period_close",
        json={"trigger_payload": {}},
    )
    assert resp.status_code == 422, resp.text
    assert "period_code" in resp.json()["detail"]


async def test_vat_return_and_year_end_also_default(client, store):
    for name in ("vat_return", "year_end_close"):
        resp = await client.post(
            f"/pipelines/run/{name}",
            json={"trigger_payload": {}},
        )
        assert resp.status_code == 200, resp.text
        run_id = resp.json()["run_id"]
        stored = await _trigger_payload_for_run(store, run_id)
        assert stored.get("period_code") == "2026-Q3", name


async def test_other_pipelines_do_not_default_period_code(client, store):
    resp = await client.post(
        "/pipelines/run/noop_demo",
        json={"trigger_payload": {}},
    )
    assert resp.status_code == 200, resp.text
    run_id = resp.json()["run_id"]
    stored = await _trigger_payload_for_run(store, run_id)
    assert "period_code" not in stored
