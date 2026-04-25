"""Wedge SQL test (RealMetaPRD §7.11 / §11.line 1519).

Runs three fixture pipelines, one per employee, then executes the wedge
query and asserts one row per employee with non-zero call_count and
the right cost shape.
"""
from __future__ import annotations

from backend.orchestration.audit import propose_checkpoint_commit
from backend.orchestration.runners.base import AgentResult, TokenUsage


def _result(tokens_in: int, tokens_out: int) -> AgentResult:
    return AgentResult(
        output={"answer": "ok"},
        model="claude-sonnet-4-6",
        response_id="msg_test",
        prompt_hash="aaaaaaaaaaaaaaaa",
        alternatives=None,
        confidence=0.99,
        usage=TokenUsage(input_tokens=tokens_in, output_tokens=tokens_out),
        latency_ms=100,
        finish_reason="end_turn",
        temperature=0.0,
        seed=None,
    )


async def _employee_id_for(store, email: str) -> int:
    cur = await store.audit.execute("SELECT id FROM employees WHERE email = ?", (email,))
    row = await cur.fetchone()
    assert row is not None, f"missing seeded employee {email}"
    return row[0]


async def test_wedge_query_one_row_per_employee(store):
    tim_id   = await _employee_id_for(store, "tim@hec.example")
    marie_id = await _employee_id_for(store, "marie@hec.example")
    paul_id  = await _employee_id_for(store, "paul@hec.example")

    # Three fixture decisions, one per employee, with distinct token volumes
    # so cost ordering is deterministic.
    await propose_checkpoint_commit(
        audit_db=store.audit, audit_lock=store.audit_lock,
        run_id=1, node_id="agent-x", result=_result(2_000_000, 1_000_000),
        runner="anthropic", employee_id=tim_id, provider="anthropic",
    )
    await propose_checkpoint_commit(
        audit_db=store.audit, audit_lock=store.audit_lock,
        run_id=2, node_id="agent-y", result=_result(1_000_000, 500_000),
        runner="anthropic", employee_id=marie_id, provider="anthropic",
    )
    await propose_checkpoint_commit(
        audit_db=store.audit, audit_lock=store.audit_lock,
        run_id=3, node_id="agent-z", result=_result(500_000, 250_000),
        runner="anthropic", employee_id=paul_id, provider="anthropic",
    )

    # Wedge SQL — single-DB version (no ATTACH needed since we already hold
    # the audit connection). Same shape as RealMetaPRD §7.11.
    cur = await store.audit.execute(
        "SELECT e.email, e.full_name, "
        "       COUNT(*)                       AS call_count, "
        "       SUM(c.cost_micro_usd)/1.0e6    AS usd_this_month "
        "FROM   agent_costs c "
        "JOIN   employees   e ON e.id = c.employee_id "
        "WHERE  c.provider = 'anthropic' "
        "  AND  strftime('%Y-%m', c.created_at) = strftime('%Y-%m', 'now') "
        "GROUP BY e.id "
        "ORDER BY usd_this_month DESC"
    )
    rows = list(await cur.fetchall())
    assert len(rows) == 3, f"expected 3 rows, got {rows}"

    emails_in_order = [r[0] for r in rows]
    # Tim got the largest token bill so should be first.
    assert emails_in_order[0] == "tim@hec.example"

    for r in rows:
        assert r[2] >= 1                  # call_count
        assert r[3] is not None and r[3] > 0   # usd_this_month
