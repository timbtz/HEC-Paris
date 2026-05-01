"""Runner threading: `wiki_context` flows into prompt_hash and out as
`AgentResult.wiki_references`. Plan §STEP-BY-STEP Task 4.

Both `AnthropicRunner` and `PydanticAiRunner` are exercised — the
Cerebras runner is patched at the SDK boundary so we don't need a real
Cerebras key in CI.
"""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from backend.orchestration.context import FingentContext
from backend.orchestration.runners import anthropic_runner, pydantic_ai_runner
from backend.orchestration.runners.anthropic_runner import AnthropicRunner
from backend.orchestration.runners.pydantic_ai_runner import PydanticAiRunner


def _ctx(store) -> FingentContext:
    return FingentContext(
        run_id=1,
        pipeline_name="test",
        trigger_source="manual",
        trigger_payload={},
        node_outputs={},
        store=store,
    )


async def test_anthropic_runner_threads_wiki_context(
    store, fake_anthropic, fake_anthropic_message,
):
    calls, fake = fake_anthropic
    fake.messages._response = fake_anthropic_message(
        tool_input={"x": 1},
        tool_name="submit_test",
    )
    runner = AnthropicRunner()

    base = await runner.run(
        ctx=_ctx(store),
        system="sys",
        tools=[{"name": "submit_test"}],
        messages=[{"role": "user", "content": "hi"}],
        model="claude-sonnet-4-6",
    )
    with_wiki = await runner.run(
        ctx=_ctx(store),
        system="sys",
        tools=[{"name": "submit_test"}],
        messages=[{"role": "user", "content": "hi"}],
        model="claude-sonnet-4-6",
        wiki_context=[(7, 42)],
    )
    other_rev = await runner.run(
        ctx=_ctx(store),
        system="sys",
        tools=[{"name": "submit_test"}],
        messages=[{"role": "user", "content": "hi"}],
        model="claude-sonnet-4-6",
        wiki_context=[(7, 43)],
    )

    # Different wiki context → different prompt hash.
    assert base.prompt_hash != with_wiki.prompt_hash
    assert with_wiki.prompt_hash != other_rev.prompt_hash

    # AgentResult carries the citation list.
    assert list(with_wiki.wiki_references) == [(7, 42)]
    assert list(base.wiki_references) == []


async def test_pydantic_ai_runner_threads_wiki_context(monkeypatch, store):
    """The Cerebras (OpenAI-compat) runner threads wiki_context too."""
    captured: dict[str, Any] = {}

    class _FakeChoiceMessage:
        def __init__(self) -> None:
            self.content = None
            self.tool_calls = [
                SimpleNamespace(
                    id="tc_1",
                    function=SimpleNamespace(
                        name="submit_test",
                        arguments='{"x": 1}',
                    ),
                )
            ]

    class _FakeResp:
        id = "rsp_test"
        model = "test-model"
        choices = [
            SimpleNamespace(
                message=_FakeChoiceMessage(),
                finish_reason="tool_calls",
            )
        ]
        usage = SimpleNamespace(
            prompt_tokens=10,
            completion_tokens=5,
            total_tokens=15,
        )

    class _FakeChat:
        class _Completions:
            async def create(self_inner, **kwargs):  # noqa: N805
                captured["kwargs"] = kwargs
                return _FakeResp()
        completions = _Completions()

    class _FakeClient:
        def __init__(self) -> None:
            self.chat = _FakeChat()

    monkeypatch.setattr(pydantic_ai_runner, "_client", _FakeClient())
    runner = PydanticAiRunner()

    base = await runner.run(
        ctx=_ctx(store),
        system="sys",
        tools=[{
            "name": "submit_test",
            "input_schema": {
                "type": "object",
                "properties": {"x": {"type": "integer"}},
                "required": ["x"],
            },
        }],
        messages=[{"role": "user", "content": "hi"}],
        model="test-model",
    )
    with_wiki = await runner.run(
        ctx=_ctx(store),
        system="sys",
        tools=[{
            "name": "submit_test",
            "input_schema": {
                "type": "object",
                "properties": {"x": {"type": "integer"}},
                "required": ["x"],
            },
        }],
        messages=[{"role": "user", "content": "hi"}],
        model="test-model",
        wiki_context=[(7, 42)],
    )

    assert base.prompt_hash != with_wiki.prompt_hash
    assert list(with_wiki.wiki_references) == [(7, 42)]
    assert list(base.wiki_references) == []
