"""Tests for the Cerebras runner — pure helpers + mocked round-trip.

Source: Plan `Orchestration/Plans/cerebras-runner-and-classifier-migration.md`
steps 5 + 7. No live network: all transport is stubbed via `SimpleNamespace`.
"""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from typing import Any

import pytest

from backend.orchestration.agents.anomaly_flag_agent import (
    _ANOMALY_KINDS,
)
from backend.orchestration.cost import micro_usd
from backend.orchestration.runners import pydantic_ai_runner
from backend.orchestration.runners.base import TokenUsage
from backend.orchestration.runners.cerebras_impl import (
    parse_response,
    translate_tool_choice,
    translate_tool_schema,
)


# ---------------------------------------------------------------------------
# Tool fixtures — literal copies of the agent shapes the runner will see.
# Don't import the agent tools directly: the agent dicts include caches /
# closed enums sourced from the DB, which would couple this unit test to
# fixtures.
# ---------------------------------------------------------------------------


def _anomaly_tool_dict() -> dict[str, Any]:
    return {
        "name": "submit_anomalies",
        "description": "Submit anomaly findings.",
        "input_schema": {
            "type": "object",
            "properties": {
                "anomalies": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "kind": {"type": "string", "enum": list(_ANOMALY_KINDS)},
                            "description": {"type": "string"},
                            "evidence": {"type": "string"},
                            "line_ids": {
                                "type": "array",
                                "items": {"type": "integer"},
                            },
                            "confidence": {"type": "number"},
                        },
                        "required": ["kind", "description", "confidence"],
                    },
                },
                "overall_confidence": {"type": "number"},
            },
            "required": ["anomalies", "overall_confidence"],
        },
    }


def _gl_tool_dict() -> dict[str, Any]:
    return {
        "name": "submit_gl_account",
        "description": "Pick a GL account.",
        "input_schema": {
            "type": "object",
            "properties": {
                "gl_account": {
                    "type": "string",
                    "enum": ["606100", "613000", "626100"],
                },
                "confidence": {"type": "number"},
                "alternatives": {
                    "type": "array",
                    "items": {"type": "object"},
                },
                "vat_rate_bp": {"type": ["integer", "null"]},
            },
            "required": ["gl_account", "confidence"],
        },
    }


def _counterparty_tool_dict() -> dict[str, Any]:
    return {
        "name": "submit_counterparty",
        "description": "Pick or null a counterparty.",
        "input_schema": {
            "type": "object",
            "properties": {
                "counterparty_id": {"type": ["integer", "null"]},
                "confidence": {"type": "number"},
                "alternatives": {
                    "type": "array",
                    "items": {"type": "object"},
                },
            },
            "required": ["counterparty_id", "confidence"],
        },
    }


# ---------------------------------------------------------------------------
# translate_tool_schema
# ---------------------------------------------------------------------------


def test_translate_tool_schema_anomaly():
    out = translate_tool_schema(_anomaly_tool_dict())
    assert out["type"] == "function"
    fn = out["function"]
    assert fn["name"] == "submit_anomalies"
    assert fn["strict"] is True

    params = fn["parameters"]
    assert params["type"] == "object"
    assert params["additionalProperties"] is False

    items = params["properties"]["anomalies"]["items"]
    assert items["type"] == "object"
    assert items["additionalProperties"] is False
    # Enum survives intact.
    assert items["properties"]["kind"]["enum"] == list(_ANOMALY_KINDS)


def test_translate_tool_schema_gl_preserves_enum():
    out = translate_tool_schema(_gl_tool_dict())
    params = out["function"]["parameters"]
    assert params["additionalProperties"] is False
    enum = params["properties"]["gl_account"]["enum"]
    assert enum == ["606100", "613000", "626100"]
    # array-of-object should also have additionalProperties:false on its items.
    alt_items = params["properties"]["alternatives"]["items"]
    assert alt_items["additionalProperties"] is False


def test_translate_tool_schema_counterparty_union_type():
    out = translate_tool_schema(_counterparty_tool_dict())
    params = out["function"]["parameters"]
    # ["integer","null"] union must survive untouched.
    assert params["properties"]["counterparty_id"]["type"] == ["integer", "null"]
    assert params["additionalProperties"] is False


def test_translate_tool_schema_does_not_mutate_input():
    original = _anomaly_tool_dict()
    snapshot = json.dumps(original, sort_keys=True)
    translate_tool_schema(original)
    assert json.dumps(original, sort_keys=True) == snapshot


def test_translate_tool_choice():
    assert translate_tool_choice("submit_anomalies") == {
        "type": "function",
        "function": {"name": "submit_anomalies"},
    }


# ---------------------------------------------------------------------------
# parse_response — happy + failure modes
# ---------------------------------------------------------------------------


def _fake_response(
    *,
    tool_name: str | None = "submit_anomalies",
    arguments: str = '{"anomalies": [], "overall_confidence": 0.9, "confidence": 0.9}',
    finish_reason: str = "tool_calls",
    response_id: str = "resp-abc",
    model: str = "gpt-oss-120b",
    prompt_tokens: int = 200,
    completion_tokens: int = 50,
    cached_tokens: int = 0,
    reasoning_tokens: int = 0,
    text_content: str | None = None,
) -> SimpleNamespace:
    if tool_name is None:
        message = SimpleNamespace(content=text_content, tool_calls=None)
    else:
        tc = SimpleNamespace(
            function=SimpleNamespace(name=tool_name, arguments=arguments),
        )
        message = SimpleNamespace(content=None, tool_calls=[tc])

    usage = SimpleNamespace(
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        prompt_tokens_details=SimpleNamespace(cached_tokens=cached_tokens),
        completion_tokens_details=SimpleNamespace(reasoning_tokens=reasoning_tokens),
    )
    choice = SimpleNamespace(finish_reason=finish_reason, message=message)
    return SimpleNamespace(id=response_id, model=model, choices=[choice], usage=usage)


def test_parse_response_happy_path():
    resp = _fake_response(
        arguments=json.dumps(
            {
                "anomalies": [
                    {
                        "kind": "balance_drift",
                        "description": "TB drift",
                        "confidence": 0.9,
                    }
                ],
                "overall_confidence": 0.9,
                "confidence": 0.92,
            }
        ),
    )
    result = parse_response(resp)
    assert isinstance(result["output"], dict)
    assert result["output"]["overall_confidence"] == 0.9
    assert result["finish_reason"] == "tool_calls"
    assert result["response_id"] == "resp-abc"
    assert result["model"] == "gpt-oss-120b"
    assert result["usage"]["input_tokens"] == 200
    assert result["usage"]["output_tokens"] == 50
    assert result["confidence"] == 0.92


def test_parse_response_tool_name_mismatch():
    resp = _fake_response(tool_name="fabricated_tool", arguments="{}")
    result = parse_response(resp)
    assert result["output"] is None
    assert result["finish_reason"] == "tool_name_mismatch"


def test_parse_response_arg_json_error():
    resp = _fake_response(arguments="{not json")
    result = parse_response(resp)
    assert result["output"] is None
    assert result["finish_reason"] == "tool_call_parse_error"


def test_parse_response_reasoning_tokens_captured():
    resp = _fake_response(reasoning_tokens=42, cached_tokens=11)
    result = parse_response(resp)
    assert result["usage"]["reasoning_tokens"] == 42
    assert result["usage"]["cache_read_tokens"] == 11
    assert result["usage"]["cache_write_tokens"] == 0


def test_parse_response_alternatives_extracted():
    resp = _fake_response(
        arguments=json.dumps(
            {
                "counterparty_id": 7,
                "confidence": 0.61,
                "alternatives": [{"id": 9, "confidence": 0.31}],
            }
        ),
        tool_name="submit_counterparty",
    )
    result = parse_response(resp)
    assert result["alternatives"] == [{"id": 9, "confidence": 0.31}]
    assert result["confidence"] == 0.61


def test_parse_response_text_only_fallback():
    resp = _fake_response(tool_name=None, text_content="hello", finish_reason="stop")
    result = parse_response(resp)
    assert result["output"] == "hello"
    assert result["finish_reason"] == "stop"


# ---------------------------------------------------------------------------
# cost.py — Cerebras pricing incl. reasoning-token basis
# ---------------------------------------------------------------------------


def test_cost_micro_usd_for_cerebras_usage_with_reasoning():
    """1M input + 500k output + 100k reasoning at gpt-oss-120b rates.

    Expected: 350 (in) + 375 (500k * 750/1M) + 75 (100k * 750/1M) == 800.
    Will fail until step 14 adds the reasoning-token line to micro_usd.
    """
    usage = TokenUsage(
        input_tokens=1_000_000,
        output_tokens=500_000,
        reasoning_tokens=100_000,
    )
    assert micro_usd(usage, "cerebras", "gpt-oss-120b") == 800


def test_cost_micro_usd_anthropic_unaffected_by_reasoning_term():
    """Anthropic always reports reasoning_tokens=0; cost is unchanged."""
    usage = TokenUsage(input_tokens=1_000_000, output_tokens=200_000, reasoning_tokens=0)
    # 1M * 3000 + 200k * 15000 = 3_000_000_000 + 3_000_000_000 = 6_000_000_000 micro_usd_total
    # // 1_000_000 = 6000.
    assert micro_usd(usage, "anthropic", "claude-sonnet-4-6") == 6000


# ---------------------------------------------------------------------------
# Round-trip via PydanticAiRunner with monkey-patched _client
# ---------------------------------------------------------------------------


@pytest.fixture
def reset_pydantic_ai_client(monkeypatch):
    """Reset the singleton client between tests so they don't leak state."""
    monkeypatch.setattr(pydantic_ai_runner, "_client", None)
    yield
    monkeypatch.setattr(pydantic_ai_runner, "_client", None)


class _FakeChatCompletions:
    def __init__(self, response: Any | None = None, exc: Exception | None = None):
        self._response = response
        self._exc = exc
        self.calls: list[dict[str, Any]] = []

    async def create(self, **kwargs):  # noqa: D401
        self.calls.append(kwargs)
        if self._exc is not None:
            raise self._exc
        return self._response


class _FakeAsyncOpenAI:
    def __init__(self, response: Any | None = None, exc: Exception | None = None):
        self._completions = _FakeChatCompletions(response=response, exc=exc)
        self.chat = SimpleNamespace(completions=self._completions)


async def test_runner_round_trip(monkeypatch, reset_pydantic_ai_client):
    response = _fake_response(
        arguments=json.dumps(
            {
                "anomalies": [
                    {"kind": "balance_drift", "description": "x", "confidence": 0.85}
                ],
                "overall_confidence": 0.85,
                "confidence": 0.85,
            }
        ),
        prompt_tokens=300,
        completion_tokens=80,
        reasoning_tokens=12,
    )
    fake = _FakeAsyncOpenAI(response=response)
    monkeypatch.setattr(pydantic_ai_runner, "_client", fake)

    runner = pydantic_ai_runner.PydanticAiRunner()
    result = await runner.run(
        ctx=None,  # type: ignore[arg-type]  # runner does not touch ctx.
        system="You are an audit assistant.",
        tools=[_anomaly_tool_dict()],
        messages=[{"role": "user", "content": "review the period"}],
        model="gpt-oss-120b",
        max_tokens=800,
        temperature=0.0,
    )

    assert isinstance(result.output, dict)
    assert result.output["overall_confidence"] == 0.85
    assert result.confidence == 0.85
    assert result.usage.input_tokens == 300
    assert result.usage.output_tokens == 80
    assert result.usage.reasoning_tokens == 12
    assert result.latency_ms >= 0
    assert result.prompt_hash
    assert result.response_id == "resp-abc"
    assert result.model == "gpt-oss-120b"
    assert result.finish_reason == "tool_calls"

    # Verify the request kwargs are translated correctly.
    sent = fake._completions.calls[0]
    assert sent["model"] == "gpt-oss-120b"
    assert sent["max_completion_tokens"] == 800
    assert "max_tokens" not in sent  # OpenAI shape, not Anthropic.
    assert sent["parallel_tool_calls"] is False
    assert sent["tool_choice"] == {
        "type": "function",
        "function": {"name": "submit_anomalies"},
    }
    assert sent["messages"][0] == {
        "role": "system",
        "content": "You are an audit assistant.",
    }
    # No `seed` key when caller did not provide one.
    assert "seed" not in sent
    # tools translated to OpenAI shape.
    assert sent["tools"][0]["type"] == "function"
    assert sent["tools"][0]["function"]["strict"] is True


async def test_runner_timeout(monkeypatch, reset_pydantic_ai_client):
    fake = _FakeAsyncOpenAI(exc=asyncio.TimeoutError())
    monkeypatch.setattr(pydantic_ai_runner, "_client", fake)

    runner = pydantic_ai_runner.PydanticAiRunner()
    result = await runner.run(
        ctx=None,  # type: ignore[arg-type]
        system="",
        tools=[_anomaly_tool_dict()],
        messages=[{"role": "user", "content": "x"}],
        model="gpt-oss-120b",
        max_tokens=256,
        temperature=0.0,
    )
    assert result.output is None
    assert result.finish_reason == "timeout"
    assert result.usage == TokenUsage()


async def test_runner_generic_error(monkeypatch, reset_pydantic_ai_client):
    fake = _FakeAsyncOpenAI(exc=RuntimeError("boom"))
    monkeypatch.setattr(pydantic_ai_runner, "_client", fake)

    runner = pydantic_ai_runner.PydanticAiRunner()
    result = await runner.run(
        ctx=None,  # type: ignore[arg-type]
        system="",
        tools=[_anomaly_tool_dict()],
        messages=[{"role": "user", "content": "x"}],
        model="gpt-oss-120b",
        max_tokens=256,
        temperature=0.0,
    )
    assert result.output is None
    assert result.finish_reason == "error:RuntimeError"


async def test_runner_omits_seed_when_none(monkeypatch, reset_pydantic_ai_client):
    """`seed=None` must be dropped — passing None to OpenAI errors."""
    response = _fake_response()
    fake = _FakeAsyncOpenAI(response=response)
    monkeypatch.setattr(pydantic_ai_runner, "_client", fake)

    runner = pydantic_ai_runner.PydanticAiRunner()
    await runner.run(
        ctx=None,  # type: ignore[arg-type]
        system="",
        tools=[_anomaly_tool_dict()],
        messages=[{"role": "user", "content": "x"}],
        model="gpt-oss-120b",
        max_tokens=128,
        temperature=0.0,
        seed=None,
    )
    sent = fake._completions.calls[0]
    assert "seed" not in sent


async def test_runner_passes_seed_when_set(monkeypatch, reset_pydantic_ai_client):
    response = _fake_response()
    fake = _FakeAsyncOpenAI(response=response)
    monkeypatch.setattr(pydantic_ai_runner, "_client", fake)

    runner = pydantic_ai_runner.PydanticAiRunner()
    await runner.run(
        ctx=None,  # type: ignore[arg-type]
        system="",
        tools=[_anomaly_tool_dict()],
        messages=[{"role": "user", "content": "x"}],
        model="gpt-oss-120b",
        max_tokens=128,
        temperature=0.0,
        seed=42,
    )
    sent = fake._completions.calls[0]
    assert sent["seed"] == 42


async def test_runner_no_tools_path(monkeypatch, reset_pydantic_ai_client):
    """Runner must work for free-text outputs (no tools provided)."""
    response = _fake_response(
        tool_name=None, text_content="free-form reply", finish_reason="stop"
    )
    fake = _FakeAsyncOpenAI(response=response)
    monkeypatch.setattr(pydantic_ai_runner, "_client", fake)

    runner = pydantic_ai_runner.PydanticAiRunner()
    result = await runner.run(
        ctx=None,  # type: ignore[arg-type]
        system="hi",
        tools=[],
        messages=[{"role": "user", "content": "say hi"}],
        model="gpt-oss-120b",
        max_tokens=64,
        temperature=0.0,
    )
    sent = fake._completions.calls[0]
    assert "tools" not in sent
    assert "tool_choice" not in sent
    assert result.output == "free-form reply"
    assert result.finish_reason == "stop"
