"""Condition for missing GL classification.

Source: 02_YAML_WORKFLOW_DSL.md:96-120 (condition shape - pure ctx -> bool).
The deterministic ``classify-gl-account`` tool returns ``gl_account=None``
on a miss; this condition gates the AI fallback agent.
"""
from __future__ import annotations

from ..context import FingentContext


def unclassified(ctx: FingentContext) -> bool:
    out = ctx.get("classify-gl-account") or {}
    return out.get("gl_account") is None
