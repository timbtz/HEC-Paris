"""Validator companion — checks extraction sums.

Source: RealMetaPRD §7.3 (validate node).

Reads the extraction dict from `ctx.get('extract')` (the output stored by the
agent in node_outputs is the parsed `submit_invoice` tool input). Returns a
dict with `ok`, `errors`, and `confidence` so the executor can route to the
deterministic fallback / review queue when the totals don't reconcile.
"""
from __future__ import annotations

from typing import Any

from ..context import FingentContext


_VAT_TOLERANCE_CENTS = 1


async def validate_totals(ctx: FingentContext) -> dict[str, Any]:
    """Three-way check: items sum, subtotal+VAT, currency.

    1. `sum(item.amount_cents) == subtotal_cents`            (strict)
    2. `subtotal_cents + vat_cents == total_cents`           (±1 cent)
    3. `currency == 'EUR'`                                   (strict)
    """
    extraction = ctx.get("extract") or {}
    if not isinstance(extraction, dict):
        return {
            "ok": False,
            "errors": [f"extraction is not a dict: got {type(extraction).__name__}"],
            "confidence": 0.0,
        }

    errors: list[str] = []

    items = extraction.get("items") or []
    subtotal_cents = extraction.get("subtotal_cents", 0)
    vat_cents = extraction.get("vat_cents", 0)
    total_cents = extraction.get("total_cents", 0)
    currency = extraction.get("currency")

    # 1. Strict line-item sum.
    items_sum = 0
    for item in items:
        if not isinstance(item, dict):
            errors.append(f"non-dict item in items: {item!r}")
            continue
        amount = item.get("amount_cents")
        if not isinstance(amount, int):
            errors.append(f"non-integer amount_cents in item: {item!r}")
            continue
        items_sum += amount

    if items_sum != subtotal_cents:
        errors.append(
            f"items sum {items_sum} != subtotal_cents {subtotal_cents}"
        )

    # 2. subtotal + VAT == total, ±1 cent for VAT rounding.
    expected_total = (subtotal_cents or 0) + (vat_cents or 0)
    if abs(expected_total - (total_cents or 0)) > _VAT_TOLERANCE_CENTS:
        errors.append(
            f"subtotal+vat {expected_total} != total_cents {total_cents} "
            f"(tolerance ±{_VAT_TOLERANCE_CENTS})"
        )

    # 3. EUR only.
    if currency != "EUR":
        errors.append(f"currency must be 'EUR'; got {currency!r}")

    ok = not errors
    return {
        "ok": ok,
        "errors": errors,
        "confidence": 1.0 if ok else 0.0,
    }
