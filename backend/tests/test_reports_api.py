"""Tests for the SQL-only `/reports/*` endpoints (Phase 3 Slice C).

Each endpoint is exercised against a tmp store with a small hand-rolled
ledger spanning two periods and mixed cash/accrual basis. We assert
shape, integer-cents enforcement, and totals correctness.
"""
from __future__ import annotations

import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from backend.api.reports import router as reports_router
from backend.api.runs import router as runs_router
from backend.orchestration.store.writes import write_tx


@pytest_asyncio.fixture
async def app(store):
    a = FastAPI()
    a.state.store = store
    a.include_router(runs_router)
    a.include_router(reports_router)
    return a


@pytest_asyncio.fixture
async def client(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac


async def _seed_chart_account(
    store, code: str, name: str = "Test", coa_type: str = "expense", parent: str | None = None,
) -> None:
    async with write_tx(store.accounting, store.accounting_lock) as conn:
        await conn.execute(
            "INSERT OR IGNORE INTO chart_of_accounts (code, name, type, parent) "
            "VALUES (?, ?, ?, ?)",
            (code, name, coa_type, parent),
        )


async def _seed_entry(
    store,
    *,
    entry_date: str,
    basis: str = "accrual",
    status: str = "posted",
    lines: list[tuple[str, int, int]],
) -> int:
    """Insert an entry with the given (account_code, debit, credit) lines."""
    async with write_tx(store.accounting, store.accounting_lock) as conn:
        cur = await conn.execute(
            "INSERT INTO journal_entries "
            "(basis, entry_date, source_pipeline, source_run_id, status) "
            "VALUES (?, ?, 'test', 1, ?)",
            (basis, entry_date, status),
        )
        entry_id = int(cur.lastrowid)
        for code, debit, credit in lines:
            await conn.execute(
                "INSERT INTO journal_lines (entry_id, account_code, debit_cents, credit_cents) "
                "VALUES (?, ?, ?, ?)",
                (entry_id, code, debit, credit),
            )
    return entry_id


async def _seed_basic_ledger(store) -> None:
    """Hand-rolled minimal ledger across Q1 and Q2 2026."""
    # Revenue entry (accrual, Q1): 100€ → AR + revenue
    await _seed_entry(
        store, entry_date="2026-01-15", basis="accrual",
        lines=[("411", 10000, 0), ("706000", 0, 10000)],
    )
    # Expense entry (accrual, Q1): 50€ + 10€ VAT → expense + VAT_in / AP
    await _seed_entry(
        store, entry_date="2026-02-10", basis="accrual",
        lines=[("626100", 5000, 0), ("4456", 1000, 0), ("401", 0, 6000)],
    )
    # Cash payment (cash, Q2): bank → AP
    await _seed_entry(
        store, entry_date="2026-04-05", basis="cash",
        lines=[("401", 6000, 0), ("512", 0, 6000)],
    )
    # Customer paid AR (cash, Q2)
    await _seed_entry(
        store, entry_date="2026-04-20", basis="cash",
        lines=[("512", 10000, 0), ("411", 0, 10000)],
    )


# --------------------------------------------------------------------------- #
# Trial balance
# --------------------------------------------------------------------------- #

async def test_trial_balance_empty_returns_zero_totals(client):
    resp = await client.get("/reports/trial_balance?as_of=2026-12-31")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["currency"] == "EUR"
    assert body["basis"] == "cash"
    # Even with empty ledger we list every CoA row.
    assert isinstance(body["lines"], list)
    assert body["totals"]["total_debit_cents"] == 0
    assert body["totals"]["total_credit_cents"] == 0
    assert body["totals"]["balanced"] is True


async def test_trial_balance_balances(store, client):
    await _seed_basic_ledger(store)
    resp = await client.get("/reports/trial_balance?as_of=2026-12-31&basis=accrual")
    assert resp.status_code == 200
    body = resp.json()
    assert body["totals"]["balanced"] is True
    # Money is integer cents.
    for line in body["lines"]:
        assert isinstance(line["total_debit_cents"], int)
        assert isinstance(line["total_credit_cents"], int)


async def test_trial_balance_basis_filter(store, client):
    """Accrual basis surfaces revenue + AR; cash basis does not."""
    await _seed_basic_ledger(store)
    resp_a = await client.get("/reports/trial_balance?as_of=2026-12-31&basis=accrual")
    resp_c = await client.get("/reports/trial_balance?as_of=2026-12-31&basis=cash")
    lines_a = {l["code"]: l for l in resp_a.json()["lines"]}
    lines_c = {l["code"]: l for l in resp_c.json()["lines"]}
    # Revenue 706000 only present (non-zero) on accrual basis.
    assert lines_a["706000"]["total_credit_cents"] > 0
    assert lines_c["706000"]["total_credit_cents"] == 0
    # Bank 512 only moves on cash basis.
    assert lines_a["512"]["total_debit_cents"] == 0
    assert lines_c["512"]["total_debit_cents"] > 0


# --------------------------------------------------------------------------- #
# Balance sheet
# --------------------------------------------------------------------------- #

async def test_balance_sheet_provisional_flag(store, client):
    await _seed_basic_ledger(store)
    resp = await client.get("/reports/balance_sheet?as_of=2026-04-30&basis=accrual")
    assert resp.status_code == 200
    body = resp.json()
    # Has revenue / expense activity → provisional retained earnings.
    assert body["provisional"] is True
    assert any(
        e["code"] == "_provisional_re" for e in body["sections"]["equity"]
    )
    # Sections present.
    assert "assets" in body["sections"]
    assert "liabilities" in body["sections"]
    assert "equity" in body["sections"]


async def test_balance_sheet_balances(store, client):
    """Assets = Liabilities + Equity (incl. provisional retained earnings)."""
    await _seed_basic_ledger(store)
    resp = await client.get("/reports/balance_sheet?as_of=2026-04-30")
    body = resp.json()
    assert body["totals"]["balanced"] is True


# --------------------------------------------------------------------------- #
# Income statement
# --------------------------------------------------------------------------- #

async def test_income_statement_empty(client):
    resp = await client.get(
        "/reports/income_statement?from=2026-01-01&to=2026-12-31"
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["currency"] == "EUR"
    assert body["totals"]["total_revenue_cents"] == 0
    assert body["totals"]["total_expense_cents"] == 0
    assert body["totals"]["net_income_cents"] == 0


async def test_income_statement_period(store, client):
    await _seed_basic_ledger(store)
    resp = await client.get(
        "/reports/income_statement?from=2026-01-01&to=2026-12-31&basis=accrual"
    )
    body = resp.json()
    # Revenue 100€, expense 50€ → net income 50€.
    assert body["totals"]["total_revenue_cents"] == 10000
    assert body["totals"]["total_expense_cents"] == 5000
    assert body["totals"]["net_income_cents"] == 5000


# --------------------------------------------------------------------------- #
# Cashflow
# --------------------------------------------------------------------------- #

async def test_cashflow_aggregates_by_section(store, client):
    await _seed_basic_ledger(store)
    resp = await client.get(
        "/reports/cashflow?from=2026-01-01&to=2026-12-31"
    )
    assert resp.status_code == 200
    body = resp.json()
    # We have exactly two cash entries: AR collection (operating) and AP
    # payment (financing — liability). Net change should be zero.
    sections = body["sections"]
    assert isinstance(sections["operating_cents"], int)
    assert isinstance(sections["investing_cents"], int)
    assert isinstance(sections["financing_cents"], int)
    assert (
        sections["operating_cents"] + sections["investing_cents"] + sections["financing_cents"]
        == body["totals"]["net_change_cents"]
    )


# --------------------------------------------------------------------------- #
# Budget vs actuals
# --------------------------------------------------------------------------- #

async def test_budget_vs_actuals_returns_envelopes(client):
    # Migration 0007 seeds 60 envelopes (4 scopes × 5 categories × 3 periods).
    resp = await client.get("/reports/budget_vs_actuals?period=2026-04")
    assert resp.status_code == 200
    body = resp.json()
    assert body["currency"] == "EUR"
    # 4 scopes × 5 categories = 20 envelopes for the period.
    assert len(body["lines"]) == 20
    for line in body["lines"]:
        assert isinstance(line["cap_cents"], int)
        assert isinstance(line["used_cents"], int)
        assert line["remaining_cents"] == line["cap_cents"] - line["used_cents"]


async def test_budget_vs_actuals_empty_period(client):
    resp = await client.get("/reports/budget_vs_actuals?period=2099-01")
    assert resp.status_code == 200
    body = resp.json()
    assert body["lines"] == []
    assert body["totals"]["total_cap_cents"] == 0


# --------------------------------------------------------------------------- #
# VAT return
# --------------------------------------------------------------------------- #

async def test_vat_return_with_seeded_rates(store, client):
    """Migration 0009 seeds vat_rates for `4456` (deductible) and `445`
    (collected). With our basic ledger, the Feb expense has 1000c VAT
    deductible, and there is no collected VAT (no sales-tax line)."""
    await _seed_basic_ledger(store)
    resp = await client.get("/reports/vat_return?period=2026-02")
    assert resp.status_code == 200
    body = resp.json()
    assert body["currency"] == "EUR"
    # Deductible 1000c on `4456`; collected 0.
    assert body["totals"]["deductible_cents"] == 1000
    assert body["totals"]["collected_cents"] == 0
    assert body["totals"]["net_due_cents"] == -1000  # we get money back


async def test_vat_return_period_format(client):
    resp = await client.get("/reports/vat_return?period=2026-99-99")
    assert resp.status_code == 422


async def test_vat_return_empty_period(client):
    resp = await client.get("/reports/vat_return?period=2099-01")
    assert resp.status_code == 200
    body = resp.json()
    # vat_rates exists (seeded by 0009) but no journal_lines in this period.
    assert body["totals"]["collected_cents"] == 0
    assert body["totals"]["deductible_cents"] == 0
