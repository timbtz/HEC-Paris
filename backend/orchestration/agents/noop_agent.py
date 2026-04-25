"""Smoke-test no-op agent.

Calls the configured runner with a tiny prompt. In tests, the AnthropicRunner
client is monkey-patched to return a deterministic AgentResult; in production
Phase 1 this would actually hit the Anthropic API.
"""
from __future__ import annotations

from ..context import AgnesContext
from ..registries import get_runner
from ..runners.base import AgentResult


async def run(ctx: AgnesContext) -> AgentResult:
    runner = get_runner("anthropic")
    return await runner.run(
        ctx=ctx,
        system="You are a test agent. Reply with the literal string 'ok'.",
        tools=[],
        messages=[{"role": "user", "content": "ping"}],
        model="claude-haiku-4-5",
        max_tokens=64,
        temperature=0.0,
    )
