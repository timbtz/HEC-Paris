"""Unit tests for `agents/counterparty_classifier.py`.

Strategy: use the existing `fake_anthropic` fixture from conftest. The
fake's `messages.create` records every call kwargs and returns whatever
fake `Message` we configure.

Verifies:
- The agent passes `submit_counterparty` in the tools list and the closed
  list of candidates in the user content.
- On a non-null counterparty_id pick, AgentResult.confidence round-trips.
- Cache writeback inserts a `counterparty_identifiers` row with source='ai'.
"""
from __future__ import annotations

from typing import Any

from backend.orchestration import context as context_module
from backend.orchestration.agents import counterparty_classifier


def _make_ctx(store, *, node_outputs: dict[str, Any] | None = None):
    return context_module.FingentContext(
        run_id=1,
        pipeline_name="test",
        trigger_source="test",
        trigger_payload={},
        node_outputs=node_outputs or {},
        store=store,
        employee_id=None,
    )


async def _identifiers_for(store, identifier: str) -> list[tuple[Any, ...]]:
    cur = await store.accounting.execute(
        "SELECT counterparty_id, identifier_type, identifier, source, confidence "
        "FROM counterparty_identifiers WHERE identifier = ?",
        (identifier,),
    )
    rows = await cur.fetchall()
    await cur.close()
    return [tuple(r) for r in rows]


async def _first_counterparty_id(store) -> int:
    cur = await store.accounting.execute(
        "SELECT id FROM counterparties ORDER BY id ASC LIMIT 1"
    )
    row = await cur.fetchone()
    await cur.close()
    return int(row[0])


# --------------------------------------------------------------------------- #


async def test_agent_passes_submit_tool_and_candidates(
    store, fake_anthropic, fake_anthropic_message,
):
    """Tool list contains `submit_counterparty`; user content lists candidates."""
    calls, fake = fake_anthropic
    cp_id = await _first_counterparty_id(store)
    fake.messages._response = fake_anthropic_message(
        tool_input={"counterparty_id": cp_id, "confidence": 0.7},
        tool_name="submit_counterparty",
    )

    ctx = _make_ctx(
        store,
        node_outputs={
            "fetch-transaction": {
                "id": "tx-x",
                "type": "SepaCreditTransferOut",
                "side": "Debit",
                "amount": {"value": "100.00", "currency": "EUR"},
                "counterparty_label": "Some Novel Vendor",
            },
        },
    )

    result = await counterparty_classifier.run(ctx)

    assert len(calls) == 1
    kwargs = calls[0]
    tool_names = [t["name"] for t in kwargs["tools"]]
    assert "submit_counterparty" in tool_names
    # Tool-choice is forced by AnthropicRunner when name starts with "submit".
    assert kwargs["tool_choice"] == {"type": "tool", "name": "submit_counterparty"}
    assert kwargs["model"] == "claude-sonnet-4-6"

    # User content should mention the closed candidate list and "None of the above".
    user_msg = kwargs["messages"][0]
    assert user_msg["role"] == "user"
    assert "Candidates:" in user_msg["content"]
    assert "None of the above" in user_msg["content"]

    # Result round-trip.
    assert result.output == {"counterparty_id": cp_id, "confidence": 0.7}
    assert result.confidence == 0.7


async def test_agent_writes_back_ai_identifier(
    store, fake_anthropic, fake_anthropic_message,
):
    """A non-null pick should INSERT OR IGNORE a name_alias with source='ai'."""
    _, fake = fake_anthropic
    cp_id = await _first_counterparty_id(store)
    label = "Brand-New Vendor 9000"
    fake.messages._response = fake_anthropic_message(
        tool_input={"counterparty_id": cp_id, "confidence": 0.6},
        tool_name="submit_counterparty",
    )

    ctx = _make_ctx(
        store,
        node_outputs={
            "fetch-transaction": {
                "id": "tx-y",
                "type": "SepaCreditTransferOut",
                "side": "Debit",
                "counterparty_label": label,
            },
        },
    )

    await counterparty_classifier.run(ctx)

    rows = await _identifiers_for(store, label)
    assert len(rows) == 1
    cp_row, ident_type, ident, source, confidence = rows[0]
    assert cp_row == cp_id
    assert ident_type == "name_alias"
    assert ident == label
    assert source == "ai"
    assert abs(confidence - 0.6) < 1e-9


async def test_agent_pins_iban_when_present(
    store, fake_anthropic, fake_anthropic_message,
):
    """Prefer IBAN over name_alias when the tx has one — that's the durable identifier."""
    _, fake = fake_anthropic
    cp_id = await _first_counterparty_id(store)
    iban = "FR7610278060610001020480999"  # not pre-seeded
    fake.messages._response = fake_anthropic_message(
        tool_input={"counterparty_id": cp_id, "confidence": 0.8},
        tool_name="submit_counterparty",
    )

    ctx = _make_ctx(
        store,
        node_outputs={
            "fetch-transaction": {
                "id": "tx-iban",
                "type": "SepaCreditTransferOut",
                "side": "Debit",
                "counterparty": {"iban": iban, "name": "Mystery Vendor"},
            },
        },
    )
    await counterparty_classifier.run(ctx)

    rows = await _identifiers_for(store, iban)
    assert len(rows) == 1
    assert rows[0][1] == "iban"
    assert rows[0][3] == "ai"


async def test_agent_no_writeback_on_null_pick(
    store, fake_anthropic, fake_anthropic_message,
):
    """If the model returns null counterparty_id, no identifier row is written."""
    _, fake = fake_anthropic
    label = "Untracked Vendor"
    fake.messages._response = fake_anthropic_message(
        tool_input={"counterparty_id": None, "confidence": 0.1},
        tool_name="submit_counterparty",
    )

    ctx = _make_ctx(
        store,
        node_outputs={
            "fetch-transaction": {
                "id": "tx-null",
                "type": "SepaCreditTransferOut",
                "side": "Debit",
                "counterparty_label": label,
            },
        },
    )
    await counterparty_classifier.run(ctx)

    rows = await _identifiers_for(store, label)
    assert rows == []
