"""Anomaly-detection agent for the period-close / vat-return pipelines.

Reads the trial balance + VAT-return totals + open accrual list (built
by the upstream tools) and proposes zero-or-more anomalies with per-item
confidence. The agent itself does NOT write to `period_reports` or
`review_queue`; downstream YAML nodes handle side effects.

Mirrors `gl_account_classifier_agent.py` structurally: build a tool
JSONSchema, call `get_runner('anthropic').run`, parse `result.output`.

Tool-use schema closes the `kind` field to a small enum so the executor
can route on it deterministically.
"""
from __future__ import annotations

import json
from typing import Any

from ..context import AgnesContext
from ..registries import default_cerebras_model, default_runner, get_runner
from ..runners.base import AgentResult


_ANOMALY_KINDS = (
    "vat_mismatch",
    "balance_drift",
    "missing_accrual",
    "outlier_expense",
    "duplicate_entry",
)


def _build_summary(ctx: AgnesContext) -> str:
    trial = ctx.get("compute-trial-balance") or {}
    open_entries = ctx.get("compute-open-entries") or {}
    vat = ctx.get("compute-vat") or {}

    payload: dict[str, Any] = {
        "trial_balance": {
            "lines": trial.get("trial_balance", []),
            "total_debit_cents": trial.get("total_debit_cents", 0),
            "total_credit_cents": trial.get("total_credit_cents", 0),
            "balanced": trial.get("balanced", True),
        },
        "open_accruals": {
            "count": open_entries.get("count", 0),
            "entries": open_entries.get("open_entries", [])[:20],
        },
    }
    if vat:
        payload["vat_return"] = {
            "lines": vat.get("lines", []),
            "totals": vat.get("totals", {}),
        }
    return json.dumps(payload, default=str, separators=(",", ":"))


async def run(ctx: AgnesContext) -> AgentResult:
    tool = {
        "name": "submit_anomalies",
        "description": (
            "Inspect the period's trial balance, open accruals, and VAT "
            "return. Propose zero-or-more anomalies with per-item "
            "confidence. Use `confidence: 1.0` only for clearly-broken "
            "data (e.g., trial balance does not sum to zero)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "anomalies": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "kind": {"type": "string", "enum": list(_ANOMALY_KINDS)},
                            "description": {"type": "string"},
                            "evidence": {"type": "string"},
                            "line_ids": {
                                "type": "array",
                                "items": {"type": "integer"},
                            },
                            "confidence": {"type": "number"},
                        },
                        "required": ["kind", "description", "confidence"],
                    },
                },
                "overall_confidence": {
                    "type": "number",
                    "description": "0.0–1.0 confidence in the anomaly review.",
                },
            },
            "required": ["anomalies", "overall_confidence"],
        },
    }

    summary = _build_summary(ctx)
    system = (
        "You are an audit assistant reviewing one fiscal period's accounting "
        "data. Flag concrete anomalies you can defend from the supplied "
        "totals — do not speculate. Return zero anomalies if the period "
        "looks clean."
    )
    messages = [
        {
            "role": "user",
            "content": (
                "Review this period summary and call submit_anomalies with "
                "your findings.\n\n"
                f"Summary:\n{summary}"
            ),
        }
    ]

    runner_key = default_runner()
    model = (
        default_cerebras_model("anomaly")
        if runner_key == "pydantic_ai"
        else "claude-sonnet-4-6"
    )
    runner = get_runner(runner_key)
    return await runner.run(
        ctx=ctx,
        system=system,
        tools=[tool],
        messages=messages,
        model=model,
        max_tokens=800,  # was 1024 — anomaly schema is compact.
        temperature=0.0,
    )
