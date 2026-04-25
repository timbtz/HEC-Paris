"""Public surface for the orchestration package.

Importing this module registers all production tools, agents, and conditions
so YAML pipelines that reference them resolve in the registry. This is
idempotent — re-imports are no-ops.
"""
from __future__ import annotations

from .registries import register_tool, register_agent, register_condition


def _register_production() -> None:
    # Tools
    register_tool("tools.swan_query:fetch_transaction", "backend.orchestration.tools.swan_query:fetch_transaction")
    register_tool("tools.swan_query:fetch_account",     "backend.orchestration.tools.swan_query:fetch_account")
    register_tool("tools.counterparty_resolver:run",    "backend.orchestration.tools.counterparty_resolver:run")
    register_tool("tools.gl_account_classifier:run",    "backend.orchestration.tools.gl_account_classifier:run")
    register_tool("tools.journal_entry_builder:build_cash",     "backend.orchestration.tools.journal_entry_builder:build_cash")
    register_tool("tools.journal_entry_builder:build_accrual",  "backend.orchestration.tools.journal_entry_builder:build_accrual")
    register_tool("tools.journal_entry_builder:match_accrual",  "backend.orchestration.tools.journal_entry_builder:match_accrual")
    register_tool("tools.journal_entry_builder:build_reversal", "backend.orchestration.tools.journal_entry_builder:build_reversal")
    register_tool("tools.journal_entry_builder:find_original",  "backend.orchestration.tools.journal_entry_builder:find_original")
    register_tool("tools.journal_entry_builder:mark_reversed",  "backend.orchestration.tools.journal_entry_builder:mark_reversed")
    register_tool("tools.gl_poster:post",            "backend.orchestration.tools.gl_poster:post")
    register_tool("tools.invariant_checker:run",     "backend.orchestration.tools.invariant_checker:run")
    register_tool("tools.budget_envelope:decrement", "backend.orchestration.tools.budget_envelope:decrement")
    register_tool("tools.confidence_gate:run",       "backend.orchestration.tools.confidence_gate:run")
    register_tool("tools.review_queue:enqueue",      "backend.orchestration.tools.review_queue:enqueue")
    register_tool("tools.document_extractor:validate_totals", "backend.orchestration.tools.document_extractor:validate_totals")
    register_tool("tools.external_payload_parser:run", "backend.orchestration.tools.external_payload_parser:run")

    # Agents
    register_agent("agents.counterparty_classifier:run", "backend.orchestration.agents.counterparty_classifier:run")
    register_agent("agents.gl_account_classifier:run",   "backend.orchestration.agents.gl_account_classifier_agent:run")
    register_agent("agents.document_extractor:run",      "backend.orchestration.agents.document_extractor:run")

    # Conditions
    register_condition("conditions.counterparty:unresolved", "backend.orchestration.conditions.counterparty:unresolved")
    register_condition("conditions.gl:unclassified",         "backend.orchestration.conditions.gl:unclassified")
    register_condition("conditions.documents:totals_ok",     "backend.orchestration.conditions.documents:totals_ok")
    register_condition("conditions.documents:totals_mismatch","backend.orchestration.conditions.documents:totals_mismatch")


_register_production()
