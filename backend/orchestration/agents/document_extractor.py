"""Claude vision PDF invoice extractor.

Source: RealMetaPRD §7.3; 04_AGENT_PATTERNS.md:90-107 (closed-list classifier);
ANTHROPIC_SDK_STACK_REFERENCE doc-block format.

deadline_s=15.0 override per RealMetaPRD §7.9.
"""
from __future__ import annotations

import asyncio
import base64
from pathlib import Path
from typing import Any

from ..context import FingentContext
from ..registries import get_runner
from ..runners.base import AgentResult
from ..tools import wiki_reader as wiki_reader_tool


_SYSTEM_PROMPT = (
    "Extract structured invoice data. Convert all monetary amounts to "
    "integer cents. Validate sums. Respond via the submit_invoice tool only."
)


_SUBMIT_TOOL: dict[str, Any] = {
    "name": "submit_invoice",
    "description": "Submit the structured invoice extraction.",
    "input_schema": {
        "type": "object",
        "required": [
            "supplier_name",
            "invoice_number",
            "date",
            "items",
            "subtotal_cents",
            "vat_cents",
            "total_cents",
            "currency",
            "confidence",
        ],
        "properties": {
            "supplier_name": {"type": "string"},
            "invoice_number": {"type": "string"},
            "date": {"type": "string", "description": "ISO YYYY-MM-DD"},
            "due_date": {"type": ["string", "null"]},
            "items": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": ["description", "amount_cents"],
                    "properties": {
                        "description": {"type": "string"},
                        "amount_cents": {"type": "integer"},
                        "vat_rate_bp": {"type": ["integer", "null"]},
                    },
                },
            },
            "subtotal_cents": {"type": "integer"},
            "vat_percent": {"type": ["number", "null"]},
            "vat_cents": {"type": "integer"},
            "total_cents": {"type": "integer"},
            "currency": {"type": "string", "enum": ["EUR"]},
            "confidence": {"type": "number"},
        },
    },
}


async def _load_document(ctx: FingentContext, document_id: int) -> tuple[str, str]:
    """Return (blob_path, sha256) for a documents.id."""
    cur = await ctx.store.accounting.execute(
        "SELECT blob_path, sha256 FROM documents WHERE id = ?",
        (document_id,),
    )
    row = await cur.fetchone()
    await cur.close()
    if row is None:
        raise ValueError(f"document_id={document_id} not found")
    return row[0], row[1]


async def run(ctx: FingentContext) -> AgentResult:
    """Extract invoice fields from a PDF blob via Claude vision."""
    document_id = ctx.trigger_payload.get("document_id")
    if document_id is None:
        raise ValueError("trigger_payload missing 'document_id'")

    blob_path, _sha256 = await _load_document(ctx, int(document_id))

    # Synchronous read wrapped in to_thread so the event loop stays unblocked.
    data = await asyncio.to_thread(Path(blob_path).read_bytes)
    b64 = base64.b64encode(data).decode("ascii")

    # Phase 4.A — Living Rule Wiki injection. Tags reflect document
    # extraction policy; document_kind lets the CFO file kind-specific
    # extraction rules (e.g. "expense_report" vs. "supplier_invoice").
    metadata = ctx.metadata if isinstance(ctx.metadata, dict) else {}
    jurisdiction = metadata.get("jurisdiction")
    payload = ctx.trigger_payload if isinstance(ctx.trigger_payload, dict) else {}
    document_kind = payload.get("document_kind") or metadata.get("document_kind")
    tags = ["document_extraction", "ocr"]
    if document_kind:
        tags.append(str(document_kind))
    wiki_payload = await wiki_reader_tool.fetch(
        ctx,
        tags=tags,
        jurisdiction=jurisdiction,
    )
    wiki_pages = wiki_payload.get("pages") or []
    wiki_references: list[tuple[int, int]] = [
        (int(p["page_id"]), int(p["revision_id"])) for p in wiki_pages
    ]
    if wiki_pages:
        policy_blocks = "\n\n".join(
            f"### {p['title']} ({p['path']}, rev {p['revision_number']})\n\n{p['body_md']}"
            for p in wiki_pages
        )
        system = (
            f"{_SYSTEM_PROMPT}\n\n"
            "## Policy reference (Living Rule Wiki)\n\n"
            f"{policy_blocks}"
        )
    else:
        system = _SYSTEM_PROMPT

    messages = [
        {
            "role": "user",
            "content": [
                {
                    "type": "document",
                    "source": {
                        "type": "base64",
                        "media_type": "application/pdf",
                        "data": b64,
                    },
                },
                {
                    "type": "text",
                    "text": "Extract the invoice. Use the submit_invoice tool.",
                },
            ],
        }
    ]

    runner = get_runner("anthropic")
    result = await runner.run(
        ctx=ctx,
        system=system,
        tools=[_SUBMIT_TOOL],
        messages=messages,
        model="claude-sonnet-4-6",
        max_tokens=2000,
        temperature=0.0,
        deadline_s=15.0,
        wiki_context=wiki_references,
    )
    return result
