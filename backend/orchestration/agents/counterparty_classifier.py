"""Claude-powered counterparty classifier — runs only when deterministic stages miss.

Source: 04_AGENT_PATTERNS.md:90-107 (closed-list classifier shape);
RealMetaPRD §7.10 (AgentResult contract); §6.4 (cache writeback so the next
deterministic stage hits).

Triggered by `conditions.counterparty:unresolved`. The model picks from the
top-20 most recent counterparties or returns null with low confidence; the
executor handles the audit row write via `propose_checkpoint_commit`.

Cache writeback: on a non-null pick, we INSERT OR IGNORE a row into
`counterparty_identifiers` with `source='ai'` so a future tx with the same
IBAN / merchant / label short-circuits in `tools/counterparty_resolver.py`.
"""
from __future__ import annotations

import json
from typing import Any

from ..context import AgnesContext
from ..registries import default_cerebras_model, default_runner, get_runner
from ..runners.base import AgentResult
from ..store.writes import write_tx


_SYSTEM_PROMPT = (
    "You classify a transaction's counterparty by selecting from a closed "
    "list of known counterparties. If none fit, return null with low "
    "confidence. Always call submit_counterparty exactly once."
)


_SUBMIT_TOOL: dict[str, Any] = {
    "name": "submit_counterparty",
    "description": (
        "Submit the chosen counterparty id (or null if none of the candidates "
        "fits), a confidence in [0, 1], and optional alternatives."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "counterparty_id": {"type": ["integer", "null"]},
            "confidence": {"type": "number"},
            "alternatives": {
                "type": "array",
                "items": {
                    # Explicit shape so Cerebras strict-mode accepts it.
                    # Anthropic accepts the same dict; cache_writeback at
                    # _writeback_ai_pick reads only counterparty_id/confidence.
                    "type": "object",
                    "properties": {
                        "counterparty_id": {"type": ["integer", "null"]},
                        "confidence": {"type": "number"},
                    },
                    "required": ["counterparty_id", "confidence"],
                },
            },
        },
        "required": ["counterparty_id", "confidence"],
    },
}


def _summarize_swan_tx(tx: dict[str, Any]) -> str:
    """Compact human-readable summary of a Swan transaction."""
    amount = tx.get("amount") or {}
    raw_cp = tx.get("counterparty")
    if isinstance(raw_cp, dict):
        cp_label = raw_cp.get("name")
    else:
        cp_label = raw_cp if isinstance(raw_cp, str) else None

    parts = [
        f"side={tx.get('side', '?')}",
        f"type={tx.get('type', '?')}",
        f"amount={amount.get('value', '?')} {amount.get('currency', '?')}",
        f"counterparty_label={cp_label or tx.get('counterparty_label', '?')}",
    ]
    if tx.get("mcc"):
        parts.append(f"mcc={tx['mcc']}")
    if tx.get("paymentReference"):
        parts.append(f"reference={tx['paymentReference']}")
    return ", ".join(parts)


def _summarize_extraction(extraction: dict[str, Any]) -> str:
    parts = [
        f"supplier_name={extraction.get('supplier_name', '?')}",
        f"amount_cents={extraction.get('amount_cents', '?')}",
        f"issue_date={extraction.get('issue_date', '?')}",
    ]
    if extraction.get("vat_number"):
        parts.append(f"vat={extraction['vat_number']}")
    return ", ".join(parts)


async def _top_candidates(ctx: AgnesContext, limit: int = 20) -> list[dict[str, Any]]:
    cur = await ctx.store.accounting.execute(
        "SELECT id, legal_name, kind FROM counterparties "
        "ORDER BY created_at DESC LIMIT ?",
        (limit,),
    )
    rows = await cur.fetchall()
    await cur.close()
    return [{"id": int(r[0]), "legal_name": r[1], "kind": r[2]} for r in rows]


def _format_candidates(candidates: list[dict[str, Any]]) -> str:
    """Numbered, single-line per candidate; ends with the explicit null option."""
    lines = [
        f"{i + 1}. id={c['id']} legal_name={c['legal_name']!r} kind={c['kind']}"
        for i, c in enumerate(candidates)
    ]
    lines.append("0. None of the above (return counterparty_id=null)")
    return "\n".join(lines)


async def _writeback_ai_pick(
    ctx: AgnesContext,
    counterparty_id: int,
    confidence: float,
    *,
    tx: dict[str, Any] | None,
    extraction: dict[str, Any] | None,
) -> None:
    """Pin the AI's pick to the exact identifier the model saw.

    Order of preference: IBAN (Swan tx) → name_alias (extraction supplier
    name → counterparty_label fallback). All inserts are INSERT OR IGNORE so
    duplicates are silently dropped.
    """
    identifier_type: str | None = None
    identifier: str | None = None

    if tx:
        cp = tx.get("counterparty")
        if isinstance(cp, dict) and cp.get("iban"):
            identifier_type, identifier = "iban", cp["iban"]
        elif isinstance(tx.get("creditor"), dict) and tx["creditor"].get("iban"):
            identifier_type, identifier = "iban", tx["creditor"]["iban"]
        elif isinstance(tx.get("debtor"), dict) and tx["debtor"].get("iban"):
            identifier_type, identifier = "iban", tx["debtor"]["iban"]

    if identifier is None:
        # Fall back to the human-readable label as a name_alias.
        label: str | None = None
        if extraction:
            label = extraction.get("supplier_name") or extraction.get("counterparty_name")
        if not label and tx:
            label = tx.get("counterparty_label")
            if not label:
                raw_cp = tx.get("counterparty")
                if isinstance(raw_cp, dict):
                    label = raw_cp.get("name")
                elif isinstance(raw_cp, str):
                    label = raw_cp
        if label:
            identifier_type, identifier = "name_alias", label

    if identifier_type is None or identifier is None:
        return  # nothing to pin; caller still gets the AgentResult.

    async with write_tx(ctx.store.accounting, ctx.store.accounting_lock) as conn:
        await conn.execute(
            "INSERT OR IGNORE INTO counterparty_identifiers "
            "(counterparty_id, identifier_type, identifier, source, confidence) "
            "VALUES (?, ?, ?, 'ai', ?)",
            (counterparty_id, identifier_type, identifier, confidence),
        )


async def run(ctx: AgnesContext) -> AgentResult:
    """AI fallback. Closed-list pick over top-20 candidates."""
    tx = ctx.get("fetch-transaction") or {}
    extraction = ctx.get("extract") or {}

    if tx:
        summary = _summarize_swan_tx(tx)
    elif extraction:
        summary = _summarize_extraction(extraction)
    else:
        summary = json.dumps(ctx.trigger_payload or {}, separators=(",", ":"))

    candidates = await _top_candidates(ctx)

    user_content = (
        f"Transaction summary: {summary}\n\n"
        f"Candidates:\n{_format_candidates(candidates)}"
    )

    runner_key = default_runner()
    model = (
        default_cerebras_model("classifier")
        if runner_key == "pydantic_ai"
        else "claude-sonnet-4-6"
    )
    runner = get_runner(runner_key)
    result = await runner.run(
        ctx=ctx,
        system=_SYSTEM_PROMPT,
        tools=[_SUBMIT_TOOL],
        messages=[{"role": "user", "content": user_content}],
        model=model,
        max_tokens=256,  # was 512 — closed-list pick is compact.
        temperature=0.0,
    )

    # Cache writeback if the model picked something (non-null counterparty_id).
    output = result.output
    if isinstance(output, dict):
        cp_id = output.get("counterparty_id")
        confidence = output.get("confidence")
        if cp_id is not None and isinstance(confidence, (int, float)):
            await _writeback_ai_pick(
                ctx,
                int(cp_id),
                float(confidence),
                tx=tx if tx else None,
                extraction=extraction if extraction else None,
            )

    return result
