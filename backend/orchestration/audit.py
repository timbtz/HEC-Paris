"""propose → checkpoint → commit — the audit spine.

Source: RealMetaPRD §6.4 lines 529-533 + §7.5 audit.db schema.
PRD1_VALIDATION_BRIEFING G3: agent_decisions has the seven extra LLM-
observability columns beyond the original PRD1 shape.

Both INSERTs (decision + cost) live in **one** `write_tx` block; partial
writes here corrupt the audit story (RealMetaPRD §14 risk #8).
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone

import aiosqlite

from .cost import micro_usd
from .gamification import auto_credit_for_decision
from .runners.base import AgentResult
from .store.writes import write_tx


async def propose_checkpoint_commit(
    *,
    audit_db: aiosqlite.Connection,
    audit_lock: asyncio.Lock,
    run_id: int,
    node_id: str,
    result: AgentResult,
    runner: str,
    employee_id: int | None,
    provider: str,
    source: str = "agent",
    wiki_references: list[tuple[int, int]] | None = None,
) -> int:
    """Write one agent_decisions + one agent_costs row atomically.

    Returns the new `agent_decisions.id` (used as `decision_id` in
    `agent_costs`).

    Phase 4.A (PRD-AutonomousCFO §7.3): `wiki_references` is the list of
    `(wiki_page_id, wiki_revision_id)` pairs the agent cited. We store
    only the FIRST pair in the new `agent_decisions.wiki_page_id` /
    `wiki_revision_id` columns; a multi-citation join table is deferred
    (PRD §7.3 deferred research item #1). When the kwarg is omitted we
    fall back to `result.wiki_references` so callers don't have to plumb
    the same data twice.
    """
    cost = micro_usd(result.usage, provider, result.model)
    completed_at = datetime.now(timezone.utc).isoformat()

    refs = wiki_references
    if refs is None:
        refs = list(getattr(result, "wiki_references", None) or [])
    first_page_id: int | None = None
    first_revision_id: int | None = None
    if refs:
        first = refs[0]
        if isinstance(first, dict):
            # Defensive — accept rich-dict citations too.
            first_page_id = (
                int(first["page_id"]) if first.get("page_id") is not None else None
            )
            first_revision_id = (
                int(first["revision_id"]) if first.get("revision_id") is not None else None
            )
        elif isinstance(first, (tuple, list)) and len(first) >= 2:
            first_page_id = int(first[0]) if first[0] is not None else None
            first_revision_id = int(first[1]) if first[1] is not None else None

    async with write_tx(audit_db, audit_lock) as conn:
        cur = await conn.execute(
            "INSERT INTO agent_decisions ("
            "  run_id_logical, node_id, source, runner, model, response_id,"
            "  prompt_hash, alternatives_json, confidence,"
            "  latency_ms, finish_reason, temperature, seed, completed_at,"
            "  wiki_page_id, wiki_revision_id"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                run_id, node_id, source, runner, result.model, result.response_id,
                result.prompt_hash,
                json.dumps(result.alternatives) if result.alternatives else None,
                result.confidence,
                result.latency_ms, result.finish_reason,
                result.temperature, result.seed,
                completed_at,
                first_page_id, first_revision_id,
            ),
        )
        decision_id = cur.lastrowid
        if decision_id is None:  # pragma: no cover — defensive
            raise RuntimeError("agent_decisions insert returned no rowid")
        await conn.execute(
            "INSERT INTO agent_costs ("
            "  decision_id, employee_id, provider, model,"
            "  input_tokens, output_tokens, cache_read_tokens, cache_write_tokens,"
            "  reasoning_tokens, cost_micro_usd"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                decision_id, employee_id, provider, result.model,
                result.usage.input_tokens, result.usage.output_tokens,
                result.usage.cache_read_tokens, result.usage.cache_write_tokens,
                result.usage.reasoning_tokens, cost,
            ),
        )
        # Phase 4.B: gamification auto-credit. One approved task_completion
        # row per agent_decisions row, idempotent on agent_decision_id.
        await auto_credit_for_decision(
            conn,
            employee_id=employee_id,
            agent_decision_id=int(decision_id),
            runner=runner,
        )
        return decision_id
