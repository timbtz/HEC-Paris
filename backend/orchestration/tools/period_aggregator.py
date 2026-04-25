"""Period-aggregation tools used by the `period_close` / `year_end_close`
pipelines.

All three callables are pure-read (no `write_tx`); they assemble the
period summary that `report_renderer.render` writes to `period_reports`.

Confidence is hard-coded `1.0` — these are deterministic SQL aggregations,
not agentic decisions.
"""
from __future__ import annotations

from typing import Any

from ..context import AgnesContext


async def _resolve_period(ctx: AgnesContext) -> dict[str, Any]:
    """Look up the period row from `accounting_periods`.

    Reads `period_code` from `ctx.trigger_payload`. Raises `ValueError`
    if the payload is missing the field or the period code is unknown.
    """
    period_code = (ctx.trigger_payload or {}).get("period_code")
    if not period_code:
        raise ValueError("period_aggregator: trigger_payload missing 'period_code'")
    cur = await ctx.store.accounting.execute(
        "SELECT code, start_date, end_date, status "
        "FROM accounting_periods WHERE code = ?",
        (period_code,),
    )
    row = await cur.fetchone()
    await cur.close()
    if row is None:
        raise ValueError(f"period_aggregator: unknown period_code {period_code!r}")
    return {
        "code": row[0],
        "start_date": row[1],
        "end_date": row[2],
        "status": row[3],
    }


async def compute_trial_balance(ctx: AgnesContext) -> dict[str, Any]:
    """Trial balance for the closing period (accrual basis).

    Returns the per-account sum of debits and credits, and a `balanced`
    flag. The `confidence` is `1.0` for the SQL aggregation; if the trial
    balance is unbalanced (which would indicate a posting bug), the
    confidence drops so downstream `confidence_gate` routes to review.
    """
    period = await _resolve_period(ctx)

    cur = await ctx.store.accounting.execute(
        "SELECT jl.account_code,"
        "       COALESCE(SUM(jl.debit_cents), 0)  AS debit_cents,"
        "       COALESCE(SUM(jl.credit_cents), 0) AS credit_cents "
        "FROM journal_lines jl "
        "JOIN journal_entries je ON je.id = jl.entry_id "
        "WHERE je.status = 'posted' "
        "  AND je.basis = 'accrual' "
        "  AND je.entry_date BETWEEN ? AND ? "
        "GROUP BY jl.account_code "
        "ORDER BY jl.account_code",
        (period["start_date"], period["end_date"]),
    )
    rows = list(await cur.fetchall())
    await cur.close()

    lines = [
        {
            "account_code": r[0],
            "debit_cents": int(r[1]),
            "credit_cents": int(r[2]),
            "balance_cents": int(r[1]) - int(r[2]),
        }
        for r in rows
    ]
    total_debit = sum(l["debit_cents"] for l in lines)
    total_credit = sum(l["credit_cents"] for l in lines)
    balanced = total_debit == total_credit

    return {
        "period_code": period["code"],
        "period_status": period["status"],
        "trial_balance": lines,
        "total_debit_cents": total_debit,
        "total_credit_cents": total_credit,
        "balanced": balanced,
        "confidence": 1.0 if balanced else 0.5,
    }


async def compute_open_entries(ctx: AgnesContext) -> dict[str, Any]:
    """List unpaired accrual entries inside the closing period.

    Returns every accrual entry whose `accrual_link_id IS NULL` AND whose
    `entry_date` falls inside the period. These are entries that should
    have been matched to a cash payment but were not (an indicator that
    the period cannot be cleanly closed yet).
    """
    period = await _resolve_period(ctx)

    cur = await ctx.store.accounting.execute(
        "SELECT je.id, je.entry_date, je.description "
        "FROM journal_entries je "
        "WHERE je.status = 'posted' "
        "  AND je.basis = 'accrual' "
        "  AND je.accrual_link_id IS NULL "
        "  AND je.entry_date BETWEEN ? AND ? "
        "ORDER BY je.entry_date, je.id",
        (period["start_date"], period["end_date"]),
    )
    rows = list(await cur.fetchall())
    await cur.close()

    entries = [
        {"entry_id": int(r[0]), "entry_date": r[1], "description": r[2]}
        for r in rows
    ]
    return {
        "period_code": period["code"],
        "open_entries": entries,
        "count": len(entries),
        "confidence": 1.0,
    }


async def summarize_period(ctx: AgnesContext) -> dict[str, Any]:
    """Final aggregator — bundles upstream node outputs for `report_renderer`.

    Pulls trial balance, open entries, anomalies (from `flag-anomalies`),
    and VAT (from `compute-vat`, if present in this pipeline). Returns a
    single dict that `report_renderer.render` serializes to disk.

    Confidence is the multiplicative product of upstream confidences,
    consistent with `confidence_gate.run`.
    """
    period = await _resolve_period(ctx)

    trial = ctx.get("compute-trial-balance") or {}
    open_entries = ctx.get("compute-open-entries") or {}
    anomalies_node = ctx.get("flag-anomalies")
    vat = ctx.get("compute-vat") or None

    # Anomalies node may be either an `AgentResult` (with `.output`) or a
    # plain dict (for tests that stub the agent inline).
    anomalies_payload: dict[str, Any] = {}
    anomaly_confidence: float | None = None
    if anomalies_node is not None:
        if hasattr(anomalies_node, "output"):
            output = anomalies_node.output
            anomalies_payload = output if isinstance(output, dict) else {}
            anomaly_confidence = getattr(anomalies_node, "confidence", None)
        elif isinstance(anomalies_node, dict):
            anomalies_payload = anomalies_node
            anomaly_confidence = anomalies_node.get("overall_confidence")

    # Multiplicative confidence: every upstream confidence ∈ (0, 1].
    confidences: list[float] = []
    for c in (
        trial.get("confidence"),
        open_entries.get("confidence"),
        (vat or {}).get("confidence"),
        anomaly_confidence,
    ):
        if c is not None:
            confidences.append(float(c))
    compound = 1.0
    for c in confidences:
        compound *= c

    return {
        "period_code": period["code"],
        "period_status": period["status"],
        "start_date": period["start_date"],
        "end_date": period["end_date"],
        "trial_balance": trial.get("trial_balance", []),
        "trial_balance_totals": {
            "total_debit_cents": trial.get("total_debit_cents", 0),
            "total_credit_cents": trial.get("total_credit_cents", 0),
            "balanced": trial.get("balanced", False),
        },
        "open_entries": open_entries.get("open_entries", []),
        "open_entries_count": open_entries.get("count", 0),
        "vat": vat,
        "anomalies": anomalies_payload.get("anomalies", []),
        "confidence": compound if confidences else 1.0,
    }
