"""Unit tests for `tools/swan_query.py`.

Strategy: monkey-patch `_get_client` to return a fake `SwanGraphQLClient`
with predictable `fetch_transaction` / `fetch_account` methods. Asserts
the row lands in `accounting.swan_transactions` via INSERT OR REPLACE.
"""
from __future__ import annotations

import json
from typing import Any

import pytest

from backend.orchestration import context as context_module
from backend.orchestration.tools import swan_query


class _FakeClient:
    """Stand-in for `SwanGraphQLClient` that returns a fixed dict."""

    def __init__(self, tx: dict[str, Any] | None = None, account: dict[str, Any] | None = None) -> None:
        self._tx = tx
        self._account = account
        self.tx_calls: list[str] = []
        self.account_calls: list[str] = []

    async def fetch_transaction(self, tx_id: str) -> dict[str, Any]:
        self.tx_calls.append(tx_id)
        if self._tx is None:
            raise LookupError(f"no fixture for {tx_id}")
        return self._tx

    async def fetch_account(self, account_id: str) -> dict[str, Any]:
        self.account_calls.append(account_id)
        if self._account is None:
            raise LookupError(f"no fixture for {account_id}")
        return self._account


def _make_ctx(store, *, trigger_payload: dict[str, Any], node_outputs: dict[str, Any] | None = None):
    return context_module.AgnesContext(
        run_id=1,
        pipeline_name="test",
        trigger_source="test",
        trigger_payload=trigger_payload,
        node_outputs=node_outputs or {},
        store=store,
        employee_id=None,
    )


async def _swan_tx_row(store, tx_id: str) -> tuple[Any, ...] | None:
    cur = await store.accounting.execute(
        "SELECT id, swan_event_id, side, type, status, amount_cents, currency, "
        "counterparty_label, payment_reference, execution_date, "
        "booked_balance_after, raw FROM swan_transactions WHERE id = ?",
        (tx_id,),
    )
    row = await cur.fetchone()
    await cur.close()
    return tuple(row) if row else None


# --------------------------------------------------------------------------- #
# fetch_transaction
# --------------------------------------------------------------------------- #


async def test_fetch_transaction_persists_row(monkeypatch: pytest.MonkeyPatch, store):
    sample = {
        "id": "tx-test-001",
        "type": "SepaCreditTransferOut",
        "side": "Debit",
        "status": "Booked",
        "amount": {"value": "12.50", "currency": "EUR"},
        "counterparty": {"name": "Anthropic", "iban": "FR7610278060610001020480101"},
        "executionDate": "2026-04-01",
        "bookedBalanceAfter": 1234567,
        "paymentReference": "ref-1",
        "account": {"id": "acc_demo_company"},
    }
    fake = _FakeClient(tx=sample)
    monkeypatch.setattr(swan_query, "_get_client", lambda: fake)

    ctx = _make_ctx(
        store,
        trigger_payload={"resourceId": "tx-test-001", "eventId": "evt-1"},
    )

    out = await swan_query.fetch_transaction(ctx)

    assert fake.tx_calls == ["tx-test-001"]
    assert out["__local_id"] == "tx-test-001"
    assert out["id"] == "tx-test-001"

    row = await _swan_tx_row(store, "tx-test-001")
    assert row is not None
    (
        rid, swan_event_id, side, ttype, status, amount_cents,
        currency, cp_label, payment_ref, exec_date, balance, raw_json,
    ) = row
    assert rid == "tx-test-001"
    assert swan_event_id == "evt-1"
    assert side == "Debit"
    assert ttype == "SepaCreditTransferOut"
    assert status == "Booked"
    assert amount_cents == 1250
    assert currency == "EUR"
    assert cp_label == "Anthropic"
    assert payment_ref == "ref-1"
    assert exec_date == "2026-04-01"
    assert balance == 1234567
    assert json.loads(raw_json)["id"] == "tx-test-001"


async def test_fetch_transaction_defensive_defaults(monkeypatch: pytest.MonkeyPatch, store):
    """Partial payload shouldn't crash the persist (NOT-NULL columns get defaults)."""
    sample = {"id": "tx-partial-1"}
    fake = _FakeClient(tx=sample)
    monkeypatch.setattr(swan_query, "_get_client", lambda: fake)

    ctx = _make_ctx(store, trigger_payload={"id": "tx-partial-1"})
    out = await swan_query.fetch_transaction(ctx)

    assert out["__local_id"] == "tx-partial-1"
    row = await _swan_tx_row(store, "tx-partial-1")
    assert row is not None
    # Defaults: side='Debit', type='Unknown', status='Booked',
    # amount_cents=0, currency='EUR', execution_date='1970-01-01'.
    assert row[2] == "Debit"
    assert row[3] == "Unknown"
    assert row[4] == "Booked"
    assert row[5] == 0
    assert row[6] == "EUR"
    assert row[9] == "1970-01-01"


async def test_fetch_transaction_uses_id_fallback(monkeypatch: pytest.MonkeyPatch, store):
    """`id` is the fallback when `resourceId` isn't present."""
    fake = _FakeClient(tx={"id": "tx-fallback", "type": "Fees", "side": "Debit",
                           "status": "Booked", "amount": {"value": "1.00", "currency": "EUR"},
                           "executionDate": "2026-04-02"})
    monkeypatch.setattr(swan_query, "_get_client", lambda: fake)

    ctx = _make_ctx(store, trigger_payload={"id": "tx-fallback"})
    await swan_query.fetch_transaction(ctx)

    assert fake.tx_calls == ["tx-fallback"]
    row = await _swan_tx_row(store, "tx-fallback")
    assert row is not None


async def test_fetch_transaction_raises_when_no_id(monkeypatch: pytest.MonkeyPatch, store):
    fake = _FakeClient(tx={})
    monkeypatch.setattr(swan_query, "_get_client", lambda: fake)
    ctx = _make_ctx(store, trigger_payload={})
    with pytest.raises(ValueError):
        await swan_query.fetch_transaction(ctx)


# --------------------------------------------------------------------------- #
# fetch_account
# --------------------------------------------------------------------------- #


async def test_fetch_account_reads_from_chained_node(monkeypatch: pytest.MonkeyPatch, store):
    fake = _FakeClient(account={"id": "acc-1", "IBAN": "FR76..."})
    monkeypatch.setattr(swan_query, "_get_client", lambda: fake)

    ctx = _make_ctx(
        store,
        trigger_payload={},
        node_outputs={"fetch-transaction": {"account": {"id": "acc-1"}}},
    )
    out = await swan_query.fetch_account(ctx)

    assert fake.account_calls == ["acc-1"]
    assert out["id"] == "acc-1"


async def test_fetch_account_falls_back_to_trigger(monkeypatch: pytest.MonkeyPatch, store):
    fake = _FakeClient(account={"id": "acc-2"})
    monkeypatch.setattr(swan_query, "_get_client", lambda: fake)

    ctx = _make_ctx(store, trigger_payload={"account_id": "acc-2"})
    out = await swan_query.fetch_account(ctx)

    assert fake.account_calls == ["acc-2"]
    assert out["id"] == "acc-2"
