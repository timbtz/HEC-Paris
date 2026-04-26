"""COST_TABLE + micro_usd math."""
from __future__ import annotations

import pytest

from backend.orchestration.cost import COST_TABLE_MICRO_USD, micro_usd
from backend.orchestration.runners.base import TokenUsage


def test_anthropic_haiku_known_value():
    """At Haiku 4 rates ($0.80 / $4 / $0.08 / $1 per M):
       100*800_000 + 50*4_000_000 + 10*80_000 + 5*1_000_000
         = 80_000_000 + 200_000_000 + 800_000 + 5_000_000
         = 285_800_000 micro-USD-total; // 1_000_000 = 285 micro-USD.
    """
    u = TokenUsage(100, 50, 10, 5, 0)
    assert micro_usd(u, "anthropic", "claude-haiku-4-5") == 285


def test_anthropic_haiku_one_million_input():
    """1_000_000 input × $0.80/M = $0.80 = 800_000 micro-USD."""
    u = TokenUsage(input_tokens=1_000_000)
    assert micro_usd(u, "anthropic", "claude-haiku-4-5") == 800_000


def test_anthropic_sonnet_one_million_output():
    """1_000_000 output × $15/M = $15.00 = 15_000_000 micro-USD."""
    u = TokenUsage(output_tokens=1_000_000)
    assert micro_usd(u, "anthropic", "claude-sonnet-4-6") == 15_000_000


def test_unknown_provider_raises():
    u = TokenUsage(input_tokens=100)
    with pytest.raises(KeyError):
        micro_usd(u, "openai", "gpt-4")


def test_unknown_model_raises():
    u = TokenUsage(input_tokens=100)
    with pytest.raises(KeyError):
        micro_usd(u, "anthropic", "claude-non-existent")


def test_table_has_expected_keys():
    """Sanity: every Anthropic/Cerebras model named in PRD is in the table."""
    expected = {
        ("anthropic", "claude-opus-4-7"),
        ("anthropic", "claude-sonnet-4-6"),
        ("anthropic", "claude-haiku-4-5"),
        ("cerebras",  "llama3.3-70b"),
        ("cerebras",  "gpt-oss-120b"),
        ("cerebras",  "qwen-3-235b"),
    }
    assert expected.issubset(COST_TABLE_MICRO_USD.keys())
