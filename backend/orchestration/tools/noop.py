"""Smoke-test no-op tool.

Tools follow the contract from 04_AGENT_PATTERNS.md:9-32: synchronous,
stateless, return a JSON-able dict. The executor dispatches sync tools via
`run_in_executor` to keep the event loop unblocked
(01_ORCHESTRATION_REFERENCE.md:65).
"""
from __future__ import annotations

from typing import Any

from ..context import AgnesContext


def run(ctx: AgnesContext) -> dict[str, Any]:
    return {
        "echo": ctx.trigger_payload,
        "node_outputs_seen": list(ctx.node_outputs.keys()),
    }
