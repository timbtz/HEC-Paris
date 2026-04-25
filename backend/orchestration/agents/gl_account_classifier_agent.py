"""Claude-powered GL classifier - fires only when deterministic rules miss.

Source: 04_AGENT_PATTERNS.md:90-107; RealMetaPRD §7.4. The chosen GL is
written back to ``account_rules`` (precedence 20, source 'ai') so the next
identical request hits the deterministic path and skips the LLM call.

The closed enum is sourced from ``chart_of_accounts`` at request time so
adding a new GL code (e.g. 627 for bank fees) without restarting the app
just works. Re-querying every call is cheap on SQLite and avoids stale-cache
debugging during the demo.
"""
from __future__ import annotations

import json
from typing import Any

from ..context import AgnesContext
from ..registries import get_runner
from ..runners.base import AgentResult
from ..store.writes import write_tx


async def _load_chart_codes(ctx: AgnesContext) -> list[str]:
    cur = await ctx.store.accounting.execute(
        "SELECT code FROM chart_of_accounts ORDER BY code"
    )
    rows = await cur.fetchall()
    await cur.close()
    return [r[0] for r in rows]


def _build_summary(ctx: AgnesContext) -> str:
    """Compact JSON summary of what we're trying to classify.

    The agent gets the full counterparty + transaction-or-extraction context
    so it can pick the most-specific account.
    """
    cp = ctx.get("resolve-counterparty") or ctx.get("ai-counterparty-fallback") or {}
    fetch_tx = ctx.get("fetch-transaction")
    extract = ctx.get("extract")

    payload: dict[str, Any] = {"counterparty": cp}
    if fetch_tx is not None:
        payload["transaction"] = fetch_tx
    if extract is not None:
        payload["extraction"] = extract
    return json.dumps(payload, default=str, separators=(",", ":"))


async def _writeback_rule(
    ctx: AgnesContext,
    *,
    cp_legal_name: str,
    gl_account: str,
) -> None:
    """Insert (or skip) a counterparty -> gl_account rule with source='ai'.

    Guarded with NOT EXISTS because ``account_rules`` has no UNIQUE constraint
    in the canonical schema (mirrors migration 0003's idempotency pattern).
    """
    async with write_tx(ctx.store.accounting, ctx.store.accounting_lock) as conn:
        await conn.execute(
            "INSERT INTO account_rules "
            "(pattern_kind, pattern_value, gl_account, precedence, source) "
            "SELECT 'counterparty', ?, ?, 20, 'ai' "
            "WHERE NOT EXISTS ("
            "  SELECT 1 FROM account_rules "
            "  WHERE pattern_kind = 'counterparty' "
            "    AND pattern_value = ? "
            "    AND gl_account = ?"
            ")",
            (cp_legal_name, gl_account, cp_legal_name, gl_account),
        )


async def run(ctx: AgnesContext) -> AgentResult:
    codes = await _load_chart_codes(ctx)

    tool = {
        "name": "submit_gl_account",
        "description": (
            "Classify a transaction or invoice line into a single GL account "
            "from the closed chart of accounts. Pick the most-specific code."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "gl_account": {
                    "type": "string",
                    "enum": codes,
                    "description": "PCG account code, e.g. '626100'.",
                },
                "confidence": {
                    "type": "number",
                    "description": "0.0-1.0; lower triggers a review row.",
                },
                "alternatives": {
                    "type": "array",
                    "items": {"type": "object"},
                },
                "vat_rate_bp": {
                    "type": ["integer", "null"],
                    "description": "VAT rate in basis points (2000 = 20%).",
                },
            },
            "required": ["gl_account", "confidence"],
        },
    }

    summary = _build_summary(ctx)
    system = (
        "Classify a transaction or invoice line into a GL account from the "
        "closed chart of accounts. Choose the most-specific account."
    )
    messages = [
        {
            "role": "user",
            "content": (
                "Classify the following accounting event. Reply by calling "
                "the submit_gl_account tool.\n\n"
                f"Event:\n{summary}"
            ),
        }
    ]

    runner = get_runner("anthropic")
    result = await runner.run(
        ctx=ctx,
        system=system,
        tools=[tool],
        messages=messages,
        model="claude-sonnet-4-6",
        max_tokens=512,
        temperature=0.0,
    )

    # Cache writeback - so the next identical request hits the deterministic
    # cascade and skips the LLM call.
    parsed = result.output if isinstance(result.output, dict) else None
    cp = ctx.get("resolve-counterparty") or ctx.get("ai-counterparty-fallback") or {}
    cp_legal_name = cp.get("counterparty_legal_name") if isinstance(cp, dict) else None
    chosen_gl = parsed.get("gl_account") if isinstance(parsed, dict) else None

    if cp_legal_name and chosen_gl:
        await _writeback_rule(ctx, cp_legal_name=cp_legal_name, gl_account=chosen_gl)

    return result
