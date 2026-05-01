"""Claude-powered GL classifier - tool wiring + cache writeback to account_rules."""
from __future__ import annotations

from backend.orchestration.agents.gl_account_classifier_agent import run
from backend.orchestration.context import FingentContext


def _ctx(store, *, node_outputs: dict | None = None) -> FingentContext:
    return FingentContext(
        run_id=42,
        pipeline_name="test-gl-classifier-agent",
        trigger_source="manual",
        trigger_payload={},
        node_outputs=node_outputs or {},
        store=store,
    )


async def test_agent_classifies_and_writes_back(store, fake_anthropic, fake_anthropic_message):
    calls, fake = fake_anthropic
    fake.messages._response = fake_anthropic_message(
        tool_input={
            "gl_account": "626100",
            "confidence": 0.9,
            "alternatives": [],
            "vat_rate_bp": 2000,
        },
        tool_name="submit_gl_account",
    )

    ctx = _ctx(
        store,
        node_outputs={
            "resolve-counterparty": {"counterparty_legal_name": "BrandNewVendor"},
            "fetch-transaction": {
                "id": "tx_42",
                "type": "CardOutgoing",
                "amount": {"value": "12.34", "currency": "EUR"},
            },
        },
    )

    result = await run(ctx)

    # AgentResult.output is the parsed tool input dict.
    assert isinstance(result.output, dict)
    assert result.output["gl_account"] == "626100"
    assert result.output["confidence"] == 0.9
    assert result.confidence == 0.9

    # The forced submit tool was wired correctly.
    assert calls, "expected the runner to have called the fake client"
    request = calls[0]
    assert request["tool_choice"]["name"] == "submit_gl_account"
    submit_tool = request["tools"][0]
    assert submit_tool["name"] == "submit_gl_account"
    enum = submit_tool["input_schema"]["properties"]["gl_account"]["enum"]
    # Closed enum is sourced from chart_of_accounts at request time.
    assert "626100" in enum and "624" in enum

    # Cache writeback - a new rule for ('counterparty', 'BrandNewVendor') -> 626100.
    cur = await store.accounting.execute(
        "SELECT pattern_kind, pattern_value, gl_account, precedence, source "
        "FROM account_rules "
        "WHERE pattern_kind = 'counterparty' AND pattern_value = ? AND gl_account = ?",
        ("BrandNewVendor", "626100"),
    )
    rows = list(await cur.fetchall())
    assert len(rows) == 1
    row = rows[0]
    assert row[3] == 20         # precedence
    assert row[4] == "ai"       # source


async def test_agent_skips_writeback_when_no_counterparty(
    store, fake_anthropic, fake_anthropic_message,
):
    """Without a counterparty legal_name we have no key to cache against."""
    calls, fake = fake_anthropic
    fake.messages._response = fake_anthropic_message(
        tool_input={"gl_account": "626100", "confidence": 0.8},
        tool_name="submit_gl_account",
    )

    ctx = _ctx(store)
    result = await run(ctx)
    assert isinstance(result.output, dict)
    assert result.output["gl_account"] == "626100"

    # No rows for unknown counterparty (we never had one).
    cur = await store.accounting.execute(
        "SELECT count(*) FROM account_rules WHERE source = 'ai'"
    )
    row = await cur.fetchone()
    assert row[0] == 0


async def test_agent_writeback_is_idempotent(
    store, fake_anthropic, fake_anthropic_message,
):
    """Calling twice for the same (counterparty, gl) inserts only one rule."""
    _calls, fake = fake_anthropic
    fake.messages._response = fake_anthropic_message(
        tool_input={"gl_account": "626100", "confidence": 0.85},
        tool_name="submit_gl_account",
    )

    ctx = _ctx(
        store,
        node_outputs={
            "resolve-counterparty": {"counterparty_legal_name": "RepeatVendor"},
        },
    )
    await run(ctx)
    await run(ctx)

    cur = await store.accounting.execute(
        "SELECT count(*) FROM account_rules "
        "WHERE pattern_kind = 'counterparty' AND pattern_value = ? AND gl_account = ?",
        ("RepeatVendor", "626100"),
    )
    row = await cur.fetchone()
    assert row[0] == 1
