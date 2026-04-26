"""AI-credit cost accounting.

Source: RealMetaPRD §7.7 (lines 1192-1216). Integer micro-USD per
million tokens, pinned per (provider, model). Refresh monthly.

Rates verified 2026-04-26 against publicly listed market pricing:
  - Anthropic console: claude.com/api/pricing  (Opus 4: $15/$75/M;
    Sonnet 4: $3/$15/M; Haiku 4: $0.80/$4/M; cache reads at 10% of
    input, cache writes at 1.25× input).
  - Cerebras inference: cerebras.ai/inference  (Build tier — llama3.1-8b
    $0.10/$0.10; llama3.3-70b $0.85/$1.20; gpt-oss-120b $0.25/$0.69;
    qwen3-235b $0.60/$1.20). Cerebras has no input cache — cache_read
    and cache_write fall back to the input rate.

The unit is **micro-USD per million tokens**: $1/M = 1_000_000 µUSD/M.
"""
from __future__ import annotations

from .runners.base import TokenUsage

# Rates are integer micro-USD per million tokens.
COST_TABLE_MICRO_USD: dict[tuple[str, str], dict[str, int]] = {
    # Anthropic — Claude 4 family (USD/M tokens):
    #   Opus 4:   $15.00 input / $75.00 output / $1.50 cache_read / $18.75 cache_write
    #   Sonnet 4: $ 3.00 input / $15.00 output / $0.30 cache_read / $ 3.75 cache_write
    #   Haiku 4:  $ 0.80 input / $ 4.00 output / $0.08 cache_read / $ 1.00 cache_write
    ("anthropic", "claude-opus-4-7"):
        {"input": 15_000_000, "output": 75_000_000, "cache_read": 1_500_000, "cache_write": 18_750_000},
    ("anthropic", "claude-sonnet-4-6"):
        {"input":  3_000_000, "output": 15_000_000, "cache_read":   300_000, "cache_write":  3_750_000},
    ("anthropic", "claude-haiku-4-5"):
        {"input":    800_000, "output":  4_000_000, "cache_read":    80_000, "cache_write":  1_000_000},
    # Cerebras — Build tier (USD/M tokens):
    #   llama3.1-8b:  $0.10 in / $0.10 out  (deprecates 2026-05-27)
    #   llama3.3-70b: $0.85 in / $1.20 out
    #   gpt-oss-120b: $0.25 in / $0.69 out
    #   qwen3-235b:   $0.60 in / $1.20 out  (deprecates 2026-05-27)
    # Cerebras has no input cache — cache_read/cache_write mirror the input rate.
    ("cerebras",  "llama3.1-8b"):
        {"input":    100_000, "output":    100_000, "cache_read":   100_000, "cache_write":    100_000},
    ("cerebras",  "llama3.3-70b"):
        {"input":    850_000, "output":  1_200_000, "cache_read":   850_000, "cache_write":    850_000},
    ("cerebras",  "gpt-oss-120b"):
        {"input":    250_000, "output":    690_000, "cache_read":   250_000, "cache_write":    250_000},
    ("cerebras",  "qwen-3-235b"):
        {"input":    600_000, "output":  1_200_000, "cache_read":   600_000, "cache_write":    600_000},
    # Cerebras returns the full versioned model id (e.g. `qwen-3-235b-a22b-
    # instruct-2507`) in completion responses; alias it to the canonical
    # rate row so the cost lookup hits. Deprecates 2026-05-27.
    ("cerebras",  "qwen-3-235b-a22b-instruct-2507"):
        {"input":    600_000, "output":  1_200_000, "cache_read":   600_000, "cache_write":    600_000},
}


def micro_usd(usage: TokenUsage, provider: str, model: str) -> int:
    """Return integer micro-USD spent on a single call.

    Floor division by 1_000_000 — the table is per-million; we charge in
    discrete micros.
    """
    r = COST_TABLE_MICRO_USD[(provider, model)]   # KeyError on unknown pair — fail loudly.
    # Reasoning tokens are billed as completion tokens
    # (CEREBRAS_STACK_REFERENCE.md §9). Anthropic always reports 0 for this
    # field (anthropic_runner.py:93), so the term is additive and safe.
    return (
        usage.input_tokens       * r["input"]
        + usage.output_tokens      * r["output"]
        + usage.cache_read_tokens  * r["cache_read"]
        + usage.cache_write_tokens * r["cache_write"]
        + usage.reasoning_tokens   * r["output"]
    ) // 1_000_000
