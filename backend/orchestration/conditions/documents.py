"""Conditions for document/extraction validation.

Source: Phase 2 plan task 35; pattern from `conditions/gating.py`. Pure
functions of `ctx` — no I/O, no randomness, no globals.

The `validate` node returns `{ok: bool, ...}` after checking that the
extracted PDF totals tie out (subtotal + VAT == total within ±1 cent).
"""
from __future__ import annotations

from ..context import AgnesContext


def totals_ok(ctx: AgnesContext) -> bool:
    out = ctx.get("validate") or {}
    return bool(out.get("ok"))


def totals_mismatch(ctx: AgnesContext) -> bool:
    out = ctx.get("validate") or {}
    return bool(out) and not out.get("ok")
