"""Unit tests for `tools/counterparty_resolver.py` — the four-stage cascade.

The `store` fixture brings up the full migration set including
`0005_demo_counterparties.py`, so Anthropic's IBAN
(FR7610278060610001020480101) is already in
`counterparty_identifiers` with `source='config'`.

Cases:
- Stage 1 (IBAN exact, outbound): Anthropic seeded → confidence 1.0,
  method='iban'.
- Stage 2 (virtual IBAN, inbound): customer IBAN → confidence 1.0,
  method='virtual_iban'.
- Stage 4 (fuzzy on label): typo'd "Anthropc" → score ≥85 → confidence
  ≤0.85, method='fuzzy'.
- Cache idempotency: re-running a fuzzy match for a novel name inserts at
  most one new identifier (INSERT OR IGNORE).
- Miss: extraneous label below threshold → counterparty_id=None.
- Document path: extraction.supplier_name → fuzzy hit.
"""
from __future__ import annotations

from typing import Any

from backend.orchestration import context as context_module
from backend.orchestration.tools import counterparty_resolver


SUPPLIER_IBAN_ANTHROPIC = "FR7610278060610001020480101"
CUSTOMER_IBAN_ACME = "FR7610278060610001020480201"


def _make_ctx(store, *, node_outputs: dict[str, Any]):
    return context_module.AgnesContext(
        run_id=1,
        pipeline_name="test",
        trigger_source="test",
        trigger_payload={},
        node_outputs=node_outputs,
        store=store,
        employee_id=None,
    )


async def _identifier_count(store, identifier: str) -> int:
    cur = await store.accounting.execute(
        "SELECT COUNT(*) FROM counterparty_identifiers WHERE identifier = ?",
        (identifier,),
    )
    row = await cur.fetchone()
    await cur.close()
    return int(row[0])


async def _identifier_source(store, identifier: str) -> str | None:
    cur = await store.accounting.execute(
        "SELECT source FROM counterparty_identifiers WHERE identifier = ?",
        (identifier,),
    )
    row = await cur.fetchone()
    await cur.close()
    return row[0] if row else None


# --------------------------------------------------------------------------- #
# Stage 1 — IBAN exact (outbound)
# --------------------------------------------------------------------------- #


async def test_stage1_iban_exact_outbound(store):
    tx = {
        "id": "tx-1",
        "type": "SepaCreditTransferOut",
        "side": "Debit",
        "counterparty": {"name": "Anthropic", "iban": SUPPLIER_IBAN_ANTHROPIC},
    }
    ctx = _make_ctx(store, node_outputs={"fetch-transaction": tx})

    out = await counterparty_resolver.run(ctx)

    assert out["counterparty_id"] is not None
    assert out["counterparty_legal_name"] == "Anthropic"
    assert out["confidence"] == 1.0
    assert out["method"] == "iban"
    assert out["envelope_category"] == "ai_tokens"


async def test_stage1_iban_via_creditor_block(store):
    tx = {
        "id": "tx-2",
        "type": "SepaCreditTransferOut",
        "side": "Debit",
        "creditor": {"iban": SUPPLIER_IBAN_ANTHROPIC},
    }
    ctx = _make_ctx(store, node_outputs={"fetch-transaction": tx})

    out = await counterparty_resolver.run(ctx)
    assert out["confidence"] == 1.0
    assert out["method"] == "iban"


# --------------------------------------------------------------------------- #
# Stage 2 — virtual IBAN (inbound)
# --------------------------------------------------------------------------- #


async def test_stage2_virtual_iban_inbound(store):
    tx = {
        "id": "tx-3",
        "type": "SepaCreditTransferIn",
        "side": "Credit",
        "counterparty": {"name": "Acme SAS", "iban": CUSTOMER_IBAN_ACME},
    }
    ctx = _make_ctx(store, node_outputs={"fetch-transaction": tx})

    out = await counterparty_resolver.run(ctx)
    assert out["confidence"] == 1.0
    assert out["method"] == "virtual_iban"
    assert out["counterparty_legal_name"] == "Acme SAS"


# --------------------------------------------------------------------------- #
# Stage 4 — fuzzy on counterparty label
# --------------------------------------------------------------------------- #


async def test_stage4_fuzzy_label_typo(store):
    """A typo'd label still hits via token_set_ratio."""
    tx = {
        "id": "tx-4",
        "type": "SepaCreditTransferOut",
        "side": "Debit",
        "counterparty_label": "Anthropic Inc",  # close enough to "Anthropic"
    }
    ctx = _make_ctx(store, node_outputs={"fetch-transaction": tx})

    out = await counterparty_resolver.run(ctx)

    assert out["counterparty_id"] is not None
    assert out["counterparty_legal_name"] == "Anthropic"
    assert out["method"] == "fuzzy"
    assert out["confidence"] <= 0.85
    assert out["confidence"] > 0.0


async def test_stage4_writes_back_name_alias(store):
    label = "Anthropic Inc"
    tx = {
        "id": "tx-5",
        "type": "SepaCreditTransferOut",
        "side": "Debit",
        "counterparty_label": label,
    }
    ctx = _make_ctx(store, node_outputs={"fetch-transaction": tx})

    # Pre-condition: nothing in identifiers for this label.
    assert await _identifier_count(store, label) == 0

    await counterparty_resolver.run(ctx)
    assert await _identifier_count(store, label) == 1
    assert await _identifier_source(store, label) == "fuzzy"

    # Re-run is idempotent — INSERT OR IGNORE swallows the duplicate.
    await counterparty_resolver.run(ctx)
    assert await _identifier_count(store, label) == 1


# --------------------------------------------------------------------------- #
# Miss path
# --------------------------------------------------------------------------- #


async def test_miss_unknown_label(store):
    tx = {
        "id": "tx-6",
        "type": "SepaCreditTransferOut",
        "side": "Debit",
        "counterparty_label": "ZZZZZ Totally Unknown Vendor",
    }
    ctx = _make_ctx(store, node_outputs={"fetch-transaction": tx})

    out = await counterparty_resolver.run(ctx)
    assert out["counterparty_id"] is None
    assert out["confidence"] == 0.0


async def test_no_inputs_returns_miss(store):
    ctx = _make_ctx(store, node_outputs={})
    out = await counterparty_resolver.run(ctx)
    assert out["counterparty_id"] is None


# --------------------------------------------------------------------------- #
# Document path (Stage 4 only)
# --------------------------------------------------------------------------- #


async def test_document_path_fuzzy_supplier_name(store):
    extraction = {"supplier_name": "Anthropic", "amount_cents": 12500}
    ctx = _make_ctx(store, node_outputs={"extract": extraction})

    out = await counterparty_resolver.run(ctx)
    assert out["counterparty_legal_name"] == "Anthropic"
    assert out["method"] == "fuzzy"
    assert out["confidence"] <= 0.85
