"""Multiplicative confidence gate - compound math, missing-as-0.5, floor override."""
from __future__ import annotations

from backend.orchestration.context import AgnesContext
from backend.orchestration.store.writes import write_tx
from backend.orchestration.tools.confidence_gate import run


def _ctx(store, *, node_outputs: dict) -> AgnesContext:
    return AgnesContext(
        run_id=1,
        pipeline_name="test-confidence-gate",
        trigger_source="manual",
        trigger_payload={},
        node_outputs=node_outputs,
        store=store,
    )


async def test_all_factors_one_passes(store):
    out = await run(
        _ctx(
            store,
            node_outputs={
                "resolve-counterparty": {"confidence": 1.0},
                "classify-gl-account": {"confidence": 1.0},
            },
        )
    )
    assert out["ok"] is True
    assert out["needs_review"] is False
    assert out["computed_confidence"] == 1.0
    assert out["confidence"] == 1.0
    assert out["floor"] == 0.50
    assert ("resolve-counterparty", 1.0) in out["contributing_factors"]


async def test_compound_above_floor_passes(store):
    """0.8 * 0.8 = 0.64 >= 0.50 default floor."""
    out = await run(
        _ctx(
            store,
            node_outputs={
                "resolve-counterparty": {"confidence": 0.8},
                "classify-gl-account": {"confidence": 0.8},
            },
        )
    )
    assert out["ok"] is True
    assert abs(out["computed_confidence"] - 0.64) < 1e-9


async def test_missing_confidence_treated_as_half(store):
    """0.5 (missing) * 0.9 = 0.45 < 0.50 default floor -> fails."""
    out = await run(
        _ctx(
            store,
            node_outputs={
                # No 'confidence' key on this builder's output.
                "build-cash-entry": {"basis": "cash"},
                "resolve-counterparty": {"confidence": 0.9},
            },
        )
    )
    assert out["ok"] is False
    assert out["needs_review"] is True
    assert abs(out["computed_confidence"] - 0.45) < 1e-9


async def test_floor_override_via_threshold_row(store):
    """Inserting a global threshold of 0.30 makes the previously-failing case pass."""
    async with write_tx(store.accounting, store.accounting_lock) as conn:
        await conn.execute(
            "INSERT INTO confidence_thresholds (scope, floor) VALUES ('global', 0.30)"
        )

    out = await run(
        _ctx(
            store,
            node_outputs={
                "build-cash-entry": {"basis": "cash"},  # contributes 0.5 (None)
                "resolve-counterparty": {"confidence": 0.9},
            },
        )
    )
    assert out["floor"] == 0.30
    assert out["ok"] is True
    assert out["needs_review"] is False


async def test_skips_unmentioned_nodes(store):
    """Nodes outside the watched set don't contribute to the compound."""
    out = await run(
        _ctx(
            store,
            node_outputs={
                "resolve-counterparty": {"confidence": 0.8},
                "some-other-node": {"confidence": 0.01},  # ignored
            },
        )
    )
    # Only resolve-counterparty contributes.
    assert out["computed_confidence"] == 0.8
    assert len(out["contributing_factors"]) == 1


async def test_zero_confidence_collapses_chain(store):
    """A 0.0 anywhere collapses the multiplicative product to 0.0."""
    out = await run(
        _ctx(
            store,
            node_outputs={
                "resolve-counterparty": {"confidence": 0.0},
                "classify-gl-account": {"confidence": 1.0},
            },
        )
    )
    assert out["computed_confidence"] == 0.0
    assert out["ok"] is False
    assert out["needs_review"] is True
