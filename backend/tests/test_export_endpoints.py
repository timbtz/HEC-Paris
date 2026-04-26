"""Tests for the three audit-pack export endpoints.

Covers:
- GET /reports/bank_reconciliation?period_code=YYYY-Qn       (CSV)
- GET /audit/decision_traces?from=&to=&format=jsonl|json     (JSONL/JSON)
- GET /wiki/snapshot[?as_of=YYYY-MM-DD]                       (markdown bundle)
"""
from __future__ import annotations

import csv
import io
import json

import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from backend.api.audit_traces import router as audit_traces_router
from backend.api.reports import router as reports_router
from backend.api.runs import router as runs_router
from backend.api.wiki import router as wiki_router
from backend.orchestration.store.writes import write_tx
from backend.orchestration.wiki.schema import WikiFrontmatter
from backend.orchestration.wiki.writer import upsert_page


@pytest_asyncio.fixture
async def app(store):
    a = FastAPI()
    a.state.store = store
    a.include_router(runs_router)
    a.include_router(reports_router)
    a.include_router(audit_traces_router)
    a.include_router(wiki_router)
    return a


@pytest_asyncio.fixture
async def client(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac


# --------------------------------------------------------------------------- #
# Bank reconciliation
# --------------------------------------------------------------------------- #


async def _seed_swan_tx(
    store,
    *,
    tx_id: str,
    execution_date: str,
    amount_cents: int,
    side: str = "Debit",
    tx_type: str = "CardOutDebit",
    status: str = "Booked",
    counterparty: str = "Anthropic",
) -> None:
    async with write_tx(store.accounting, store.accounting_lock) as conn:
        await conn.execute(
            "INSERT INTO swan_transactions "
            "(id, swan_event_id, side, type, status, amount_cents, currency, "
            " counterparty_label, execution_date, raw) "
            "VALUES (?, ?, ?, ?, ?, ?, 'EUR', ?, ?, '{}')",
            (
                tx_id, f"evt_{tx_id}", side, tx_type, status, amount_cents,
                counterparty, execution_date,
            ),
        )


async def _seed_posted_entry(
    store, *, entry_date: str, debit_account: str, credit_account: str, amount_cents: int,
) -> int:
    async with write_tx(store.accounting, store.accounting_lock) as conn:
        cur = await conn.execute(
            "INSERT INTO journal_entries "
            "(basis, entry_date, source_pipeline, source_run_id, status) "
            "VALUES ('cash', ?, 'test', 1, 'posted')",
            (entry_date,),
        )
        entry_id = int(cur.lastrowid)
        await conn.execute(
            "INSERT INTO journal_lines (entry_id, account_code, debit_cents, credit_cents) "
            "VALUES (?, ?, ?, 0)",
            (entry_id, debit_account, amount_cents),
        )
        await conn.execute(
            "INSERT INTO journal_lines (entry_id, account_code, debit_cents, credit_cents) "
            "VALUES (?, ?, 0, ?)",
            (entry_id, credit_account, amount_cents),
        )
    return entry_id


async def test_bank_reconciliation_csv_matched_and_unmatched(store, client):
    # 2026-Q1 is seeded as 'closed' by migration 0009; date range 2026-01-01..03-31.
    # tx_a will match a posted entry; tx_b will be unmatched.
    await _seed_swan_tx(store, tx_id="tx_a", execution_date="2026-01-15", amount_cents=12345)
    await _seed_swan_tx(store, tx_id="tx_b", execution_date="2026-02-10", amount_cents=999)

    await _seed_posted_entry(
        store, entry_date="2026-01-15", debit_account="626100", credit_account="512",
        amount_cents=12345,
    )

    resp = await client.get("/reports/bank_reconciliation?period_code=2026-Q1")
    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"].startswith("text/csv")
    assert "bank_reconciliation_2026-Q1.csv" in resp.headers["content-disposition"]

    reader = csv.DictReader(io.StringIO(resp.text))
    rows = list(reader)
    by_id = {r["swan_id"]: r for r in rows}
    assert reader.fieldnames is not None
    assert "matched" in reader.fieldnames
    assert "amount_cents" in reader.fieldnames

    assert by_id["tx_a"]["matched"] == "Y"
    assert by_id["tx_a"]["notes"] == "matched"
    assert by_id["tx_a"]["journal_entry_id"]  # non-empty

    assert by_id["tx_b"]["matched"] == "N"
    assert by_id["tx_b"]["notes"] == "no entry"
    assert by_id["tx_b"]["journal_entry_id"] == ""


async def test_bank_reconciliation_404_unknown_period(client):
    resp = await client.get("/reports/bank_reconciliation?period_code=2099-Q4")
    assert resp.status_code == 404


async def test_bank_reconciliation_invalid_period_code(client):
    resp = await client.get("/reports/bank_reconciliation?period_code=2026-01")
    # FastAPI Query pattern → 422
    assert resp.status_code == 422


# --------------------------------------------------------------------------- #
# Decision traces
# --------------------------------------------------------------------------- #


async def _seed_decision(
    store, *, started_at: str, run_id: int, node_id: str, model: str = "claude-sonnet",
) -> int:
    async with write_tx(store.audit, store.audit_lock) as conn:
        cur = await conn.execute(
            "INSERT INTO agent_decisions "
            "(run_id_logical, node_id, source, runner, model, response_id, "
            " confidence, latency_ms, finish_reason, started_at, completed_at) "
            "VALUES (?, ?, 'agent', 'anthropic', ?, 'resp_1', 0.9, 250, 'end_turn', ?, ?)",
            (run_id, node_id, model, started_at, started_at),
        )
        decision_id = int(cur.lastrowid)
        await conn.execute(
            "INSERT INTO agent_costs "
            "(decision_id, employee_id, provider, model, input_tokens, output_tokens, "
            " cache_read_tokens, cache_write_tokens, reasoning_tokens, cost_micro_usd) "
            "VALUES (?, NULL, 'anthropic', ?, 100, 50, 0, 0, 0, 12345)",
            (decision_id, model),
        )
    return decision_id


async def test_decision_traces_jsonl_default(store, client):
    await _seed_decision(store, started_at="2026-04-10T12:00:00Z", run_id=1, node_id="n1")
    await _seed_decision(store, started_at="2026-04-15T08:30:00Z", run_id=2, node_id="n2")
    await _seed_decision(store, started_at="2026-05-01T10:00:00Z", run_id=3, node_id="n3")

    resp = await client.get("/audit/decision_traces?from=2026-04-01&to=2026-04-30")
    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"].startswith("application/x-ndjson")
    assert "decision_traces_2026-04-01_2026-04-30.jsonl" in resp.headers["content-disposition"]

    lines = [ln for ln in resp.text.split("\n") if ln.strip()]
    assert len(lines) == 2  # only April rows
    parsed = [json.loads(ln) for ln in lines]
    for rec in parsed:
        assert "decision_id" in rec
        assert "node_id" in rec
        assert "started_at" in rec
        assert rec["cost"] is not None
        assert rec["cost"]["input_tokens"] == 100
        assert rec["cost"]["cost_micro_usd"] == 12345


async def test_decision_traces_json_format(store, client):
    await _seed_decision(store, started_at="2026-04-10T12:00:00Z", run_id=1, node_id="n1")

    resp = await client.get(
        "/audit/decision_traces?from=2026-04-01&to=2026-04-30&format=json"
    )
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("application/json")
    body = resp.json()
    assert isinstance(body, list)
    assert len(body) == 1
    assert body[0]["cost"]["cost_micro_usd"] == 12345


async def test_decision_traces_invalid_date(client):
    resp = await client.get("/audit/decision_traces?from=04/01/2026&to=2026-04-30")
    assert resp.status_code == 422


async def test_decision_traces_empty_range_ok(client):
    resp = await client.get("/audit/decision_traces?from=2099-01-01&to=2099-01-31")
    assert resp.status_code == 200
    assert resp.text == ""


# --------------------------------------------------------------------------- #
# Wiki snapshot
# --------------------------------------------------------------------------- #


async def test_wiki_snapshot_returns_markdown_bundle(store, client):
    fm = WikiFrontmatter(applies_to=["all"], revision=1)
    await upsert_page(
        store.orchestration, store.orchestration_lock,
        path="policies/a.md", title="Alpha policy",
        frontmatter=fm, body_md="# Alpha\n\nA body.", author="cfo",
    )
    await upsert_page(
        store.orchestration, store.orchestration_lock,
        path="policies/b.md", title="Beta policy",
        frontmatter=fm, body_md="# Beta\n\nB body.", author="cfo",
    )

    resp = await client.get("/wiki/snapshot")
    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"].startswith("text/markdown")
    assert "wiki_snapshot.md" in resp.headers["content-disposition"]

    body = resp.text
    assert "---" in body  # at least one separator between pages
    # Pages ordered by path ASC; "policies/a.md" appears before "policies/b.md".
    a_idx = body.index("policies/a.md")
    b_idx = body.index("policies/b.md")
    assert a_idx < b_idx
    assert "Alpha policy" in body
    assert "Beta policy" in body
    assert "A body." in body
    assert "B body." in body


async def test_wiki_snapshot_empty_returns_empty_body(client):
    resp = await client.get("/wiki/snapshot")
    assert resp.status_code == 200
    assert resp.text == ""


async def test_wiki_snapshot_invalid_as_of(client):
    resp = await client.get("/wiki/snapshot?as_of=not-a-date")
    assert resp.status_code == 422
