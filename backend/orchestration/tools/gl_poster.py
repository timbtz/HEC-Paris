"""Single chokepoint for journal_entries + journal_lines + decision_traces writes.

Source: RealMetaPRD §6.4 (chokepoint), §7.5 (table shapes), §7.6 invariant 1.
This is the ONLY tool that INSERTs into `journal_entries`. CI grep should
match exactly one file under `backend/orchestration/tools/` for the string
``INSERT INTO journal_entries``.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from ..context import FingentContext
from ..event_bus import publish_event_dashboard
from ..runners.base import AgentResult
from ..store.writes import write_tx


async def _find_closed_period(ctx: FingentContext, entry_date: str) -> str | None:
    """Return the period `code` if `entry_date` falls in a closed period.

    None means: no period covers this date (allowed — e.g. dates outside
    any seeded fiscal period) OR the covering period is open/closing.
    Phase 3 hard rule: a closed period is immutable.
    """
    cur = await ctx.store.accounting.execute(
        "SELECT code FROM accounting_periods "
        "WHERE status = 'closed' "
        "  AND start_date <= ? AND end_date >= ? "
        "LIMIT 1",
        (entry_date, entry_date),
    )
    row = await cur.fetchone()
    await cur.close()
    return row[0] if row is not None else None


def _attribute_source(ctx: FingentContext) -> str:
    """Walk `ctx.node_outputs` for any `AgentResult`-shaped output. If any
    agent contributed upstream, the trace is `'agent'`, else `'rule'`.
    """
    for value in ctx.node_outputs.values():
        if isinstance(value, AgentResult):
            return "agent"
        # Some pipelines may store the unwrapped agent output (a dict with the
        # agent's payload). Treat any node output that looks like an agent
        # decision (has a `prompt_hash` or `model` key) as agent-attributed.
        if isinstance(value, dict) and (
            "prompt_hash" in value or "alternatives" in value
        ):
            return "agent"
    return "rule"


async def post(ctx: FingentContext) -> dict[str, Any]:
    """Persist a built journal entry: entry → lines → one trace per line.

    Reads the built shape from one of:
      - `build-cash-entry`
      - `build-accrual-entry`
      - `build-reversal`

    Validates `SUM(debit) == SUM(credit)`. Mismatch raises `ValueError`.

    Emits `ledger.entry_posted` on the dashboard bus after commit.
    """
    built = (
        ctx.get("build-cash-entry")
        or ctx.get("build-accrual-entry")
        or ctx.get("build-reversal")
        or {}
    )

    if not built:
        return {"status": "skipped", "reason": "no_built_entry"}

    if built.get("skip"):
        return {"status": "skipped", "reason": built.get("reason")}

    lines: list[dict[str, Any]] = built.get("lines") or []
    if not lines:
        return {"status": "skipped", "reason": "no_lines"}

    total_debits = sum(int(line.get("debit_cents", 0)) for line in lines)
    total_credits = sum(int(line.get("credit_cents", 0)) for line in lines)
    if total_debits != total_credits:
        raise ValueError(
            f"unbalanced: dr={total_debits} cr={total_credits} "
            f"(pipeline={ctx.pipeline_name} run={ctx.run_id})"
        )

    basis = built.get("basis", "cash")
    entry_date = built.get("entry_date") or datetime.now(timezone.utc).date().isoformat()
    description = built.get("description")
    accrual_link_id = built.get("accrual_link_id")
    reversal_of_id = built.get("reversal_of_id")
    confidence = (
        (ctx.get("gate-confidence") or {}).get("computed_confidence")
        if isinstance(ctx.get("gate-confidence"), dict)
        else None
    )
    if confidence is None:
        confidence = built.get("confidence", 1.0)

    source = _attribute_source(ctx)
    parent_event_id = (
        (ctx.trigger_payload or {}).get("eventId")
        or (ctx.trigger_payload or {}).get("sha256")
    )

    # Period-lock enforcement: refuse to post into an `accounting_periods`
    # row whose status is 'closed'. Read-only check; uses the existing
    # `accounting` connection without `write_tx` because no write happens.
    closed_period = await _find_closed_period(ctx, entry_date)
    if closed_period is not None:
        raise RuntimeError(
            f"period {closed_period} is closed; "
            f"cannot post entry_date={entry_date}"
        )

    posted_at = datetime.now(timezone.utc).isoformat()

    async with write_tx(ctx.store.accounting, ctx.store.accounting_lock) as conn:
        cur = await conn.execute(
            "INSERT INTO journal_entries "
            "(basis, entry_date, description, source_pipeline, source_run_id, "
            " status, accrual_link_id, reversal_of_id, posted_at) "
            "VALUES (?, ?, ?, ?, ?, 'posted', ?, ?, ?)",
            (
                basis,
                entry_date,
                description,
                ctx.pipeline_name,
                ctx.run_id,
                accrual_link_id,
                reversal_of_id,
                posted_at,
            ),
        )
        entry_id = cur.lastrowid
        await cur.close()
        assert entry_id is not None

        for line in lines:
            cur = await conn.execute(
                "INSERT INTO journal_lines "
                "(entry_id, account_code, debit_cents, credit_cents, "
                " counterparty_id, swan_transaction_id, document_id, description) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    entry_id,
                    line["account_code"],
                    int(line.get("debit_cents", 0)),
                    int(line.get("credit_cents", 0)),
                    line.get("counterparty_id"),
                    line.get("swan_transaction_id"),
                    line.get("document_id"),
                    line.get("description"),
                ),
            )
            line_id = cur.lastrowid
            await cur.close()
            assert line_id is not None

            await conn.execute(
                "INSERT INTO decision_traces "
                "(line_id, source, rule_id, confidence, "
                " agent_decision_id_logical, parent_event_id, "
                " approver_id, approved_at) "
                "VALUES (?, ?, NULL, ?, NULL, ?, NULL, NULL)",
                (line_id, source, float(confidence), parent_event_id),
            )

    await publish_event_dashboard({
        "event_type": "ledger.entry_posted",
        "ts": datetime.now(timezone.utc).isoformat(),
        "data": {
            "entry_id": int(entry_id),
            "basis": basis,
            "entry_date": entry_date,
            "total_cents": int(total_debits),
            "lines": len(lines),
            "run_id": ctx.run_id,
            "employee_id": ctx.employee_id,
        },
    })

    return {
        "entry_id": int(entry_id),
        "status": "posted",
        "lines": len(lines),
        "total_cents": int(total_debits),
    }
