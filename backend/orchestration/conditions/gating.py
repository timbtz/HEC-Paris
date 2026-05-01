"""Condition functions referenced by `when:` in pipelines.

Source: 02_YAML_WORKFLOW_DSL.md:96-120; defensive read pattern from :198-199.
Conditions are pure functions of `ctx`. **No I/O, no randomness, no globals.**
Phase 1 ships these as stubs so Phase D pipelines drop in cleanly.
"""
from __future__ import annotations

from ..context import FingentContext


def passes_confidence(ctx: FingentContext) -> bool:
    out = ctx.get("gate-confidence") or {}
    return bool(out.get("ok"))


def needs_review(ctx: FingentContext) -> bool:
    out = ctx.get("gate-confidence") or {}
    return bool(out) and not out.get("ok")


def posted(ctx: FingentContext) -> bool:
    out = ctx.get("post-entry") or {}
    return out.get("status") == "posted"
