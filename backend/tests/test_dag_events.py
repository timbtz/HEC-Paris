"""DAG-viewer event contract — PRD-AutonomousCFO §7.4.

Asserts two backend invariants the live DAG-viewer relies on:

1. `node_started` carries enough metadata to paint a node before any
   output arrives — `kind`, `ref`, `depends_on`, `when`, `cacheable`.
2. Every agent node fires exactly one `agent.decision` event after the
   audit row is committed, with the cost / token / prompt-hash fields
   the right-rail drawer renders.
"""
from __future__ import annotations

import json

from backend.orchestration.executor import execute_pipeline


async def _events_for_run(store, run_id: int) -> list[tuple[str, str | None, dict]]:
    cur = await store.orchestration.execute(
        "SELECT event_type, node_id, data FROM pipeline_events "
        "WHERE run_id = ? ORDER BY id",
        (run_id,),
    )
    rows = list(await cur.fetchall())
    await cur.close()
    return [(r[0], r[1], json.loads(r[2] or "{}")) for r in rows]


async def test_node_started_carries_metadata(store, fake_anthropic, fake_anthropic_message):
    """Every `node_started` payload exposes kind/ref/depends_on/when/cacheable."""
    _, fake = fake_anthropic
    fake.messages._response = fake_anthropic_message(
        tool_input={"answer": "ok"}, tool_name="submit_test",
    )

    run_id = await execute_pipeline(
        "noop_demo",
        trigger_source="manual",
        trigger_payload={},
        store=store,
        background=False,
    )

    events = await _events_for_run(store, run_id)
    started = [(node_id, data) for et, node_id, data in events if et == "node_started"]
    assert {n for n, _ in started} == {"tool-a", "agent-b", "tool-c"}, started

    by_id = {n: data for n, data in started}

    # Tool node — minimum shape.
    assert by_id["tool-a"]["kind"] == "tool"
    assert by_id["tool-a"]["ref"] == "tools.noop:run"
    assert by_id["tool-a"]["depends_on"] == []
    assert by_id["tool-a"]["when"] is None
    assert by_id["tool-a"]["cacheable"] is True

    # Agent node — depends on the tool, not cacheable.
    assert by_id["agent-b"]["kind"] == "agent"
    assert by_id["agent-b"]["ref"] == "agents.noop:run"
    assert by_id["agent-b"]["depends_on"] == ["tool-a"]
    assert by_id["agent-b"]["cacheable"] is False


async def test_agent_decision_event_emitted(store, fake_anthropic, fake_anthropic_message):
    """The agent node must fire `agent.decision` with cost + prompt_hash."""
    _, fake = fake_anthropic
    # Pick a usage that lands above the per-million floor for haiku (input
    # 800µ$/Mtok, output 4000µ$/Mtok) so the cost emit is observably non-zero.
    fake.messages._response = fake_anthropic_message(
        tool_input={"answer": "ok"},
        tool_name="submit_test",
        usage={"input_tokens": 200_000, "output_tokens": 80_000},
    )

    run_id = await execute_pipeline(
        "noop_demo",
        trigger_source="manual",
        trigger_payload={},
        store=store,
        employee_id=1,
        background=False,
    )

    events = await _events_for_run(store, run_id)
    decisions = [(node_id, data) for et, node_id, data in events
                 if et == "agent.decision"]
    assert len(decisions) == 1, [e[0] for e in events]
    node_id, data = decisions[0]

    assert node_id == "agent-b"
    # Must reference an audit row.
    assert isinstance(data["decision_id"], int) and data["decision_id"] >= 1
    # Reasoning fields the drawer reads.
    assert data["model"] == "claude-haiku-4-5"
    assert data["runner"] == "anthropic"
    assert data["provider"] == "anthropic"
    assert isinstance(data["prompt_hash"], str) and data["prompt_hash"]
    assert "finish_reason" in data
    assert "confidence" in data
    assert "latency_ms" in data
    # Token + cost fields mirror the audit row.
    assert data["input_tokens"] == 200_000
    assert data["output_tokens"] == 80_000
    assert isinstance(data["cost_micro_usd"], int)
    assert data["cost_micro_usd"] > 0
    # Wiki citations default to an empty list when the agent did not cite.
    assert data["wiki_citations"] == []

    # Audit row must agree with the event's decision_id.
    cur = await store.audit.execute(
        "SELECT id, model, prompt_hash FROM agent_decisions "
        "WHERE run_id_logical = ?",
        (run_id,),
    )
    rows = list(await cur.fetchall())
    await cur.close()
    assert len(rows) == 1
    assert rows[0][0] == data["decision_id"]
    assert rows[0][1] == data["model"]
    assert rows[0][2] == data["prompt_hash"]
