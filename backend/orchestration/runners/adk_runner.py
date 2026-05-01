"""Stub Google ADK runner.

Phase 1 ships the interface only; live ADK wiring is post-hackathon
(PRD1_VALIDATION_BRIEFING C5; RealMetaPRD §14 risk #4). The module imports
cleanly without `google-adk` installed; only `_run_impl` would touch it.
Tests inject a fake `_run_impl` to assert AgentResult-shape parity with
the Anthropic and Pydantic AI runners (RealMetaPRD §11 line 1538-1540).
"""
from __future__ import annotations

import time
from typing import Any, TYPE_CHECKING

from .base import AgentResult, TokenUsage
from ..prompt_hash import prompt_hash

if TYPE_CHECKING:
    from ..context import FingentContext


class AdkRunner:
    async def _run_impl(self, *, system: str, tools: list[dict], messages: list[dict],
                        model: str, temperature: float, max_tokens: int,
                        seed: int | None) -> dict[str, Any]:
        raise NotImplementedError(
            "ADK runner stub-only in Phase 1; see RealMetaPRD §14 risk #4."
        )

    async def run(
        self,
        *,
        ctx: "FingentContext",
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
            system=system, tools=tools, messages=messages,
            model=model, temperature=temperature, max_tokens=max_tokens, seed=seed,
        )
        elapsed_ms = int((time.monotonic() - start) * 1000)
        u = impl.get("usage") or {}
        return AgentResult(
            output=impl.get("output"),
            model=impl.get("model", model),
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
