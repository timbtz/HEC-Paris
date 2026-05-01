"""Claude-powered GL classifier - fires only when deterministic rules miss.

Source: 04_AGENT_PATTERNS.md:90-107; RealMetaPRD §7.4. The chosen GL is
written back to ``account_rules`` (precedence 20, source 'ai') so the next
identical request hits the deterministic path and skips the LLM call.

The closed enum is sourced from ``chart_of_accounts`` at request time so
adding a new GL code (e.g. 627 for bank fees) without restarting the app
just works. Re-querying every call is cheap on SQLite and avoids stale-cache
debugging during the demo.

Phase 4.A (PRD-AutonomousCFO §7.3): before building the system prompt,
we call ``wiki_reader`` with tags ``[gl_accounts, classification]``. The
returned page bodies are appended to the system prompt under
``## Policy reference (Living Rule Wiki)`` and the
``(wiki_page_id, wiki_revision_id)`` pairs flow into the AgentResult's
``wiki_references`` field, threading through prompt_hash + cache + audit.
"""
from __future__ import annotations

import json
from typing import Any

from ..context import FingentContext
from ..registries import default_cerebras_model, default_runner, get_runner
from ..runners.base import AgentResult
from ..store.writes import write_tx
from ..tools import wiki_reader as wiki_reader_tool


async def _load_chart_codes(ctx: FingentContext) -> list[str]:
    cur = await ctx.store.accounting.execute(
        "SELECT code FROM chart_of_accounts ORDER BY code"
    )
    rows = await cur.fetchall()
    await cur.close()
    codes = [r[0] for r in rows]
    # Cerebras strict-mode caps enums at 500 entries (CEREBRAS_STACK_REFERENCE
    # §5). HEC Paris demo CoA is ~80 codes; raise loudly if a future migration
    # crosses the boundary so we catch it before the model rejects the call.
    if len(codes) > 500:
        raise RuntimeError(
            f"chart_of_accounts has {len(codes)} entries — Cerebras strict "
            "mode caps closed-list enums at 500. Split the schema or fall "
            "back to a free-form pick."
        )
    return codes


def _build_summary(ctx: FingentContext) -> str:
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
    ctx: FingentContext,
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


async def run(ctx: FingentContext) -> AgentResult:
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
                    "items": {
                        # Explicit shape so Cerebras strict-mode accepts it.
                        "type": "object",
                        "properties": {
                            "gl_account": {"type": "string"},
                            "confidence": {"type": "number"},
                        },
                        "required": ["gl_account", "confidence"],
                    },
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

    # Phase 4.A — pull Living Rule Wiki pages for GL classification before
    # building the prompt. `jurisdiction` is read from ctx.metadata when an
    # upstream resolver/onboarding step set it; otherwise unfiltered.
    jurisdiction = ctx.metadata.get("jurisdiction") if isinstance(ctx.metadata, dict) else None
    wiki_payload = await wiki_reader_tool.fetch(
        ctx,
        tags=["gl_accounts", "classification"],
        jurisdiction=jurisdiction,
    )
    wiki_pages = wiki_payload.get("pages") or []
    wiki_references: list[tuple[int, int]] = [
        (int(p["page_id"]), int(p["revision_id"])) for p in wiki_pages
    ]

    base_system = (
        "Classify a transaction or invoice line into a GL account from the "
        "closed chart of accounts. Choose the most-specific account."
    )
    if wiki_pages:
        # Concatenate verbatim — the wiki body is the policy frame.
        policy_blocks = "\n\n".join(
            f"### {p['title']} ({p['path']}, rev {p['revision_number']})\n\n{p['body_md']}"
            for p in wiki_pages
        )
        system = (
            f"{base_system}\n\n"
            "## Policy reference (Living Rule Wiki)\n\n"
            f"{policy_blocks}"
        )
    else:
        # Zero matches → behave exactly like the pre-wiki agent.
        system = base_system

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

    runner_key = default_runner()
    model = (
        default_cerebras_model("classifier")
        if runner_key == "pydantic_ai"
        else "claude-sonnet-4-6"
    )
    runner = get_runner(runner_key)
    result = await runner.run(
        ctx=ctx,
        system=system,
        tools=[tool],
        messages=messages,
        model=model,
        max_tokens=256,  # was 512 — single-enum pick + confidence is compact.
        temperature=0.0,
        wiki_context=wiki_references,
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
