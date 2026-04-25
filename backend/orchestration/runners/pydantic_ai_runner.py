"""Cerebras (OpenAI-compat) runner.

Registry name (`pydantic_ai`) is kept for compat with the executor's provider
mapping (`pydantic_ai → cerebras`); the implementation is raw `AsyncOpenAI`
against `https://api.cerebras.ai/v1` per
`Orchestration/research/CEREBRAS_STACK_REFERENCE.md` §13. A Pydantic-AI
wrapper would add code without earning anything our `submit_*` agents need.

The wrapper `run()` (a) hashes the prompt, (b) measures latency, (c) builds
the AgentResult from `_run_impl()`'s dict return. `_run_impl()` only owns
the wire call: schema translation + the OpenAI `chat.completions.create`
envelope + error capture.
"""
from __future__ import annotations

import asyncio
import os
import time
from typing import Any, TYPE_CHECKING

from .base import AgentResult, TokenUsage
from .cerebras_impl import (
    parse_response,
    translate_tool_choice,
    translate_tool_schema,
)
from ..prompt_hash import prompt_hash

if TYPE_CHECKING:
    from ..context import AgnesContext

try:  # Lazy-tolerant import so unit tests without the SDK still collect.
    from openai import AsyncOpenAI
except ImportError:  # pragma: no cover
    AsyncOpenAI = None  # type: ignore[assignment]


# Module-level singleton; constructed on first use. Tests monkey-patch
# `_client` directly.
_client: Any = None


def _get_client() -> Any:
    """Per-app `AsyncOpenAI` singleton pointed at Cerebras."""
    global _client
    if _client is not None:
        return _client
    if AsyncOpenAI is None:
        raise RuntimeError(
            "openai SDK not installed; install with `pip install openai`"
        )
    _client = AsyncOpenAI(
        base_url="https://api.cerebras.ai/v1",
        api_key=os.environ.get("CEREBRAS_API_KEY"),
        timeout=4.0,
    )
    return _client


class PydanticAiRunner:
    """Cerebras runner — see module docstring for the registry-name caveat."""

    async def _run_impl(
        self,
        *,
        system: str,
        tools: list[dict],
        messages: list[dict],
        model: str,
        temperature: float,
        max_tokens: int,
        seed: int | None,
        deadline_s: float = 4.5,
    ) -> dict[str, Any]:
        api_messages: list[dict[str, Any]] = []
        if system:
            api_messages.append({"role": "system", "content": system})
        api_messages.extend(messages)

        api_tools = [translate_tool_schema(t) for t in tools] if tools else None
        submit_name = next(
            (t["name"] for t in tools if t.get("name", "").startswith("submit")),
            None,
        )

        kwargs: dict[str, Any] = {
            "model": model,
            "messages": api_messages,
            "max_completion_tokens": max_tokens,
            "temperature": temperature,
            "parallel_tool_calls": False,
        }
        if api_tools:
            kwargs["tools"] = api_tools
            kwargs["tool_choice"] = (
                translate_tool_choice(submit_name) if submit_name else "auto"
            )
        if seed is not None:
            kwargs["seed"] = seed

        try:
            # Wrapping `_get_client()` inside the envelope means missing-key /
            # SDK-import errors also surface as a clean AgentResult with
            # `finish_reason="error:..."` rather than escaping (so the
            # deterministic fallback can still take over — RealMetaPRD §7.9).
            client = _get_client()
            resp = await asyncio.wait_for(
                client.chat.completions.create(**kwargs),
                timeout=deadline_s + 1.0,
            )
        except Exception as exc:
            timeout_marker = (
                isinstance(exc, asyncio.TimeoutError)
                or type(exc).__name__ in ("APITimeoutError", "TimeoutError")
            )
            return {
                "output": None,
                "model": model,
                "response_id": None,
                "alternatives": None,
                "confidence": None,
                "usage": {},
                "finish_reason": "timeout" if timeout_marker
                                 else f"error:{type(exc).__name__}",
            }
        return parse_response(resp)

    async def run(
        self,
        *,
        ctx: "AgnesContext",
        system: str,
        tools: list[dict],
        messages: list[dict],
        model: str,
        temperature: float = 0.0,
        max_tokens: int = 1024,
        deadline_s: float = 4.5,
        seed: int | None = None,
    ) -> AgentResult:
        ph = prompt_hash(model, system, tools, messages)
        start = time.monotonic()
        impl = await self._run_impl(
            system=system,
            tools=tools,
            messages=messages,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            seed=seed,
            deadline_s=deadline_s,
        )
        elapsed_ms = int((time.monotonic() - start) * 1000)
        u = impl.get("usage") or {}
        return AgentResult(
            output=impl.get("output"),
            model=impl.get("model") or model,
            response_id=impl.get("response_id"),
            prompt_hash=ph,
            alternatives=impl.get("alternatives"),
            confidence=impl.get("confidence"),
            usage=TokenUsage(
                input_tokens=u.get("input_tokens", 0),
                output_tokens=u.get("output_tokens", 0),
                cache_read_tokens=u.get("cache_read_tokens", 0),
                cache_write_tokens=u.get("cache_write_tokens", 0),
                reasoning_tokens=u.get("reasoning_tokens", 0),
            ),
            latency_ms=elapsed_ms,
            finish_reason=impl.get("finish_reason"),
            temperature=temperature,
            seed=seed,
        )
