"""Tests for `GET /runs`, `GET /pipelines`, `GET /pipelines/{name}`.

Source: backend-gap-plan §1. Covers paginated run list with cost +
review aggregates, pipeline catalog, and DAG topology endpoint.
"""
from __future__ import annotations

import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

import backend.orchestration  # noqa: F401 — registers production tools/agents
from backend.api.runs import router as runs_router
from backend.orchestration.store.writes import write_tx


@pytest_asyncio.fixture
async def app(store):
    a = FastAPI()
    a.state.store = store
    a.include_router(runs_router)
    return a


@pytest_asyncio.fixture
async def client(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac


async def _seed_run(
    store,
    *,
    pipeline_name: str = "transaction_booked",
    status: str = "completed",
    employee_id_logical: str | None = "1",
    started_at: str = "2026-04-25T08:14:31",
    completed_at: str | None = "2026-04-25T08:14:34",
) -> int:
    async with write_tx(store.orchestration, store.orchestration_lock) as conn:
        cur = await conn.execute(
            "INSERT INTO pipeline_runs "
            "(pipeline_name, pipeline_version, trigger_source, trigger_payload, "
            " employee_id_logical, status, started_at, completed_at) "
            "VALUES (?, 1, 'manual', '{}', ?, ?, ?, ?)",
            (pipeline_name, employee_id_logical, status, started_at, completed_at),
        )
        run_id = int(cur.lastrowid)
    return run_id


async def _seed_decision_with_cost(store, run_id: int, cost_micro_usd: int) -> None:
    async with write_tx(store.audit, store.audit_lock) as conn:
        cur = await conn.execute(
            "INSERT INTO agent_decisions "
            "(run_id_logical, node_id, source, runner, model) "
            "VALUES (?, 'n', 'agent', 'anthropic', 'claude-haiku-4-5')",
            (run_id,),
        )
        decision_id = int(cur.lastrowid)
        await conn.execute(
            "INSERT INTO agent_costs "
            "(decision_id, employee_id, provider, model, cost_micro_usd) "
            "VALUES (?, 1, 'anthropic', 'claude-haiku-4-5', ?)",
            (decision_id, cost_micro_usd),
        )


async def _seed_review_for_run(store, run_id: int) -> None:
    async with write_tx(store.accounting, store.accounting_lock) as conn:
        await conn.execute(
            "INSERT OR IGNORE INTO chart_of_accounts (code, name, type) "
            "VALUES ('626100', 'X', 'expense')"
        )
        cur = await conn.execute(
            "INSERT INTO journal_entries "
            "(basis, entry_date, source_pipeline, source_run_id, status) "
            "VALUES ('cash', '2026-04-25', 'transaction_booked', ?, 'review')",
            (run_id,),
        )
        entry_id = int(cur.lastrowid)
        await conn.execute(
            "INSERT INTO review_queue (entry_id, kind, confidence, reason) "
            "VALUES (?, 'low_confidence', 0.42, 'flagged in tests')",
            (entry_id,),
        )


# --------------------------------------------------------------------------- #
# GET /runs
# --------------------------------------------------------------------------- #


async def test_list_runs_default_pagination_newest_first(store, client):
    for i in range(3):
        await _seed_run(store, started_at=f"2026-04-{20 + i:02d}T08:00:00")
    resp = await client.get("/runs")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["limit"] == 50
    assert body["offset"] == 0
    assert body["total"] == 3
    assert len(body["items"]) == 3
    ids = [it["id"] for it in body["items"]]
    assert ids == sorted(ids, reverse=True)


async def test_list_runs_aggregates_cost_and_reviews(store, client):
    rid = await _seed_run(store, pipeline_name="transaction_booked")
    await _seed_decision_with_cost(store, rid, 1000)
    await _seed_decision_with_cost(store, rid, 2500)
    await _seed_review_for_run(store, rid)

    resp = await client.get("/runs")
    assert resp.status_code == 200, resp.text
    item = resp.json()["items"][0]
    assert item["id"] == rid
    assert item["total_cost_micro_usd"] == 3500
    assert item["review_count"] == 1
    assert item["elapsed_ms"] == 3000  # 31 → 34 = 3 s
    assert item["employee_id_logical"] == 1


async def test_list_runs_filters_pipeline_status_and_dates(store, client):
    await _seed_run(store, pipeline_name="transaction_booked", status="completed",
                    started_at="2026-04-01T00:00:00")
    await _seed_run(store, pipeline_name="document_ingested", status="failed",
                    started_at="2026-04-15T00:00:00")
    await _seed_run(store, pipeline_name="transaction_booked", status="completed",
                    started_at="2026-05-01T00:00:00")

    resp = await client.get("/runs?pipeline_name=transaction_booked")
    assert resp.json()["total"] == 2

    resp = await client.get("/runs?status=failed")
    assert resp.json()["total"] == 1

    resp = await client.get("/runs?from=2026-04-10&to=2026-04-30")
    body = resp.json()
    assert body["total"] == 1
    assert body["items"][0]["pipeline_name"] == "document_ingested"


async def test_list_runs_handles_in_progress(store, client):
    await _seed_run(store, status="running", completed_at=None)
    resp = await client.get("/runs")
    item = resp.json()["items"][0]
    assert item["status"] == "running"
    assert item["completed_at"] is None
    assert item["elapsed_ms"] is None


# --------------------------------------------------------------------------- #
# GET /pipelines
# --------------------------------------------------------------------------- #


async def test_list_pipelines_returns_known_yaml_files(client):
    resp = await client.get("/pipelines")
    assert resp.status_code == 200, resp.text
    items = resp.json()["items"]
    names = {it["name"] for it in items}
    # A handful of known pipelines from `backend/orchestration/pipelines/`.
    assert {"transaction_booked", "document_ingested", "period_close"} <= names
    for it in items:
        assert isinstance(it["node_count"], int) and it["node_count"] > 0
        assert it["kind"] in ("event", "manual")


async def test_list_pipelines_kind_event_for_routed(client):
    resp = await client.get("/pipelines")
    by_name = {it["name"]: it for it in resp.json()["items"]}
    # `transaction_booked` is referenced from `routing.yaml`.
    assert by_name["transaction_booked"]["kind"] == "event"


# --------------------------------------------------------------------------- #
# GET /pipelines/{name}
# --------------------------------------------------------------------------- #


async def test_get_pipeline_returns_dag_topology(client):
    resp = await client.get("/pipelines/transaction_booked")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["name"] == "transaction_booked"
    assert body["kind"] == "event"
    assert isinstance(body["nodes"], list) and body["nodes"]
    first = body["nodes"][0]
    assert {"id", "kind", "ref", "depends_on", "when", "cacheable"} <= set(first)


async def test_get_pipeline_404_for_unknown(client):
    resp = await client.get("/pipelines/does_not_exist")
    assert resp.status_code == 404
