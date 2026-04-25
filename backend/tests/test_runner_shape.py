"""Three runners, one AgentResult shape (RealMetaPRD §11 line 1538-1540)."""
from __future__ import annotations

from dataclasses import fields
from types import SimpleNamespace
from typing import Any

import pytest

from backend.orchestration.context import AgnesContext
from backend.orchestration.runners.adk_runner import AdkRunner
from backend.orchestration.runners.anthropic_runner import AnthropicRunner
from backend.orchestration.runners.base import AgentResult
from backend.orchestration.runners.pydantic_ai_runner import PydanticAiRunner


def _ctx(store) -> AgnesContext:
    return AgnesContext(
        run_id=1, pipeline_name="t", trigger_source="manual",
        trigger_payload={}, node_outputs={}, store=store,
    )


_FIXTURE_IMPL: dict[str, Any] = {
    "output": {"answer": "ok"},
    "model": "claude-haiku-4-5",
    "response_id": "fake_resp_id",
    "alternatives": [{"value": "ok", "score": 0.9}],
    "confidence": 0.95,
    "usage": {
        "input_tokens": 100, "output_tokens": 50,
        "cache_read_tokens": 0, "cache_write_tokens": 0,
        "reasoning_tokens": 0,
    },
    "finish_reason": "end_turn",
}


async def test_anthropic_runner_shape(store, fake_anthropic, fake_anthropic_message):
    calls, fake = fake_anthropic
    fake.messages._response = fake_anthropic_message(
        tool_input={"answer": "ok", "confidence": 0.95,
                    "alternatives": [{"value": "ok", "score": 0.9}]},
        tool_name="submit_test",
    )
    runner = AnthropicRunner()
    r = await runner.run(
        ctx=_ctx(store),
        system="You are helpful.",
        tools=[{"name": "submit_test", "input_schema": {"type": "object"}}],
        messages=[{"role": "user", "content": "hi"}],
        model="claude-haiku-4-5",
    )
    _assert_full_result(r)
    assert r.output == {"answer": "ok", "confidence": 0.95,
                        "alternatives": [{"value": "ok", "score": 0.9}]}
    assert r.confidence == 0.95
    assert r.alternatives and r.alternatives[0]["value"] == "ok"
    # Forced tool_choice present in request
    assert calls[0]["tool_choice"]["name"] == "submit_test"


async def test_adk_runner_shape_with_stub_impl(store, monkeypatch):
    runner = AdkRunner()

    async def stub(**_: Any) -> dict[str, Any]:
        return _FIXTURE_IMPL

    monkeypatch.setattr(runner, "_run_impl", stub)
    r = await runner.run(
        ctx=_ctx(store), system="s", tools=[], messages=[{"role": "user", "content": "x"}],
        model="claude-haiku-4-5",
    )
    _assert_full_result(r)


async def test_pydantic_ai_runner_shape_with_stub_impl(store, monkeypatch):
    runner = PydanticAiRunner()

    async def stub(**_: Any) -> dict[str, Any]:
        return _FIXTURE_IMPL

    monkeypatch.setattr(runner, "_run_impl", stub)
    r = await runner.run(
        ctx=_ctx(store), system="s", tools=[], messages=[{"role": "user", "content": "x"}],
        model="claude-haiku-4-5",
    )
    _assert_full_result(r)


async def test_adk_real_run_raises_not_implemented(store):
    """The stub-only behavior (PRD1_VALIDATION_BRIEFING C5)."""
    runner = AdkRunner()
    with pytest.raises(NotImplementedError):
        await runner.run(
            ctx=_ctx(store), system="s", tools=[],
            messages=[{"role": "user", "content": "x"}],
            model="any",
        )


async def test_pydantic_ai_real_run_surfaces_missing_key_as_error_result(
    store, monkeypatch
):
    """The Cerebras runner surfaces a missing CEREBRAS_API_KEY as a clean
    AgentResult with finish_reason=='error:OpenAIError', not an exception.

    Surfacing the failure in the AgentResult lets the deterministic fallback
    path take over (RealMetaPRD §7.9). Reset the singleton so this test does
    not pick up a client built by an earlier test.
    """
    from backend.orchestration.runners import pydantic_ai_runner

    monkeypatch.setattr(pydantic_ai_runner, "_client", None)
    monkeypatch.delenv("CEREBRAS_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    runner = PydanticAiRunner()
    r = await runner.run(
        ctx=_ctx(store), system="s", tools=[],
        messages=[{"role": "user", "content": "x"}],
        model="gpt-oss-120b",
    )
    assert isinstance(r, AgentResult)
    assert r.output is None
    assert r.finish_reason and r.finish_reason.startswith("error:")
    monkeypatch.setattr(pydantic_ai_runner, "_client", None)


def _assert_full_result(r: AgentResult) -> None:
    assert isinstance(r, AgentResult)
    # Every field present and typed
    field_names = {f.name for f in fields(AgentResult)}
    assert field_names.issuperset({
        "output", "model", "response_id", "prompt_hash", "alternatives",
        "confidence", "usage", "latency_ms", "finish_reason", "temperature", "seed",
    })
    assert isinstance(r.prompt_hash, str) and len(r.prompt_hash) == 16
    assert isinstance(r.latency_ms, int)
    assert r.usage is not None
