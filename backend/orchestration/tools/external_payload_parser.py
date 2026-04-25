"""Generic CRM/external-event payload parser.

Source: RealMetaPRD §7.2; Phase 2 plan task 41. Stub — Stripe shape only.

The pipeline `external_event.yaml` accepts arbitrary inbound webhooks
(Stripe, Shopify, etc) and routes them through here. We normalize what we
recognize and pass everything else through verbatim.

`expected_payments` write is intentionally skipped for MVP — Phase 3 will
wire that up once we have a richer counterparty resolution path for
external-event provenance.
"""
from __future__ import annotations

from typing import Any

from ..context import AgnesContext


def _provider_from_trigger(trigger_source: str | None) -> str | None:
    """`external.stripe.invoice_paid` -> `stripe`. Returns None if unknown."""
    if not trigger_source:
        return None
    parts = trigger_source.split(".")
    # Shapes seen so far:
    #   `external_event:external.stripe.invoice_paid` (executor convention)
    #   `external.stripe.invoice_paid`                (router-emitted shorthand)
    for chunk in parts:
        if ":" in chunk:
            chunk = chunk.split(":", 1)[1]
        if chunk and chunk != "external" and chunk != "external_event":
            return chunk
    return None


def _parse_stripe(payload: dict[str, Any]) -> dict[str, Any]:
    """Stripe webhook shape — flatten the bits we care about."""
    obj = (payload.get("data") or {}).get("object") or {}
    return {
        "provider": "stripe",
        "event_type": payload.get("type"),
        "payment_intent_id": obj.get("payment_intent"),
        # Stripe amounts are already integer cents.
        "amount_cents": obj.get("amount", 0),
        "currency": obj.get("currency"),
        "raw": payload,
    }


async def run(ctx: AgnesContext) -> dict[str, Any]:
    payload = ctx.trigger_payload or {}
    provider = _provider_from_trigger(ctx.trigger_source)

    if provider == "stripe":
        normalized = _parse_stripe(payload)
        return {
            "normalized": normalized,
            "expected_payment_id": None,  # Phase 3
        }

    # Unknown provider — pass through, log a hint.
    return {
        "normalized": payload,
        "note": "unknown_provider_passthrough",
        "expected_payment_id": None,
    }
