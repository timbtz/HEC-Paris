"""AnthropicRunner — the default agent runtime.

Source: ANTHROPIC_SDK_STACK_REFERENCE.md:41-105 (client init + Messages API),
:300-323 (submit-tool extraction), :1067-1083 (per-app singleton),
:1255-1259 (no LLM retry on timeout).

The runner returns an AgentResult; the executor (not the runner) calls
`audit.propose_checkpoint_commit` afterwards. This keeps audit writes in
one place and makes the runner pure-LLM.
"""
from __future__ import annotations

import asyncio
import os
import time
from typing import Any, TYPE_CHECKING

from .base import AgentResult, TokenUsage
from ..prompt_hash import prompt_hash

if TYPE_CHECKING:
    from ..context import AgnesContext

try:  # Lazy-tolerant import so tests can monkey-patch without a key.
    import anthropic
except ImportError:  # pragma: no cover
    anthropic = None  # type: ignore[assignment]


# Module-level singleton; constructed on first use. Tests can replace
# `_client` directly with a stub.
_client: Any = None


def _get_client() -> Any:
    """Per-app `AsyncAnthropic` singleton (ANTHROPIC_SDK_STACK_REFERENCE:1067-1083)."""
    global _client
    if _client is not None:
        return _client
    if anthropic is None:
        raise RuntimeError(
            "anthropic SDK not installed; install with `pip install anthropic`"
        )
    _client = anthropic.AsyncAnthropic(
        api_key=os.environ.get("ANTHROPIC_API_KEY"),
        timeout=4.5,
        max_retries=2,
    )
    return _client


def _extract_submit_tool_input(content: list[Any]) -> Any | None:
    """Find the forced `submit_*` tool's `input` block.

    Falls back to the first `tool_use` block; falls back to the first text
    block if no tool was called.
    """
    submit = next(
        (
            b for b in content
            if getattr(b, "type", None) == "tool_use"
            and getattr(b, "name", "").startswith("submit")
        ),
        None,
    )
    if submit is not None:
        return submit.input

    any_tool = next((b for b in content if getattr(b, "type", None) == "tool_use"), None)
    if any_tool is not None:
        return any_tool.input

    text = next((b for b in content if getattr(b, "type", None) == "text"), None)
    if text is not None:
        return getattr(text, "text", None)
    return None


def _usage_from_anthropic(usage_obj: Any) -> TokenUsage:
    """Map Anthropic's usage to our `TokenUsage`.

    Anthropic exposes `cache_creation_input_tokens` /
    `cache_read_input_tokens`; we rename to `cache_write_tokens` /
    `cache_read_tokens` (the AgentResult contract).
    """
    if usage_obj is None:
        return TokenUsage()
    return TokenUsage(
        input_tokens=getattr(usage_obj, "input_tokens", 0) or 0,
        output_tokens=getattr(usage_obj, "output_tokens", 0) or 0,
        cache_read_tokens=getattr(usage_obj, "cache_read_input_tokens", 0) or 0,
        cache_write_tokens=getattr(usage_obj, "cache_creation_input_tokens", 0) or 0,
        reasoning_tokens=0,  # Anthropic does not split reasoning tokens.
    )


def _confidence_from_input(parsed: Any) -> float | None:
    if isinstance(parsed, dict):
        c = parsed.get("confidence")
        if isinstance(c, (int, float)):
            return float(c)
    return None


def _alternatives_from_input(parsed: Any) -> list[dict] | None:
    if isinstance(parsed, dict):
        alt = parsed.get("alternatives")
        if isinstance(alt, list):
            return alt
    return None


class AnthropicRunner:
    """Default runner — wraps `client.messages.create` into AgentResult shape."""

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
        client = _get_client()

        request_kwargs: dict[str, Any] = {
            "model": model,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "messages": messages,
        }
        if system:
            request_kwargs["system"] = system
        if tools:
            request_kwargs["tools"] = tools
            # Force the submit-tool when present; otherwise let Claude pick.
            submit_tool = next(
                (t for t in tools if t.get("name", "").startswith("submit")),
                None,
            )
            if submit_tool is not None:
                request_kwargs["tool_choice"] = {"type": "tool", "name": submit_tool["name"]}

        try:
            # Per-call deadline override (RealMetaPRD §7.9). The
            # AsyncAnthropic client is built with timeout=4.5; vision agents
            # need more headroom (e.g. deadline_s=15.0 for the doc extractor).
            # `asyncio.wait_for` works with any SDK version.
            msg = await asyncio.wait_for(
                client.messages.create(**request_kwargs),
                timeout=deadline_s + 1.0,
            )
        except Exception as exc:
            # APITimeoutError + transport errors: surface as AgentResult so
            # the deterministic fallback path can take over (RealMetaPRD §7.9).
            elapsed_ms = int((time.monotonic() - start) * 1000)
            timeout_marker = (
                isinstance(exc, asyncio.TimeoutError)
                or type(exc).__name__ in ("APITimeoutError", "TimeoutError")
            )
            return AgentResult(
                output=None,
                model=model,
                response_id=None,
                prompt_hash=ph,
                alternatives=None,
                confidence=None,
                usage=TokenUsage(),
                latency_ms=elapsed_ms,
                finish_reason="timeout" if timeout_marker else f"error:{type(exc).__name__}",
                temperature=temperature,
                seed=seed,
                raw={"exception": repr(exc)},
            )

        elapsed_ms = int((time.monotonic() - start) * 1000)
        content = list(getattr(msg, "content", []) or [])
        parsed = _extract_submit_tool_input(content)

        return AgentResult(
            output=parsed,
            model=getattr(msg, "model", model),
            response_id=getattr(msg, "id", None),
            prompt_hash=ph,
            alternatives=_alternatives_from_input(parsed),
            confidence=_confidence_from_input(parsed),
            usage=_usage_from_anthropic(getattr(msg, "usage", None)),
            latency_ms=elapsed_ms,
            finish_reason=getattr(msg, "stop_reason", None),
            temperature=temperature,
            seed=seed,
        )
