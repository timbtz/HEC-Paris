"""Document extractor agent tests.

Source: RealMetaPRD §7.3 (vision-extract → validate node).
The fake_anthropic fixture short-circuits the SDK call; we just verify
the agent passes the right shape and returns the AgentResult.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from backend.orchestration.agents import document_extractor
from backend.orchestration.context import AgnesContext
from backend.orchestration.runners.base import AgentResult
from backend.orchestration.store.writes import write_tx


_SAMPLE_EXTRACTION = {
    "supplier_name": "Anthropic, PBC",
    "invoice_number": "INV-001",
    "date": "2026-04-01",
    "due_date": None,
    "items": [
        {"description": "Claude API usage", "amount_cents": 5000, "vat_rate_bp": 2000},
    ],
    "subtotal_cents": 5000,
    "vat_percent": 20.0,
    "vat_cents": 1000,
    "total_cents": 6000,
    "currency": "EUR",
    "confidence": 0.93,
}


async def _insert_document(store, blob_path: Path) -> int:
    """Insert a documents row pointing at blob_path; return its id."""
    sha = "fakehash" + blob_path.name
    async with write_tx(store.accounting, store.accounting_lock) as conn:
        cur = await conn.execute(
            "INSERT INTO documents "
            "(sha256, kind, direction, blob_path) "
            "VALUES (?, 'invoice_in', 'inbound', ?)",
            (sha, str(blob_path)),
        )
        return cur.lastrowid


async def test_document_extractor_returns_agent_result(
    store, tmp_path, fake_anthropic, fake_anthropic_message
):
    # Write a tiny dummy PDF blob to disk.
    pdf_path = tmp_path / "fake.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n%fake\n")

    document_id = await _insert_document(store, pdf_path)

    calls, fake = fake_anthropic
    fake.messages._response = fake_anthropic_message(
        tool_input=_SAMPLE_EXTRACTION,
        tool_name="submit_invoice",
    )

    ctx = AgnesContext(
        run_id=1,
        pipeline_name="document_ingested",
        trigger_source="document.uploaded",
        trigger_payload={"document_id": document_id, "sha256": "abc"},
        node_outputs={},
        store=store,
    )

    result = await document_extractor.run(ctx)

    assert isinstance(result, AgentResult)
    assert result.output == _SAMPLE_EXTRACTION
    assert result.confidence == pytest.approx(0.93)

    # Confirm the request was structured as a vision call with a doc block.
    assert len(calls) == 1
    sent = calls[0]
    assert sent["model"] == "claude-sonnet-4-6"
    assert sent["max_tokens"] == 2000
    assert sent["temperature"] == 0.0
    assert sent["tool_choice"] == {"type": "tool", "name": "submit_invoice"}
    user_content = sent["messages"][0]["content"]
    assert any(
        block.get("type") == "document"
        and block["source"]["media_type"] == "application/pdf"
        for block in user_content
    )
    # Tool schema includes confidence and currency=EUR enum.
    tool = sent["tools"][0]
    assert tool["name"] == "submit_invoice"
    assert "confidence" in tool["input_schema"]["required"]
    assert tool["input_schema"]["properties"]["currency"]["enum"] == ["EUR"]


async def test_document_extractor_missing_id_raises(store):
    ctx = AgnesContext(
        run_id=1,
        pipeline_name="document_ingested",
        trigger_source="document.uploaded",
        trigger_payload={},  # no document_id
        node_outputs={},
        store=store,
    )
    with pytest.raises(ValueError, match="document_id"):
        await document_extractor.run(ctx)


async def test_document_extractor_unknown_id_raises(store):
    ctx = AgnesContext(
        run_id=1,
        pipeline_name="document_ingested",
        trigger_source="document.uploaded",
        trigger_payload={"document_id": 99999},
        node_outputs={},
        store=store,
    )
    with pytest.raises(ValueError, match="not found"):
        await document_extractor.run(ctx)
