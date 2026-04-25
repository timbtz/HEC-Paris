"""End-to-end test for `backend/scripts/replay_swan_seed.py`.

Strategy:
1. Trim the auto-applied seed (200 rows from migration 0006) down to a
   handful of deterministic rows to fit in pytest-timeout's 15s budget.
2. Mount the real `swan_webhook` router on a FastAPI test app and
   monkey-patch `httpx.AsyncClient` inside the script module to use
   `ASGITransport` so the script's POSTs hit the test app.
3. Run `main()` and assert the script's outcome counters are
   reasonable; assert idempotency on a second run.

We do NOT assert exact `journal_entries` counts because the production
pipeline fans out to agent fallbacks for unrecognized counterparties; we
test the replay *plumbing*, not pipeline coverage.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx
import pytest_asyncio
from fastapi import FastAPI

from backend.api.swan_webhook import router as swan_router
from backend.orchestration import executor as executor_mod
from backend.orchestration.store.writes import write_tx
from backend.scripts import replay_swan_seed


_SAMPLE_TX_IDS = [f"tx-replay-{i:02d}" for i in range(3)]


@pytest_asyncio.fixture
async def app(store):
    a = FastAPI()
    a.state.store = store
    a.include_router(swan_router)
    return a


async def _trim_swan_transactions(store) -> None:
    """Replace the auto-seeded 200 rows with a tiny deterministic subset."""
    async with write_tx(store.accounting, store.accounting_lock) as conn:
        await conn.execute("DELETE FROM swan_transactions")
        for i, tx_id in enumerate(_SAMPLE_TX_IDS):
            raw = {
                "id": tx_id,
                "type": "Fees",
                "side": "Debit",
                "amount": {"value": 1.50, "currency": "EUR"},
                "executionDate": f"2026-04-{10 + i:02d}",
                "counterparty": {"name": "Swan"},
                "bookedBalanceAfter": 5000000 - (i + 1) * 150,
            }
            await conn.execute(
                "INSERT INTO swan_transactions "
                "(id, swan_event_id, side, type, status, amount_cents, currency, "
                " counterparty_label, payment_reference, execution_date, "
                " booked_balance_after, raw) "
                "VALUES (?, ?, 'Debit', 'Fees', 'Booked', 150, 'EUR', "
                "        'Swan', NULL, ?, ?, ?)",
                (tx_id, f"evt-{tx_id}", f"2026-04-{10 + i:02d}",
                 5000000 - (i + 1) * 150, json.dumps(raw)),
            )


async def _await_pending_runs(timeout_s: float = 8.0) -> None:
    """Wait for all background pipeline runs scheduled by the webhook handler."""
    deadline = asyncio.get_event_loop().time() + timeout_s
    while executor_mod._run_tasks and asyncio.get_event_loop().time() < deadline:
        tasks = list(executor_mod._run_tasks.values())
        for t in tasks:
            remaining = deadline - asyncio.get_event_loop().time()
            if remaining <= 0:
                break
            try:
                await asyncio.wait_for(t, timeout=remaining)
            except (asyncio.TimeoutError, BaseException):
                pass


def _patch_httpx_to_app(monkeypatch, app: FastAPI) -> None:
    """Make `httpx.AsyncClient()` (no args) return one bound to `app`."""
    orig_ctor = httpx.AsyncClient

    class _ASGIClient(orig_ctor):  # type: ignore[misc, valid-type]
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            kwargs.setdefault("transport", httpx.ASGITransport(app=app))
            kwargs.setdefault("base_url", "http://test")
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(replay_swan_seed.httpx, "AsyncClient", _ASGIClient)


async def test_replay_iterates_and_posts(monkeypatch, store, app, tmp_path):
    monkeypatch.setenv("SWAN_WEBHOOK_SECRET", "")
    monkeypatch.setenv("AGNES_SWAN_LOCAL_REPLAY", "1")
    monkeypatch.delenv("SWAN_CLIENT_ID", raising=False)
    _patch_httpx_to_app(monkeypatch, app)
    await _trim_swan_transactions(store)

    summary = await replay_swan_seed.main(
        host="dummy", port=8000, data_dir=tmp_path,
    )

    assert summary["total"] == len(_SAMPLE_TX_IDS)
    # Every row should be either posted or already-skipped (no failed).
    # Allow `posted` ∈ [0, total] because some pipelines may fail in test
    # without external services — we only want to confirm the wire-up.
    assert summary["failed"] == 0, summary

    await _await_pending_runs()


async def test_replay_is_idempotent(monkeypatch, store, app, tmp_path):
    monkeypatch.setenv("SWAN_WEBHOOK_SECRET", "")
    monkeypatch.setenv("AGNES_SWAN_LOCAL_REPLAY", "1")
    monkeypatch.delenv("SWAN_CLIENT_ID", raising=False)
    _patch_httpx_to_app(monkeypatch, app)
    await _trim_swan_transactions(store)

    await replay_swan_seed.main(host="dummy", port=8000, data_dir=tmp_path)
    await _await_pending_runs()

    cur = await store.orchestration.execute(
        "SELECT COUNT(*) FROM external_events WHERE provider='swan'"
    )
    after_first = int((await cur.fetchone())[0])
    await cur.close()

    summary2 = await replay_swan_seed.main(
        host="dummy", port=8000, data_dir=tmp_path,
    )
    await _await_pending_runs()

    cur = await store.orchestration.execute(
        "SELECT COUNT(*) FROM external_events WHERE provider='swan'"
    )
    after_second = int((await cur.fetchone())[0])
    await cur.close()

    # external_events.UNIQUE(provider, event_id) — second run inserts none.
    assert after_second == after_first, (
        f"external_events not idempotent: {after_first} → {after_second}"
    )
    # The script should report `posted=0` on the second run because the
    # webhook returns `{status:"duplicate"}` for every row.
    assert summary2["posted"] == 0, summary2
