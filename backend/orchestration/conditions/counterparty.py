"""Condition functions for counterparty-resolution gating.

Source: Phase 2 plan §Task 24; pattern from `conditions/gating.py`. Pure
functions of `ctx` — no I/O, no randomness, no globals.

Used by the routing block in pipelines that need to fall back to the AI
classifier when the deterministic 4-stage cascade misses (or returns a
null counterparty_id).
"""
from __future__ import annotations

from ..context import FingentContext


def unresolved(ctx: FingentContext) -> bool:
    """True iff the resolver returned no counterparty.

    Routes the AI fallback agent. Tolerates a missing node entirely (returns
    True — better to over-trigger the fallback than skip resolution silently).
    """
    out = ctx.get("resolve-counterparty") or {}
    return out.get("counterparty_id") is None
