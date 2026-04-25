"""`when:` predicates for the Phase 3 reporting pipelines.

Pure functions of `ctx`. No I/O, no globals. Mirror the style of
`conditions/gating.py`.
"""
from __future__ import annotations

from typing import Any

from ..context import AgnesContext


_REPORT_CONFIDENCE_FLOOR = 0.75


def _node_output_dict(node: Any) -> dict[str, Any]:
    """Coerce a node output (dict or AgentResult) to a dict."""
    if node is None:
        return {}
    if hasattr(node, "output"):
        out = node.output
        return out if isinstance(out, dict) else {}
    if isinstance(node, dict):
        return node
    return {}


def period_open(ctx: AgnesContext) -> bool:
    """True if the trial-balance node reports the period as 'open'.

    Reads `period_status` off `compute-trial-balance`'s output (set by
    `period_aggregator._resolve_period`).
    """
    out = ctx.get("compute-trial-balance") or {}
    return out.get("period_status") == "open"


def period_closeable(ctx: AgnesContext) -> bool:
    """True if the period status is `open` or `closing`.

    `validate-period` is the gate node that decides whether to run the
    rest of the close pipeline. We look at `compute-trial-balance` here
    because the validate node aliases to the same tool.
    """
    out = ctx.get("compute-trial-balance") or {}
    status = out.get("period_status")
    return status in ("open", "closing")


def has_anomalies(ctx: AgnesContext) -> bool:
    """True if the anomaly agent surfaced at least one anomaly."""
    out = _node_output_dict(ctx.get("flag-anomalies"))
    anomalies = out.get("anomalies") or []
    return bool(anomalies)


def passes_report_confidence(ctx: AgnesContext) -> bool:
    """True if the period summary's compound confidence ≥ 0.75."""
    out = ctx.get("summarize-period") or {}
    confidence = out.get("confidence")
    if confidence is None:
        return True  # default-pass (no confidence published yet)
    return float(confidence) >= _REPORT_CONFIDENCE_FLOOR
