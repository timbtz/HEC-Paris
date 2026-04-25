"""validate_totals: items sum + subtotal+VAT == total + EUR-only.

Source: RealMetaPRD §7.3.
"""
from __future__ import annotations

from backend.orchestration.context import AgnesContext
from backend.orchestration.tools.document_extractor import validate_totals


def _ctx(store, extraction):
    return AgnesContext(
        run_id=1,
        pipeline_name="document_ingested",
        trigger_source="document.uploaded",
        trigger_payload={},
        node_outputs={"extract": extraction},
        store=store,
    )


async def test_ok_clean_invoice(store):
    extraction = {
        "items": [
            {"description": "A", "amount_cents": 3000},
            {"description": "B", "amount_cents": 2000},
        ],
        "subtotal_cents": 5000,
        "vat_cents": 1000,
        "total_cents": 6000,
        "currency": "EUR",
    }
    result = await validate_totals(_ctx(store, extraction))
    assert result["ok"] is True
    assert result["errors"] == []
    assert result["confidence"] == 1.0


async def test_ok_one_cent_vat_rounding(store):
    extraction = {
        "items": [{"description": "A", "amount_cents": 1000}],
        "subtotal_cents": 1000,
        "vat_cents": 200,
        "total_cents": 1201,  # 1 cent off — within tolerance
        "currency": "EUR",
    }
    result = await validate_totals(_ctx(store, extraction))
    assert result["ok"] is True


async def test_bad_items_sum(store):
    extraction = {
        "items": [
            {"description": "A", "amount_cents": 3000},
            {"description": "B", "amount_cents": 1500},  # sums to 4500, not 5000
        ],
        "subtotal_cents": 5000,
        "vat_cents": 1000,
        "total_cents": 6000,
        "currency": "EUR",
    }
    result = await validate_totals(_ctx(store, extraction))
    assert result["ok"] is False
    assert result["confidence"] == 0.0
    assert any("items sum" in e for e in result["errors"])


async def test_bad_subtotal_plus_vat(store):
    extraction = {
        "items": [{"description": "A", "amount_cents": 5000}],
        "subtotal_cents": 5000,
        "vat_cents": 1000,
        "total_cents": 7000,  # 1000 cents off — outside ±1 tolerance
        "currency": "EUR",
    }
    result = await validate_totals(_ctx(store, extraction))
    assert result["ok"] is False
    assert any("subtotal+vat" in e for e in result["errors"])


async def test_bad_currency(store):
    extraction = {
        "items": [{"description": "A", "amount_cents": 5000}],
        "subtotal_cents": 5000,
        "vat_cents": 1000,
        "total_cents": 6000,
        "currency": "USD",  # not EUR
    }
    result = await validate_totals(_ctx(store, extraction))
    assert result["ok"] is False
    assert any("EUR" in e for e in result["errors"])


async def test_missing_extraction(store):
    """No `extract` node output → ok=False, confidence=0."""
    ctx = AgnesContext(
        run_id=1,
        pipeline_name="document_ingested",
        trigger_source="document.uploaded",
        trigger_payload={},
        node_outputs={},  # no extract key
        store=store,
    )
    result = await validate_totals(ctx)
    assert result["ok"] is False
    assert result["confidence"] == 0.0
