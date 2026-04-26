"""AgentResult, TokenUsage, AgentRunner Protocol.

Source: RealMetaPRD §7.10 (lines 1265-1301) + ANTHROPIC_SDK_STACK_REFERENCE
1087-1107. Every runtime returns the same dataclass; runtimes that don't
surface a field zero-fill it.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Protocol, TYPE_CHECKING

if TYPE_CHECKING:
    from ..context import AgnesContext


@dataclass(frozen=True)
class TokenUsage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    reasoning_tokens: int = 0


@dataclass(frozen=True)
class AgentResult:
    output: Any
    model: str
    response_id: str | None
    prompt_hash: str
    alternatives: list[dict] | None
    confidence: float | None
    usage: TokenUsage
    latency_ms: int
    finish_reason: str | None
    temperature: float | None
    seed: int | None
    raw: dict[str, Any] = field(default_factory=dict)
    # Phase 4.A — Living Rule Wiki citations (PRD-AutonomousCFO §7.3).
    # Each entry is `(wiki_page_id, wiki_revision_id)`. Threads through
    # `prompt_hash`, the cross-run cache, and `propose_checkpoint_commit`
    # so a wiki edit invalidates exactly the agents that read that page
    # and every reasoning decision is cite-able.
    wiki_references: list[tuple[int, int]] = field(default_factory=list)


class AgentRunner(Protocol):
    """Every runner exposes this surface; tests parametrize over the trio."""

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
        wiki_context: Iterable[tuple[int, int]] | None = None,
    ) -> AgentResult: ...
