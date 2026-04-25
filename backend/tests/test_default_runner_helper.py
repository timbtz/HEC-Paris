"""Tests for `registries.default_runner()` + `default_cerebras_model()`."""
from __future__ import annotations

import pytest

from backend.orchestration.registries import (
    default_cerebras_model,
    default_runner,
)


def test_defaults_to_anthropic_when_unset(monkeypatch):
    monkeypatch.delenv("AGNES_LLM_PROVIDER", raising=False)
    assert default_runner() == "anthropic"


def test_cerebras_maps_to_pydantic_ai(monkeypatch):
    monkeypatch.setenv("AGNES_LLM_PROVIDER", "cerebras")
    assert default_runner() == "pydantic_ai"


def test_anthropic_explicit(monkeypatch):
    monkeypatch.setenv("AGNES_LLM_PROVIDER", "anthropic")
    assert default_runner() == "anthropic"


def test_case_insensitive(monkeypatch):
    monkeypatch.setenv("AGNES_LLM_PROVIDER", "Cerebras")
    assert default_runner() == "pydantic_ai"


def test_unknown_value_falls_back_to_anthropic(monkeypatch):
    monkeypatch.setenv("AGNES_LLM_PROVIDER", "foobar")
    assert default_runner() == "anthropic"


def test_adk_routes_to_adk(monkeypatch):
    monkeypatch.setenv("AGNES_LLM_PROVIDER", "adk")
    assert default_runner() == "adk"


# default_cerebras_model — per-role model selection ----------------------


def test_classifier_default_is_free_tier_llama_8b(monkeypatch):
    monkeypatch.delenv("AGNES_CEREBRAS_CLASSIFIER_MODEL", raising=False)
    assert default_cerebras_model("classifier") == "llama3.1-8b"


def test_anomaly_default_is_free_tier_qwen(monkeypatch):
    monkeypatch.delenv("AGNES_CEREBRAS_ANOMALY_MODEL", raising=False)
    assert default_cerebras_model("anomaly") == "qwen-3-235b-a22b-instruct-2507"


def test_classifier_override_via_env(monkeypatch):
    monkeypatch.setenv("AGNES_CEREBRAS_CLASSIFIER_MODEL", "gpt-oss-120b")
    assert default_cerebras_model("classifier") == "gpt-oss-120b"


def test_anomaly_override_via_env(monkeypatch):
    monkeypatch.setenv("AGNES_CEREBRAS_ANOMALY_MODEL", "llama3.3-70b")
    assert default_cerebras_model("anomaly") == "llama3.3-70b"


def test_unknown_role_falls_back_to_classifier_default(monkeypatch):
    monkeypatch.delenv("AGNES_CEREBRAS_CLASSIFIER_MODEL", raising=False)
    monkeypatch.delenv("AGNES_CEREBRAS_DEFAULT_MODEL", raising=False)
    assert default_cerebras_model("writer") == "llama3.1-8b"
