"""Deterministic GL classifier - cascade order + miss returns None.

Migration 0003 seeds the rules used here:
  ('counterparty', 'Anthropic') -> 626100 @ precedence 10
  ('counterparty', 'SNCF')      -> 624    @ precedence 10
  ('mcc', '5814')               -> 6257   @ precedence 50
"""
from __future__ import annotations

from backend.orchestration.context import FingentContext
from backend.orchestration.tools.gl_account_classifier import run


def _ctx(store, *, node_outputs: dict | None = None) -> FingentContext:
    return FingentContext(
        run_id=1,
        pipeline_name="test-gl-classifier",
        trigger_source="manual",
        trigger_payload={},
        node_outputs=node_outputs or {},
        store=store,
    )


async def test_counterparty_hit(store):
    out = await run(
        _ctx(
            store,
            node_outputs={
                "resolve-counterparty": {"counterparty_legal_name": "Anthropic"},
            },
        )
    )
    assert out["gl_account"] == "626100"
    assert out["confidence"] == 1.0
    assert isinstance(out["rule_id"], int)


async def test_counterparty_via_ai_fallback(store):
    """Cascade falls through to ai-counterparty-fallback when primary missing."""
    out = await run(
        _ctx(
            store,
            node_outputs={
                "ai-counterparty-fallback": {"counterparty_legal_name": "SNCF"},
            },
        )
    )
    assert out["gl_account"] == "624"
    assert out["confidence"] == 1.0


async def test_mcc_fallback_when_counterparty_unknown(store):
    """No counterparty match but Swan transaction has an MCC - hit MCC rule."""
    out = await run(
        _ctx(
            store,
            node_outputs={
                "resolve-counterparty": {"counterparty_legal_name": "UnknownVendor"},
                "fetch-transaction": {"mcc": "5814"},
            },
        )
    )
    assert out["gl_account"] == "6257"
    assert out["confidence"] == 1.0


async def test_miss_returns_none(store):
    """No counterparty match and no MCC -> gl_account=None for downstream gating."""
    out = await run(
        _ctx(
            store,
            node_outputs={
                "resolve-counterparty": {"counterparty_legal_name": "TotallyUnknown"},
            },
        )
    )
    assert out["gl_account"] is None
    assert out["confidence"] is None
    assert out["rule_id"] is None


async def test_empty_context_returns_none(store):
    """No counterparty and no MCC at all - clean miss."""
    out = await run(_ctx(store))
    assert out["gl_account"] is None
