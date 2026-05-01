"""Gamification API + audit-hook tests.

Covers the wedge: every `audit.write_decision` produces an approved auto
task_completion that shows up on the leaderboard. Also covers the manual
self-declared loop (submit → manager approve → coins) and the redemption
balance bookkeeping (pending locks, reject refunds).
"""
from __future__ import annotations

import asyncio

import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from backend.api.gamification import router as gamification_router
from backend.orchestration import audit
from backend.orchestration.gamification import AUTO_COIN_REWARD, coin_balance
from backend.orchestration.runners.base import AgentResult, TokenUsage
from backend.orchestration.store.writes import write_tx


@pytest_asyncio.fixture
async def app(store):
    a = FastAPI()
    a.state.store = store
    a.include_router(gamification_router)
    return a


@pytest_asyncio.fixture
async def client(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac


# ──────────────────────────────────────────────────────────────────────────
# Migration + seed
# ──────────────────────────────────────────────────────────────────────────

async def test_seed_data_present(client):
    resp = await client.get("/gamification/tasks")
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert len(items) >= 9
    assert any(it["title"].startswith("CRM Lead") for it in items)

    resp = await client.get("/gamification/rewards")
    assert resp.status_code == 200
    assert len(resp.json()["items"]) >= 4


async def test_tim_is_seeded_manager(store):
    cur = await store.audit.execute(
        "SELECT is_manager FROM employees WHERE email = 'tim@hec.example'"
    )
    row = await cur.fetchone()
    await cur.close()
    assert row[0] == 1


# ──────────────────────────────────────────────────────────────────────────
# Auto-credit hook (the wedge)
# ──────────────────────────────────────────────────────────────────────────

async def _run_decision(store, employee_id):
    result = AgentResult(
        output={"answer": "ok"},
        model="claude-haiku-4-5",
        response_id="msg_test",
        prompt_hash="h",
        alternatives=None,
        confidence=0.9,
        usage=TokenUsage(input_tokens=10, output_tokens=5),
        latency_ms=42,
        finish_reason="end_turn",
        temperature=0.0,
        seed=None,
    )
    return await audit.propose_checkpoint_commit(
        audit_db=store.audit,
        audit_lock=store.audit_lock,
        run_id=42,
        node_id="test-node",
        result=result,
        runner="anthropic",
        employee_id=employee_id,
        provider="anthropic",
    )


async def test_auto_credit_creates_approved_completion(store):
    decision_id = await _run_decision(store, employee_id=1)
    cur = await store.audit.execute(
        "SELECT employee_id, status, coins_awarded, source, agent_decision_id "
        "FROM task_completions WHERE agent_decision_id = ?",
        (decision_id,),
    )
    row = await cur.fetchone()
    await cur.close()
    assert row is not None
    assert row[0] == 1
    assert row[1] == "approved"
    assert row[2] == AUTO_COIN_REWARD
    assert row[3] == "auto"


async def test_auto_credit_idempotent_on_replay(store):
    """Replaying the same decision_id never double-credits."""
    decision_id = await _run_decision(store, employee_id=1)
    # Manually call the hook again with the same decision_id — simulates a
    # caller mistakenly retrying inside a new write_tx.
    from backend.orchestration.gamification import auto_credit_for_decision
    async with write_tx(store.audit, store.audit_lock) as conn:
        await auto_credit_for_decision(
            conn, employee_id=1, agent_decision_id=decision_id, runner="anthropic",
        )
    cur = await store.audit.execute(
        "SELECT COUNT(*) FROM task_completions WHERE agent_decision_id = ?",
        (decision_id,),
    )
    n = (await cur.fetchone())[0]
    await cur.close()
    assert n == 1


async def test_auto_credit_skips_when_no_employee(store):
    """System-attributed calls (employee_id=None) don't credit anyone."""
    decision_id = await _run_decision(store, employee_id=None)
    cur = await store.audit.execute(
        "SELECT COUNT(*) FROM task_completions WHERE agent_decision_id = ?",
        (decision_id,),
    )
    n = (await cur.fetchone())[0]
    await cur.close()
    assert n == 0


async def test_balance_reflects_auto_credits(store):
    for _ in range(3):
        await _run_decision(store, employee_id=2)
    bal = await coin_balance(store.audit, 2)
    assert bal == 3 * AUTO_COIN_REWARD


# ──────────────────────────────────────────────────────────────────────────
# Manual completion flow
# ──────────────────────────────────────────────────────────────────────────

async def test_manual_completion_requires_author_header(client):
    resp = await client.post(
        "/gamification/completions",
        json={"task_id": 1, "note": "did it"},
    )
    assert resp.status_code == 400


async def test_manual_completion_then_approve(client, store):
    # Marie submits.
    resp = await client.post(
        "/gamification/completions",
        json={"task_id": 1, "note": "boom"},
        headers={"x-fingent-author": "marie@hec.example"},
    )
    assert resp.status_code == 200, resp.text
    completion_id = resp.json()["id"]

    # Marie can't approve her own (not a manager).
    resp = await client.post(
        f"/gamification/completions/{completion_id}/approve",
        headers={"x-fingent-author": "marie@hec.example"},
    )
    assert resp.status_code == 403

    # Tim approves.
    resp = await client.post(
        f"/gamification/completions/{completion_id}/approve",
        headers={"x-fingent-author": "tim@hec.example"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "approved"
    assert body["coins_awarded"] == 55  # task 1 = "Quarterly SaaS Burn-Rate" = 55

    bal = await coin_balance(store.audit, 2)
    assert bal == 55


async def test_double_approve_returns_409(client):
    resp = await client.post(
        "/gamification/completions",
        json={"task_id": 2},
        headers={"x-fingent-author": "marie@hec.example"},
    )
    cid = resp.json()["id"]
    await client.post(
        f"/gamification/completions/{cid}/approve",
        headers={"x-fingent-author": "tim@hec.example"},
    )
    resp = await client.post(
        f"/gamification/completions/{cid}/approve",
        headers={"x-fingent-author": "tim@hec.example"},
    )
    assert resp.status_code == 409


# ──────────────────────────────────────────────────────────────────────────
# Rewards / redemptions
# ──────────────────────────────────────────────────────────────────────────

async def _give_coins(store, employee_id, n_calls):
    for _ in range(n_calls):
        await _run_decision(store, employee_id=employee_id)


async def test_redemption_locks_then_refunds_on_reject(client, store):
    await _give_coins(store, 2, 20)  # 20 * 5 = 100 coins
    bal_before = await coin_balance(store.audit, 2)
    assert bal_before == 100

    # Marie redeems "Coffee on the house" (50 coins).
    resp = await client.post(
        "/gamification/redemptions",
        json={"reward_id": 1},
        headers={"x-fingent-author": "marie@hec.example"},
    )
    assert resp.status_code == 200, resp.text
    rid = resp.json()["id"]

    # Pending: balance is locked.
    bal_pending = await coin_balance(store.audit, 2)
    assert bal_pending == 50

    # Tim rejects → refund.
    resp = await client.post(
        f"/gamification/redemptions/{rid}/reject",
        headers={"x-fingent-author": "tim@hec.example"},
    )
    assert resp.status_code == 200
    bal_after = await coin_balance(store.audit, 2)
    assert bal_after == 100


async def test_redemption_insufficient_coins(client):
    # Marie has 0 coins and tries to redeem the 50-coin coffee.
    resp = await client.post(
        "/gamification/redemptions",
        json={"reward_id": 1},
        headers={"x-fingent-author": "marie@hec.example"},
    )
    assert resp.status_code == 409


# ──────────────────────────────────────────────────────────────────────────
# Leaderboard + today
# ──────────────────────────────────────────────────────────────────────────

async def test_leaderboard_orders_by_coins(client, store):
    await _give_coins(store, 1, 10)  # Tim: 50
    await _give_coins(store, 2, 3)   # Marie: 15
    await _give_coins(store, 3, 5)   # Paul: 25
    resp = await client.get("/gamification/leaderboard?period=all")
    assert resp.status_code == 200
    items = resp.json()["items"]
    # Active employees only (3); ordered by coins desc.
    by_email = {it["email"]: it["coins"] for it in items}
    assert by_email["tim@hec.example"] == 50
    assert by_email["paul@hec.example"] == 25
    assert by_email["marie@hec.example"] == 15
    coins_in_order = [it["coins"] for it in items]
    assert coins_in_order == sorted(coins_in_order, reverse=True)


async def test_today_summary_streak_is_one_after_credit(client, store):
    await _give_coins(store, 1, 2)
    resp = await client.get("/gamification/today/1")
    assert resp.status_code == 200
    body = resp.json()
    assert body["coins_today"] == 2 * AUTO_COIN_REWARD
    assert body["streak_days"] == 1
    assert body["daily_target"] == 100
    assert body["coins_balance"] == 2 * AUTO_COIN_REWARD


# ──────────────────────────────────────────────────────────────────────────
# Coin adjustments
# ──────────────────────────────────────────────────────────────────────────

async def test_coin_adjustment_credits_then_blocks_negative(client, store):
    resp = await client.post(
        "/gamification/coin_adjustments",
        json={"employee_id": 2, "amount": 250, "reason": "bonus"},
        headers={"x-fingent-author": "tim@hec.example"},
    )
    assert resp.status_code == 200
    assert resp.json()["new_balance"] == 250

    # Trying to debit more than balance → 409.
    resp = await client.post(
        "/gamification/coin_adjustments",
        json={"employee_id": 2, "amount": -1000, "reason": "clawback"},
        headers={"x-fingent-author": "tim@hec.example"},
    )
    assert resp.status_code == 409


async def test_non_manager_cannot_adjust(client):
    resp = await client.post(
        "/gamification/coin_adjustments",
        json={"employee_id": 2, "amount": 100},
        headers={"x-fingent-author": "marie@hec.example"},
    )
    assert resp.status_code == 403
