"""Tests for `GET /journal_entries` and `GET /envelopes`.

Source: `Orchestration/Plans/phase-2-list-endpoints-and-frontend.md` Task 3.
"""
from __future__ import annotations

import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

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


async def _seed_chart_account(store, code: str = "626100") -> None:
    async with write_tx(store.accounting, store.accounting_lock) as conn:
        await conn.execute(
            "INSERT OR IGNORE INTO chart_of_accounts (code, name, type) "
            "VALUES (?, ?, 'expense')",
            (code, f"Account {code}"),
        )


async def _seed_basic_entry(
    store,
    *,
    entry_date: str = "2026-04-15",
    status: str = "posted",
    debit_code: str = "626100",
    credit_code: str = "401",
    amount_cents: int = 50000,
) -> int:
    """Insert a journal_entry with one debit + one credit line. Returns entry_id."""
    await _seed_chart_account(store, debit_code)
    await _seed_chart_account(store, credit_code)
    async with write_tx(store.accounting, store.accounting_lock) as conn:
        cur = await conn.execute(
            "INSERT INTO journal_entries "
            "(basis, entry_date, source_pipeline, source_run_id, status) "
            "VALUES ('cash', ?, 'transaction_booked', 1, ?)",
            (entry_date, status),
        )
        entry_id = int(cur.lastrowid)
        await conn.execute(
            "INSERT INTO journal_lines (entry_id, account_code, debit_cents, credit_cents) "
            "VALUES (?, ?, ?, 0)",
            (entry_id, debit_code, amount_cents),
        )
        await conn.execute(
            "INSERT INTO journal_lines (entry_id, account_code, debit_cents, credit_cents) "
            "VALUES (?, ?, 0, ?)",
            (entry_id, credit_code, amount_cents),
        )
    return entry_id


# --------------------------------------------------------------------------- #
# GET /journal_entries
# --------------------------------------------------------------------------- #

async def test_list_journal_entries_default_pagination(store, client):
    for i in range(60):
        await _seed_basic_entry(store, entry_date=f"2026-04-{(i % 28) + 1:02d}")
    resp = await client.get("/journal_entries")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["limit"] == 50
    assert body["offset"] == 0
    assert body["total"] == 60
    assert len(body["items"]) == 50
    # newest-first: ids descend
    ids = [item["id"] for item in body["items"]]
    assert ids == sorted(ids, reverse=True)
    # money is integer cents
    for item in body["items"]:
        assert isinstance(item["total_cents"], int)
        assert item["total_cents"] == 50000
        assert item["line_count"] == 2


async def test_list_journal_entries_offset_paging(store, client):
    for _ in range(15):
        await _seed_basic_entry(store)
    resp1 = await client.get("/journal_entries?limit=10&offset=0")
    resp2 = await client.get("/journal_entries?limit=10&offset=10")
    assert resp1.status_code == 200
    assert resp2.status_code == 200
    ids1 = [i["id"] for i in resp1.json()["items"]]
    ids2 = [i["id"] for i in resp2.json()["items"]]
    assert set(ids1).isdisjoint(set(ids2))
    assert resp2.json()["total"] == 15
    # offset=10 returns the remaining 5
    assert len(ids2) == 5


async def test_list_journal_entries_status_filter(store, client):
    await _seed_basic_entry(store, status="posted")
    await _seed_basic_entry(store, status="review")
    resp = await client.get("/journal_entries?status=review")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    assert all(i["status"] == "review" for i in body["items"])


async def test_list_journal_entries_invalid_limit(client):
    resp = await client.get("/journal_entries?limit=999")
    assert resp.status_code == 422


# --------------------------------------------------------------------------- #
# GET /envelopes
# --------------------------------------------------------------------------- #

async def test_list_envelopes_employee_filter(store, client):
    # Seed migration 0007 inserts envelopes for employee_id=1,2,3 + company,
    # for periods 2026-02..04, across 5 categories (60 rows total).
    resp = await client.get("/envelopes?employee_id=1&period=2026-04")
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert len(items) == 5  # 5 categories
    for item in items:
        assert item["scope_kind"] == "employee"
        assert item["scope_id"] == 1
        assert item["period"] == "2026-04"
        assert isinstance(item["cap_cents"], int)
        assert isinstance(item["used_cents"], int)
        assert item["used_cents"] == 0  # no allocations seeded


async def test_list_envelopes_used_cents_rolled_up(store, client):
    # Pick the ai_tokens envelope for employee 1, period 2026-04, then insert
    # a journal entry + a budget_allocations row.
    await _seed_chart_account(store, "626100")
    async with write_tx(store.accounting, store.accounting_lock) as conn:
        cur = await conn.execute(
            "SELECT id FROM budget_envelopes WHERE scope_kind='employee' AND scope_id=1 "
            "AND category='ai_tokens' AND period='2026-04'"
        )
        envelope_id = (await cur.fetchone())[0]
        cur = await conn.execute(
            "INSERT INTO journal_entries "
            "(basis, entry_date, source_pipeline, source_run_id, status) "
            "VALUES ('cash','2026-04-10','transaction_booked',1,'posted')"
        )
        entry_id = int(cur.lastrowid)
        cur = await conn.execute(
            "INSERT INTO journal_lines (entry_id, account_code, debit_cents, credit_cents) "
            "VALUES (?, '626100', 5000, 0)",
            (entry_id,),
        )
        line_id = int(cur.lastrowid)
        await conn.execute(
            "INSERT INTO budget_allocations (envelope_id, line_id, amount_cents) "
            "VALUES (?, ?, 5000)",
            (envelope_id, line_id),
        )
    resp = await client.get("/envelopes?employee_id=1&period=2026-04")
    items = {i["category"]: i for i in resp.json()["items"]}
    assert items["ai_tokens"]["used_cents"] == 5000
    assert items["ai_tokens"]["allocation_count"] == 1


async def test_list_envelopes_handles_negative_allocation_for_reversal(store, client):
    """Net usage = 0 after a reversal allocation (negative amount_cents)."""
    await _seed_chart_account(store, "6257")
    async with write_tx(store.accounting, store.accounting_lock) as conn:
        cur = await conn.execute(
            "SELECT id FROM budget_envelopes WHERE scope_kind='employee' AND scope_id=2 "
            "AND category='food' AND period='2026-04'"
        )
        envelope_id = (await cur.fetchone())[0]
        cur = await conn.execute(
            "INSERT INTO journal_entries "
            "(basis, entry_date, source_pipeline, source_run_id, status) "
            "VALUES ('cash','2026-04-10','transaction_booked',1,'posted')"
        )
        e1 = int(cur.lastrowid)
        cur = await conn.execute(
            "INSERT INTO journal_lines (entry_id, account_code, debit_cents, credit_cents) "
            "VALUES (?, '6257', 1500, 0)",
            (e1,),
        )
        l1 = int(cur.lastrowid)
        await conn.execute(
            "INSERT INTO budget_allocations (envelope_id, line_id, amount_cents) "
            "VALUES (?, ?, 1500)",
            (envelope_id, l1),
        )
        # reversal: positive line + negative allocation
        cur = await conn.execute(
            "INSERT INTO journal_entries "
            "(basis, entry_date, source_pipeline, source_run_id, status, reversal_of_id) "
            "VALUES ('cash','2026-04-11','transaction_released',2,'posted',?)",
            (e1,),
        )
        e2 = int(cur.lastrowid)
        cur = await conn.execute(
            "INSERT INTO journal_lines (entry_id, account_code, debit_cents, credit_cents) "
            "VALUES (?, '6257', 0, 1500)",
            (e2,),
        )
        l2 = int(cur.lastrowid)
        await conn.execute(
            "INSERT INTO budget_allocations (envelope_id, line_id, amount_cents) "
            "VALUES (?, ?, -1500)",
            (envelope_id, l2),
        )
    resp = await client.get("/envelopes?employee_id=2&period=2026-04")
    items = {i["category"]: i for i in resp.json()["items"]}
    assert items["food"]["used_cents"] == 0
    assert items["food"]["allocation_count"] == 2


async def test_list_envelopes_invalid_period_format(client):
    """Pydantic regex validates YYYY-MM format."""
    resp = await client.get("/envelopes?employee_id=1&period=2026-99-99")
    assert resp.status_code == 422


async def test_list_envelopes_unknown_employee_returns_empty(client):
    """Filter that matches nothing returns an empty items list (200, not 404)."""
    resp = await client.get("/envelopes?employee_id=999&period=2026-04")
    assert resp.status_code == 200
    assert resp.json()["items"] == []
