"""Tests for `tools.external_payload_parser:run` (MVP stub).

Asserts:
  - Stripe-shaped payload normalizes correctly.
  - Unknown provider passes through verbatim with a hint.
"""
from __future__ import annotations

import pytest

from backend.orchestration.context import AgnesContext
from backend.orchestration.tools import external_payload_parser


pytestmark = pytest.mark.asyncio


def _ctx(trigger_source: str, trigger_payload: dict) -> AgnesContext:
    return AgnesContext(
        run_id=1,
        pipeline_name="external_event",
        trigger_source=trigger_source,
        trigger_payload=trigger_payload,
        node_outputs={},
        store=None,  # type: ignore[arg-type] — this tool doesn't touch the store
    )


async def test_stripe_invoice_paid_normalizes():
    payload = {
        "type": "invoice.paid",
        "data": {
            "object": {
                "payment_intent": "pi_test_123",
                "amount": 12_345,
                "currency": "eur",
            }
        },
    }
    ctx = _ctx("external_event:external.stripe.invoice_paid", payload)
    out = await external_payload_parser.run(ctx)

    assert out["expected_payment_id"] is None
    norm = out["normalized"]
    assert norm["provider"] == "stripe"
    assert norm["event_type"] == "invoice.paid"
    assert norm["payment_intent_id"] == "pi_test_123"
    assert norm["amount_cents"] == 12_345
    assert norm["currency"] == "eur"
    assert norm["raw"] is payload


async def test_unknown_provider_passes_through():
    payload = {"shopify_event": "order/created", "id": 999}
    ctx = _ctx("external_event:external.shopify.order_created", payload)
    out = await external_payload_parser.run(ctx)

    assert out["normalized"] == payload
    assert out.get("note") == "unknown_provider_passthrough"
    assert out["expected_payment_id"] is None


async def test_stripe_missing_object_safely_handled():
    """Malformed Stripe payload still parses — fields fall back to None / 0."""
    ctx = _ctx("external_event:external.stripe.invoice_paid", {"type": "x.y"})
    out = await external_payload_parser.run(ctx)

    norm = out["normalized"]
    assert norm["provider"] == "stripe"
    assert norm["event_type"] == "x.y"
    assert norm["payment_intent_id"] is None
    assert norm["amount_cents"] == 0
    assert norm["currency"] is None
