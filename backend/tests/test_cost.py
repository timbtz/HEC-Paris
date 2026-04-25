"""COST_TABLE + micro_usd math."""
from __future__ import annotations

import pytest

from backend.orchestration.cost import COST_TABLE_MICRO_USD, micro_usd
from backend.orchestration.runners.base import TokenUsage


def test_anthropic_haiku_known_value():
    """100*800 + 50*4000 + 10*80 + 5*1000 = 285_800 micro-USD; //1e6 = 0."""
    u = TokenUsage(100, 50, 10, 5, 0)
    assert micro_usd(u, "anthropic", "claude-haiku-4-5") == 0


def test_anthropic_haiku_one_million_input():
    """1_000_000 input tokens × 800 / 1_000_000 = 800 micro-USD."""
    u = TokenUsage(input_tokens=1_000_000)
    assert micro_usd(u, "anthropic", "claude-haiku-4-5") == 800


def test_anthropic_sonnet_one_million_output():
    """1_000_000 output × 15000 / 1_000_000 = 15_000 micro-USD = $0.015."""
    u = TokenUsage(output_tokens=1_000_000)
    assert micro_usd(u, "anthropic", "claude-sonnet-4-6") == 15000


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
