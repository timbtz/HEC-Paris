"""Unit test for `agents.anomaly_flag_agent.run`.

Uses the `fake_anthropic` fixture (defined in conftest.py) to stub the
runner and asserts the agent emits the parsed tool_input via
`AgentResult.output`. Anomaly schema is the contract Slice D's reporting
pipelines depend on.
"""
from __future__ import annotations

from types import SimpleNamespace

from backend.orchestration.agents.anomaly_flag_agent import run as anomaly_run
from backend.orchestration.context import FingentContext


async def test_anomaly_agent_returns_structured_output(store, fake_anthropic):
    calls, fake_client = fake_anthropic

    fake_client.messages._response = SimpleNamespace(
        id="msg_anom_1",
        model="claude-sonnet-4-6",
        stop_reason="tool_use",
        content=[SimpleNamespace(
            type="tool_use",
            id="tu_anom_1",
            name="submit_anomalies",
            input={
                "anomalies": [
                    {
                        "kind": "balance_drift",
                        "description": "Trial balance off by 10€",
                        "evidence": "TB: dr=12010 cr=12000",
                        "line_ids": [],
                        "confidence": 0.85,
                    }
                ],
                "overall_confidence": 0.85,
            },
        )],
        usage=SimpleNamespace(
            input_tokens=200,
            output_tokens=50,
            cache_creation_input_tokens=0,
            cache_read_input_tokens=0,
        ),
    )

    ctx = FingentContext(
        run_id=1,
        pipeline_name="period_close",
        trigger_source="test",
        trigger_payload={"period_code": "2026-Q1"},
        node_outputs={
            "compute-trial-balance": {
                "trial_balance": [],
                "total_debit_cents": 12010,
                "total_credit_cents": 12000,
                "balanced": False,
                "confidence": 0.5,
            },
            "compute-open-entries": {
                "open_entries": [],
                "count": 0,
                "confidence": 1.0,
            },
            "compute-vat": {
                "lines": [],
                "totals": {
                    "collected_cents": 0,
                    "deductible_cents": 0,
                    "net_due_cents": 0,
                },
                "confidence": 1.0,
            },
        },
        store=store,
    )

    result = await anomaly_run(ctx)
    assert isinstance(result.output, dict)
    assert "anomalies" in result.output
    assert len(result.output["anomalies"]) == 1
    assert result.output["anomalies"][0]["kind"] == "balance_drift"
    assert result.output["overall_confidence"] == 0.85

    # Tool schema closed `kind` to a known enum.
    assert calls, "expected at least one runner call"
    submitted_tools = calls[0].get("tools", [])
    assert submitted_tools
    schema = submitted_tools[0]["input_schema"]
    kind_enum = schema["properties"]["anomalies"]["items"]["properties"]["kind"]["enum"]
    assert "balance_drift" in kind_enum
    assert "vat_mismatch" in kind_enum
