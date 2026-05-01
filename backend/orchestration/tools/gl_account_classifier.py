"""Deterministic GL classifier - looks up account_rules by precedence.

Source: RealMetaPRD §7.4 (deterministic-first cascade). Read-only tool.

Cascade order (first match wins, ordered by precedence ASC within each kind):
  1. counterparty legal_name match (seeded at precedence 10, AI writeback at 20)
  2. MCC match (seeded at precedence 50; only available on Swan transactions)

Returns ``{gl_account, confidence, rule_id}``. Miss returns ``gl_account=None``
so the downstream condition (``conditions.gl:unclassified``) can fan out to
the AI fallback agent.
"""
from __future__ import annotations

from typing import Any

from ..context import FingentContext


async def run(ctx: FingentContext) -> dict[str, Any]:
    # Counterparty resolution: prefer the deterministic resolver, fall back
    # to the AI resolver's output (same shape: {counterparty_legal_name: ...}).
    cp = ctx.get("resolve-counterparty") or ctx.get("ai-counterparty-fallback") or {}
    cp_legal_name = cp.get("counterparty_legal_name") if isinstance(cp, dict) else None

    # MCC is Swan-only. Document pipelines never set this.
    fetch_tx = ctx.get("fetch-transaction") or {}
    mcc = fetch_tx.get("mcc") if isinstance(fetch_tx, dict) else None

    conn = ctx.store.accounting

    # 1. Counterparty cascade.
    if cp_legal_name:
        cur = await conn.execute(
            "SELECT id, gl_account FROM account_rules "
            "WHERE pattern_kind = 'counterparty' AND pattern_value = ? "
            "ORDER BY precedence ASC LIMIT 1",
            (cp_legal_name,),
        )
        row = await cur.fetchone()
        await cur.close()
        if row is not None:
            return {
                "gl_account": row[1],
                "confidence": 1.0,
                "rule_id": row[0],
            }

    # 2. MCC cascade (Swan only).
    if mcc is not None:
        cur = await conn.execute(
            "SELECT id, gl_account FROM account_rules "
            "WHERE pattern_kind = 'mcc' AND pattern_value = ? "
            "ORDER BY precedence ASC LIMIT 1",
            (str(mcc),),
        )
        row = await cur.fetchone()
        await cur.close()
        if row is not None:
            return {
                "gl_account": row[1],
                "confidence": 1.0,
                "rule_id": row[0],
            }

    # Miss - downstream condition will fan out to the AI fallback agent.
    return {"gl_account": None, "confidence": None, "rule_id": None}
