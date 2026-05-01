"""AnthropicRunner — per-call deadline_s + timeout-as-AgentResult fallback.

Source: RealMetaPRD §7.9 (no-retry-on-timeout policy; fallback path).
The runner must surface a slow / hung SDK call as an AgentResult with
finish_reason='timeout' so the executor's confidence gate routes to the
deterministic fallback or review queue.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from backend.orchestration.context import FingentContext
from backend.orchestration.runners import anthropic_runner
from backend.orchestration.runners.anthropic_runner import AnthropicRunner
from backend.orchestration.runners.base import AgentResult


def _ctx(store) -> FingentContext:
    return FingentContext(
        run_id=1,
        pipeline_name="t",
        trigger_source="manual",
        trigger_payload={},
        node_outputs={},
        store=store,
    )


def _make_slow_client(sleep_s: float):
    """A fake client whose messages.create sleeps then returns a fake message."""

    class _SlowMessages:
        async def create(self, **_kwargs):
            await asyncio.sleep(sleep_s)
            return SimpleNamespace(
                id="msg_slow",
                model="claude-sonnet-4-6",
                stop_reason="end_turn",
                content=[SimpleNamespace(type="text", text="ok")],
                usage=SimpleNamespace(
                    input_tokens=1,
                    output_tokens=1,
                    cache_read_input_tokens=0,
                    cache_creation_input_tokens=0,
                ),
            )

    class _SlowClient:
        def __init__(self):
            self.messages = _SlowMessages()

    return _SlowClient()


async def test_deadline_too_tight_returns_timeout_agent_result(store, monkeypatch):
    """deadline_s=0.5 vs sleep=2.0 → AgentResult(finish_reason='timeout')."""
    monkeypatch.setattr(anthropic_runner, "_client", _make_slow_client(2.0))

    runner = AnthropicRunner()
    result = await runner.run(
        ctx=_ctx(store),
        system="s",
        tools=[],
        messages=[{"role": "user", "content": "x"}],
        model="claude-sonnet-4-6",
        deadline_s=0.5,
    )

    assert isinstance(result, AgentResult)
    assert result.finish_reason == "timeout"
    assert result.output is None
    assert result.confidence is None
    assert result.usage.input_tokens == 0
    assert result.usage.output_tokens == 0


async def test_per_call_deadline_override_completes(store, monkeypatch):
    """deadline_s=3.0 vs sleep=0.1 → completes successfully."""
    monkeypatch.setattr(anthropic_runner, "_client", _make_slow_client(0.1))

    runner = AnthropicRunner()
    result = await runner.run(
        ctx=_ctx(store),
        system="s",
        tools=[],
        messages=[{"role": "user", "content": "x"}],
        model="claude-sonnet-4-6",
        deadline_s=3.0,
    )

    assert isinstance(result, AgentResult)
    assert result.finish_reason == "end_turn"
    # Output is the text fallback (no submit_* tool registered).
    assert result.output == "ok"


async def test_apitimeout_error_classified_as_timeout(store, monkeypatch):
    """SDK-style APITimeoutError exception name → finish_reason='timeout'."""

    class _APITimeoutError(Exception):
        pass

    # Rename so type(exc).__name__ == "APITimeoutError"
    _APITimeoutError.__name__ = "APITimeoutError"

    class _Messages:
        async def create(self, **_kwargs):
            raise _APITimeoutError("simulated SDK timeout")

    class _Client:
        def __init__(self):
            self.messages = _Messages()

    monkeypatch.setattr(anthropic_runner, "_client", _Client())

    runner = AnthropicRunner()
    result = await runner.run(
        ctx=_ctx(store),
        system="s",
        tools=[],
        messages=[{"role": "user", "content": "x"}],
        model="claude-sonnet-4-6",
        deadline_s=4.5,
    )

    assert result.finish_reason == "timeout"
    assert result.output is None
    assert result.confidence is None
