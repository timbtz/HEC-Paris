"""propose_checkpoint_commit atomicity + shape."""
from __future__ import annotations

import pytest

from backend.orchestration.audit import propose_checkpoint_commit
from backend.orchestration.runners.base import AgentResult, TokenUsage


def _result(model: str = "claude-haiku-4-5") -> AgentResult:
    return AgentResult(
        output={"answer": "ok"},
        model=model,
        response_id="msg_test",
        prompt_hash="abc123def4567890",
        alternatives=[{"value": "ok", "score": 0.9}],
        confidence=0.92,
        usage=TokenUsage(input_tokens=100, output_tokens=50,
                         cache_read_tokens=10, cache_write_tokens=5,
                         reasoning_tokens=0),
        latency_ms=420,
        finish_reason="end_turn",
        temperature=0.0,
        seed=None,
    )


async def test_writes_both_rows(store):
    decision_id = await propose_checkpoint_commit(
        audit_db=store.audit, audit_lock=store.audit_lock,
        run_id=1, node_id="agent-x", result=_result(),
        runner="anthropic", employee_id=1, provider="anthropic",
    )
    assert isinstance(decision_id, int) and decision_id > 0

    cur = await store.audit.execute(
        "SELECT model, prompt_hash, confidence, finish_reason "
        "FROM agent_decisions WHERE id = ?", (decision_id,))
    row = await cur.fetchone()
    assert row[0] == "claude-haiku-4-5"
    assert row[1] == "abc123def4567890"
    assert row[2] == 0.92
    assert row[3] == "end_turn"

    cur = await store.audit.execute(
        "SELECT employee_id, provider, model, input_tokens, output_tokens, "
        "       cache_read_tokens, cache_write_tokens, cost_micro_usd "
        "FROM agent_costs WHERE decision_id = ?", (decision_id,))
    row = await cur.fetchone()
    assert row[0] == 1
    assert row[1] == "anthropic"
    assert row[2] == "claude-haiku-4-5"
    assert row[3] == 100 and row[4] == 50
    assert row[5] == 10 and row[6] == 5
    # Haiku rates: 100*800 + 50*4000 + 10*80 + 5*1000 = 80000 + 200000 + 800 + 5000 = 285800
    # Floor div by 1_000_000 → 0
    assert row[7] == 0


async def test_atomic_rollback(store, monkeypatch):
    """If the second INSERT fails, neither row is persisted (write_tx rolls back)."""
    real_execute = store.audit.execute
    call_count = {"n": 0}

    async def failing_execute(sql, *args, **kwargs):
        call_count["n"] += 1
        # 1st execute = BEGIN IMMEDIATE; 2nd = decisions INSERT; 3rd = costs INSERT.
        # Fail the costs INSERT.
        if call_count["n"] == 3 and "agent_costs" in sql:
            raise RuntimeError("simulated INSERT failure on agent_costs")
        return await real_execute(sql, *args, **kwargs)

    monkeypatch.setattr(store.audit, "execute", failing_execute)

    with pytest.raises(RuntimeError, match="simulated INSERT failure"):
        await propose_checkpoint_commit(
            audit_db=store.audit, audit_lock=store.audit_lock,
            run_id=2, node_id="agent-y", result=_result(),
            runner="anthropic", employee_id=1, provider="anthropic",
        )

    # Use the real execute (not the patched one) to verify rollback persisted nothing.
    monkeypatch.setattr(store.audit, "execute", real_execute)
    cur = await store.audit.execute("SELECT count(*) FROM agent_decisions")
    assert (await cur.fetchone())[0] == 0
    cur = await store.audit.execute("SELECT count(*) FROM agent_costs")
    assert (await cur.fetchone())[0] == 0
