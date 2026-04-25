"""Tests for `backend.api.runs` — manual trigger, run inspection, review approve.

Source: RealMetaPRD §10.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

import backend.orchestration  # noqa: F401 — registers production tools/agents
from backend.api.dashboard import router as dashboard_router
from backend.api.documents import router as documents_router
from backend.api.runs import router as runs_router
from backend.orchestration.executor import wait_for_run
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
    a.include_router(documents_router)
    a.include_router(runs_router)
    a.include_router(dashboard_router)
    return a


@pytest_asyncio.fixture
async def client(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac


# --------------------------------------------------------------------------- #
# Seed helpers
# --------------------------------------------------------------------------- #

async def _seed_chart_account(store, code: str = "626100") -> None:
    async with write_tx(store.accounting, store.accounting_lock) as conn:
        await conn.execute(
            "INSERT OR IGNORE INTO chart_of_accounts (code, name, type) "
            "VALUES (?, ?, ?)",
            (code, "Test Account", "expense"),
        )


async def _seed_review_entry(store) -> tuple[int, list[int]]:
    """Insert a journal_entry (status='review') with two lines + traces."""
    await _seed_chart_account(store, "626100")
    await _seed_chart_account(store, "401000")

    today = datetime.now(timezone.utc).date().isoformat()
    async with write_tx(store.accounting, store.accounting_lock) as conn:
        cur = await conn.execute(
            "INSERT INTO journal_entries "
            "(basis, entry_date, description, source_pipeline, source_run_id, status) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("accrual", today, "Test review entry", "document_ingested", 1, "review"),
        )
        entry_id = int(cur.lastrowid)

        cur = await conn.execute(
            "INSERT INTO journal_lines "
            "(entry_id, account_code, debit_cents, credit_cents) "
            "VALUES (?, ?, ?, ?)",
            (entry_id, "626100", 1200, 0),
        )
        line1 = int(cur.lastrowid)

        cur = await conn.execute(
            "INSERT INTO journal_lines "
            "(entry_id, account_code, debit_cents, credit_cents) "
            "VALUES (?, ?, ?, ?)",
            (entry_id, "401000", 0, 1200),
        )
        line2 = int(cur.lastrowid)

        for line_id in (line1, line2):
            await conn.execute(
                "INSERT INTO decision_traces "
                "(line_id, source, confidence) VALUES (?, 'agent', 0.92)",
                (line_id,),
            )

    return entry_id, [line1, line2]


# --------------------------------------------------------------------------- #
# POST /pipelines/run/{name}
# --------------------------------------------------------------------------- #

async def test_trigger_pipeline_returns_run_id(client, store):
    resp = await client.post(
        "/pipelines/run/noop_demo",
        json={"trigger_payload": {"hello": "world"}, "employee_id": 1},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["run_id"] >= 1
    assert body["stream_url"] == f"/runs/{body['run_id']}/stream"

    await wait_for_run(body["run_id"])

    # Run row exists, status == completed.
    cur = await store.orchestration.execute(
        "SELECT status FROM pipeline_runs WHERE id = ?", (body["run_id"],)
    )
    row = await cur.fetchone()
    await cur.close()
    assert row[0] == "completed"


# --------------------------------------------------------------------------- #
# GET /runs/{run_id}
# --------------------------------------------------------------------------- #

async def test_get_run_returns_run_events_and_decisions(client, store):
    resp = await client.post(
        "/pipelines/run/noop_demo",
        json={"trigger_payload": {"x": 1}, "employee_id": 1},
    )
    run_id = resp.json()["run_id"]
    await wait_for_run(run_id)

    resp2 = await client.get(f"/runs/{run_id}")
    assert resp2.status_code == 200, resp2.text
    body = resp2.json()

    assert body["run"]["id"] == run_id
    assert body["run"]["status"] == "completed"
    assert any(e["event_type"] == "pipeline_started" for e in body["events"])
    assert any(e["event_type"] == "pipeline_completed" for e in body["events"])
    # noop_demo has 1 agent node → at least one agent_decisions row
    assert len(body["agent_decisions"]) >= 1


async def test_get_run_404_for_unknown(client):
    resp = await client.get("/runs/99999")
    assert resp.status_code == 404


# --------------------------------------------------------------------------- #
# POST /review/{entry_id}/approve
# --------------------------------------------------------------------------- #

async def test_approve_flips_status_and_stamps_traces(client, store):
    entry_id, line_ids = await _seed_review_entry(store)

    resp = await client.post(
        f"/review/{entry_id}/approve",
        json={"approver_id": 42},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body == {"entry_id": entry_id, "approver_id": 42, "status": "approved"}

    cur = await store.accounting.execute(
        "SELECT status FROM journal_entries WHERE id = ?", (entry_id,)
    )
    row = await cur.fetchone()
    await cur.close()
    assert row[0] == "posted"

    placeholders = ",".join("?" for _ in line_ids)
    cur = await store.accounting.execute(
        f"SELECT approver_id, approved_at FROM decision_traces "
        f"WHERE line_id IN ({placeholders})",
        tuple(line_ids),
    )
    rows = await cur.fetchall()
    await cur.close()
    for r in rows:
        assert int(r[0]) == 42
        assert r[1] is not None  # ISO timestamp stamped


async def test_approve_idempotent(client, store):
    entry_id, _ = await _seed_review_entry(store)

    resp1 = await client.post(f"/review/{entry_id}/approve", json={"approver_id": 1})
    assert resp1.status_code == 200
    resp2 = await client.post(f"/review/{entry_id}/approve", json={"approver_id": 1})
    # Second call still 200; status remains 'posted' (UPDATE WHERE status='review' no-ops).
    assert resp2.status_code == 200

    cur = await store.accounting.execute(
        "SELECT status FROM journal_entries WHERE id = ?", (entry_id,)
    )
    row = await cur.fetchone()
    await cur.close()
    assert row[0] == "posted"


async def test_approve_404_for_unknown_entry(client):
    resp = await client.post("/review/9999/approve", json={"approver_id": 1})
    assert resp.status_code == 404


async def test_approve_requires_approver_id(client, store):
    entry_id, _ = await _seed_review_entry(store)
    resp = await client.post(f"/review/{entry_id}/approve", json={})
    assert resp.status_code == 400
