"""Multiplicative confidence gate - collects upstream confidences and gates the post.

Source: 04_AGENT_PATTERNS.md:132-146 (compound_confidence; missing -> 0.5).

Walks a fixed set of upstream node IDs and reads ``confidence`` off each.
Compound score = product of all factors (None coerced to 0.5). Gate ``ok``
iff compound >= floor. Floor comes from ``confidence_thresholds`` (scope
'global', latest row); defaults to 0.50 if no row exists.

The output emits both ``ok`` and ``needs_review = not ok`` so existing
``conditions.gating:needs_review`` (which reads ``gate.get('needs_review')``)
continues to work unchanged. ``confidence`` is also emitted so the gate's own
output can be folded into a downstream compound chain if needed.
"""
from __future__ import annotations

from typing import Any

from ..context import FingentContext


# The set of nodes whose confidence we fold into the gate. Includes both the
# deterministic resolvers (which emit confidence=1.0 on hit) and their AI
# fallbacks; whichever of the pair fired contributes its score.
_NODE_IDS: tuple[str, ...] = (
    "resolve-counterparty",
    "ai-counterparty-fallback",
    "classify-gl-account",
    "ai-account-fallback",
    "build-cash-entry",
    "build-accrual-entry",
    "extract",
    "validate",
)


def _confidence_of(out: Any) -> float | None:
    """Pull ``confidence`` off a node output, regardless of dict vs AgentResult.

    The executor stores the agent's ``result.output`` (the parsed tool input
    dict) in ``ctx.node_outputs``, so for both tool nodes and agent nodes the
    value is a dict whose ``confidence`` key we can read directly.
    """
    if isinstance(out, dict):
        c = out.get("confidence")
        if isinstance(c, (int, float)):
            return float(c)
    return None


async def _read_floor(ctx: FingentContext) -> float:
    cur = await ctx.store.accounting.execute(
        "SELECT floor FROM confidence_thresholds "
        "WHERE scope = 'global' "
        "ORDER BY id DESC LIMIT 1"
    )
    row = await cur.fetchone()
    await cur.close()
    if row is None:
        return 0.50
    return float(row[0])


async def run(ctx: FingentContext) -> dict[str, Any]:
    contributing: list[tuple[str, float | None]] = []
    for node_id in _NODE_IDS:
        if node_id not in ctx.node_outputs:
            continue
        out = ctx.node_outputs.get(node_id)
        c = _confidence_of(out)
        contributing.append((node_id, c))

    compound = 1.0
    for _node_id, c in contributing:
        compound *= 0.5 if c is None else c

    floor = await _read_floor(ctx)
    ok = compound >= floor

    return {
        "ok": ok,
        "needs_review": not ok,
        "computed_confidence": compound,
        "confidence": compound,
        "contributing_factors": contributing,
        "floor": floor,
    }
