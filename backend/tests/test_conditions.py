"""Pure-function condition tests - no I/O, no DB, no fixtures beyond ctx."""
from __future__ import annotations

from backend.orchestration.conditions.counterparty import unresolved
from backend.orchestration.conditions import documents as cond_documents
from backend.orchestration.conditions import gating as cond_gating
from backend.orchestration.conditions.gl import unclassified
from backend.orchestration.context import AgnesContext


def _ctx(node_outputs: dict) -> AgnesContext:
    # store is unused by pure conditions; pass None.
    return AgnesContext(
        run_id=1,
        pipeline_name="test-conditions",
        trigger_source="manual",
        trigger_payload={},
        node_outputs=node_outputs,
        store=None,  # type: ignore[arg-type]
    )


def test_gl_unclassified_when_node_missing():
    """Tool didn't run yet -> treated as unclassified so the AI fallback fires."""
    assert unclassified(_ctx({})) is True


def test_gl_unclassified_when_gl_account_is_none():
    assert unclassified(_ctx({"classify-gl-account": {"gl_account": None}})) is True


def test_gl_classified_when_gl_account_set():
    assert unclassified(
        _ctx({"classify-gl-account": {"gl_account": "626100", "confidence": 1.0}})
    ) is False


def test_gl_unclassified_when_output_is_empty_dict():
    assert unclassified(_ctx({"classify-gl-account": {}})) is True


# --------------------------------------------------------------------------- #
# counterparty.unresolved — gate that triggers the AI fallback agent
# --------------------------------------------------------------------------- #


def test_counterparty_unresolved_when_node_missing():
    """No resolver output yet -> treat as unresolved so the AI fallback fires."""
    assert unresolved(_ctx({})) is True


def test_counterparty_unresolved_when_id_is_none():
    assert unresolved(
        _ctx({"resolve-counterparty": {"counterparty_id": None, "confidence": 0.0}})
    ) is True


def test_counterparty_resolved_when_id_present():
    assert unresolved(
        _ctx({"resolve-counterparty": {"counterparty_id": 42, "confidence": 1.0}})
    ) is False


def test_counterparty_unresolved_when_output_is_empty_dict():
    assert unresolved(_ctx({"resolve-counterparty": {}})) is True


# --------------------------------------------------------------------------- #
# gating.passes_confidence — gates the post-entry node
# --------------------------------------------------------------------------- #


def test_passes_confidence_true_when_gate_ok():
    assert cond_gating.passes_confidence(
        _ctx({"gate-confidence": {"ok": True, "computed_confidence": 0.9}})
    ) is True


def test_passes_confidence_false_when_gate_not_ok():
    assert cond_gating.passes_confidence(
        _ctx({"gate-confidence": {"ok": False, "computed_confidence": 0.1}})
    ) is False


def test_passes_confidence_false_when_gate_missing():
    assert cond_gating.passes_confidence(_ctx({})) is False


def test_passes_confidence_false_when_gate_empty_dict():
    assert cond_gating.passes_confidence(_ctx({"gate-confidence": {}})) is False


# --------------------------------------------------------------------------- #
# gating.needs_review — gates the review-queue node
# --------------------------------------------------------------------------- #


def test_needs_review_true_when_gate_failed():
    assert cond_gating.needs_review(
        _ctx({"gate-confidence": {"ok": False, "computed_confidence": 0.1}})
    ) is True


def test_needs_review_false_when_gate_ok():
    assert cond_gating.needs_review(
        _ctx({"gate-confidence": {"ok": True, "computed_confidence": 0.9}})
    ) is False


def test_needs_review_false_when_gate_missing():
    # If the gate never ran we can't claim review is needed.
    assert cond_gating.needs_review(_ctx({})) is False


# --------------------------------------------------------------------------- #
# gating.posted — gates downstream nodes (envelope decrement, invariants)
# --------------------------------------------------------------------------- #


def test_posted_true_when_status_posted():
    assert cond_gating.posted(
        _ctx({"post-entry": {"status": "posted", "entry_id": 7}})
    ) is True


def test_posted_false_when_status_other():
    assert cond_gating.posted(
        _ctx({"post-entry": {"status": "skipped"}})
    ) is False


def test_posted_false_when_post_entry_missing():
    assert cond_gating.posted(_ctx({})) is False


# --------------------------------------------------------------------------- #
# documents.totals_ok / totals_mismatch — gates document pipeline branches
# --------------------------------------------------------------------------- #


def test_documents_totals_ok_true():
    assert cond_documents.totals_ok(_ctx({"validate": {"ok": True}})) is True


def test_documents_totals_ok_false_when_validate_failed():
    assert cond_documents.totals_ok(
        _ctx({"validate": {"ok": False, "failures": ["sum"]}})
    ) is False


def test_documents_totals_ok_false_when_validate_missing():
    assert cond_documents.totals_ok(_ctx({})) is False


def test_documents_totals_mismatch_true_when_validate_failed():
    assert cond_documents.totals_mismatch(
        _ctx({"validate": {"ok": False, "failures": ["sum"]}})
    ) is True


def test_documents_totals_mismatch_false_when_validate_ok():
    assert cond_documents.totals_mismatch(_ctx({"validate": {"ok": True}})) is False


def test_documents_totals_mismatch_false_when_validate_missing():
    # No validate output -> nothing to call mismatched.
    assert cond_documents.totals_mismatch(_ctx({})) is False
