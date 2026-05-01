"""document_extractor injects wiki body + carries wiki_references.

Source: plan §STEP-BY-STEP Task 15. The vision agent gets the same
treatment as text agents — wiki splice into the system prompt.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from backend.orchestration.agents.document_extractor import run
from backend.orchestration.context import FingentContext
from backend.orchestration.store.writes import write_tx
from backend.orchestration.wiki.schema import WikiFrontmatter
from backend.orchestration.wiki.writer import upsert_page


async def _seed_doc(store, tmp_path: Path) -> int:
    """Insert a tiny stub PDF blob and a documents row; return the document id."""
    blob_path = tmp_path / "fake.pdf"
    # Minimal valid PDF header — body content is irrelevant; the runner
    # is faked, so the PDF is never parsed.
    blob_path.write_bytes(b"%PDF-1.4\n%minimal\n")

    async with write_tx(store.accounting, store.accounting_lock) as conn:
        cur = await conn.execute(
            "INSERT INTO documents "
            "(sha256, kind, direction, blob_path) "
            "VALUES (?, ?, ?, ?)",
            ("fakehash", "invoice_in", "inbound", str(blob_path)),
        )
        document_id = cur.lastrowid
    assert document_id is not None
    return int(document_id)


def _ctx(store, *, document_id: int, document_kind: str | None = None) -> FingentContext:
    payload: dict = {"document_id": document_id}
    if document_kind is not None:
        payload["document_kind"] = document_kind
    return FingentContext(
        run_id=31,
        pipeline_name="document_ingested",
        trigger_source="manual",
        trigger_payload=payload,
        node_outputs={},
        store=store,
    )


@pytest.fixture
async def doc_id(store, tmp_path):
    return await _seed_doc(store, tmp_path)


async def test_wiki_body_appears_in_system_prompt(
    store, fake_anthropic, fake_anthropic_message, doc_id,
):
    page_id, revision_id = await upsert_page(
        store.orchestration, store.orchestration_lock,
        path="policies/doc-extraction.md",
        title="Doc extraction",
        frontmatter=WikiFrontmatter(
            applies_to=["document_extraction", "ocr"],
            revision=1,
        ),
        body_md="SENTINEL_DOC_TOKEN — convert money to integer cents only.",
        author="test",
    )

    calls, fake = fake_anthropic
    fake.messages._response = fake_anthropic_message(
        tool_input={
            "supplier_name": "ACME",
            "invoice_number": "INV-1",
            "date": "2026-01-01",
            "items": [],
            "subtotal_cents": 0,
            "vat_cents": 0,
            "total_cents": 0,
            "currency": "EUR",
            "confidence": 0.9,
        },
        tool_name="submit_invoice",
    )

    ctx = _ctx(store, document_id=doc_id)
    result = await run(ctx)

    system_text = calls[0].get("system") or ""
    assert "## Policy reference (Living Rule Wiki)" in system_text
    assert "SENTINEL_DOC_TOKEN" in system_text

    assert list(result.wiki_references) == [(page_id, revision_id)]


async def test_document_kind_tag_widens_match(
    store, fake_anthropic, fake_anthropic_message, doc_id,
):
    """A page tagged with the document_kind only is reachable when the
    trigger payload includes that kind."""
    page_id, revision_id = await upsert_page(
        store.orchestration, store.orchestration_lock,
        path="policies/expense-report.md",
        title="Expense reports",
        frontmatter=WikiFrontmatter(
            applies_to=["expense_report"],  # neither generic tag
            revision=1,
        ),
        body_md="EXPENSE_KIND_TOKEN — receipts must include VAT_ID.",
        author="test",
    )

    calls, fake = fake_anthropic
    fake.messages._response = fake_anthropic_message(
        tool_input={
            "supplier_name": "ACME",
            "invoice_number": "INV-1",
            "date": "2026-01-01",
            "items": [],
            "subtotal_cents": 0,
            "vat_cents": 0,
            "total_cents": 0,
            "currency": "EUR",
            "confidence": 0.9,
        },
        tool_name="submit_invoice",
    )

    ctx = _ctx(store, document_id=doc_id, document_kind="expense_report")
    result = await run(ctx)

    system_text = calls[0].get("system") or ""
    assert "EXPENSE_KIND_TOKEN" in system_text
    assert (page_id, revision_id) in list(result.wiki_references)
