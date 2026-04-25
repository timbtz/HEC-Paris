"""Tests for `GET /journal_entries/{id}/trace` — auditable drilldown.

Source: RealMetaPRD §10. Walks all three DBs (accounting, audit,
orchestration) to assemble an in-Python merged view.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

import backend.orchestration  # noqa: F401
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


async def _seed_entry_with_traces(store) -> tuple[int, int, int]:
    """Insert a journal_entry + 2 lines + 1 audit decision + trace pointing at it.

    Returns (entry_id, line_id, decision_id).
    """
    today = datetime.now(timezone.utc).date().isoformat()

    async with write_tx(store.accounting, store.accounting_lock) as conn:
        await conn.execute(
            "INSERT OR IGNORE INTO chart_of_accounts (code, name, type) "
            "VALUES (?, ?, ?)",
            ("626100", "Test Account", "expense"),
        )
        await conn.execute(
            "INSERT OR IGNORE INTO chart_of_accounts (code, name, type) "
            "VALUES (?, ?, ?)",
            ("401000", "Suppliers", "liability"),
        )

    # Pretend a pipeline run exists in orchestration so source_run lookup works.
    async with write_tx(store.orchestration, store.orchestration_lock) as conn:
        cur = await conn.execute(
            "INSERT INTO pipeline_runs "
            "(pipeline_name, pipeline_version, trigger_source, trigger_payload, status) "
            "VALUES (?, ?, ?, ?, ?)",
            ("document_ingested", 1, "document.uploaded", '{"document_id":1}', "completed"),
        )
        run_id = int(cur.lastrowid)

    # An audit decision the trace will reference logically.
    async with write_tx(store.audit, store.audit_lock) as conn:
        cur = await conn.execute(
            "INSERT INTO agent_decisions "
            "(run_id_logical, node_id, source, runner, model, prompt_hash, confidence) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (run_id, "classify-gl-account", "agent", "anthropic",
             "claude-haiku-4-5", "phash_abc", 0.92),
        )
        decision_id = int(cur.lastrowid)

        await conn.execute(
            "INSERT INTO agent_costs "
            "(decision_id, employee_id, provider, model, "
            " input_tokens, output_tokens, cost_micro_usd) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (decision_id, 1, "anthropic", "claude-haiku-4-5", 100, 30, 1500),
        )

    async with write_tx(store.accounting, store.accounting_lock) as conn:
        cur = await conn.execute(
            "INSERT INTO journal_entries "
            "(basis, entry_date, description, source_pipeline, source_run_id, status) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("accrual", today, "Test entry", "document_ingested", run_id, "posted"),
        )
        entry_id = int(cur.lastrowid)

        cur = await conn.execute(
            "INSERT INTO journal_lines "
            "(entry_id, account_code, debit_cents, credit_cents) "
            "VALUES (?, ?, ?, ?)",
            (entry_id, "626100", 1200, 0),
        )
        line_id = int(cur.lastrowid)

        cur = await conn.execute(
            "INSERT INTO journal_lines "
            "(entry_id, account_code, debit_cents, credit_cents) "
            "VALUES (?, ?, ?, ?)",
            (entry_id, "401000", 0, 1200),
        )
        line2_id = int(cur.lastrowid)

        await conn.execute(
            "INSERT INTO decision_traces "
            "(line_id, source, confidence, agent_decision_id_logical) "
            "VALUES (?, 'agent', 0.92, ?)",
            (line_id, str(decision_id)),
        )
        await conn.execute(
            "INSERT INTO decision_traces (line_id, source, confidence) "
            "VALUES (?, 'rule', 1.0)",
            (line2_id,),
        )

    return entry_id, line_id, decision_id


async def test_trace_returns_joined_shape(client, store):
    entry_id, line_id, decision_id = await _seed_entry_with_traces(store)

    resp = await client.get(f"/journal_entries/{entry_id}/trace")
    assert resp.status_code == 200, resp.text
    body = resp.json()

    # Top-level keys per RealMetaPRD §10 drilldown contract.
    for key in (
        "entry", "lines", "traces", "agent_decisions", "agent_costs",
        "source_run", "swan_transactions", "documents",
    ):
        assert key in body, f"missing key: {key}"

    assert body["entry"]["id"] == entry_id
    assert len(body["lines"]) == 2
    assert any(int(line["id"]) == line_id for line in body["lines"])
    assert len(body["traces"]) == 2

    # The decision the trace points at is hydrated from audit DB.
    assert len(body["agent_decisions"]) == 1
    assert body["agent_decisions"][0]["id"] == decision_id
    assert len(body["agent_costs"]) == 1
    assert body["agent_costs"][0]["decision_id"] == decision_id

    # source_run is the orchestration row we hand-seeded.
    assert body["source_run"] is not None
    assert body["source_run"]["pipeline_name"] == "document_ingested"

    # No documents/swan_tx attached → empty lists.
    assert body["swan_transactions"] == []
    assert body["documents"] == []


async def test_trace_404_unknown_entry(client):
    resp = await client.get("/journal_entries/9999/trace")
    assert resp.status_code == 404
