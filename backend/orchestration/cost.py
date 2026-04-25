"""AI-credit cost accounting.

Source: RealMetaPRD §7.7 (lines 1192-1216) verbatim. Integer micro-USD per
million tokens, pinned per (provider, model). Refresh monthly.
# verified 2026-04-25 against ANTHROPIC_SDK_STACK_REFERENCE:556 and
#                       CEREBRAS_STACK_REFERENCE:357-368.
"""
from __future__ import annotations

from .runners.base import TokenUsage

# Rates are integer micro-USD per million tokens.
COST_TABLE_MICRO_USD: dict[tuple[str, str], dict[str, int]] = {
    ("anthropic", "claude-opus-4-7"):
        {"input": 15000, "output": 75000, "cache_read": 1500, "cache_write": 18750},
    ("anthropic", "claude-sonnet-4-6"):
        {"input":  3000, "output": 15000, "cache_read":  300, "cache_write":  3750},
    ("anthropic", "claude-haiku-4-5"):
        {"input":   800, "output":  4000, "cache_read":   80, "cache_write":  1000},
    ("cerebras",  "llama3.3-70b"):
        {"input":   600, "output":   600, "cache_read":  600, "cache_write":   600},
    ("cerebras",  "gpt-oss-120b"):
        {"input":   350, "output":   750, "cache_read":  350, "cache_write":   350},
    ("cerebras",  "qwen-3-235b"):
        {"input":   600, "output":  1200, "cache_read":  600, "cache_write":   600},
}


def micro_usd(usage: TokenUsage, provider: str, model: str) -> int:
    """Return integer micro-USD spent on a single call.

    Floor division by 1_000_000 — the table is per-million; we charge in
    discrete micros.
    """
    r = COST_TABLE_MICRO_USD[(provider, model)]   # KeyError on unknown pair — fail loudly.
    return (
        usage.input_tokens       * r["input"]
        + usage.output_tokens      * r["output"]
        + usage.cache_read_tokens  * r["cache_read"]
        + usage.cache_write_tokens * r["cache_write"]
    ) // 1_000_000
