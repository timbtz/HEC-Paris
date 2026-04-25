"""Four flat-dict registries — tools, agents, runners, conditions.

Source: 01_ORCHESTRATION_REFERENCE.md:138-151 + RealMetaPRD §6.4.
A KeyError on miss is the right behavior — it signals a misconfigured
pipeline and we fail loudly rather than silently routing to a default.
"""
from __future__ import annotations

import importlib
import os
from functools import lru_cache
from typing import Any, Callable


_TOOL_REGISTRY: dict[str, str] = {
    "tools.noop:run": "backend.orchestration.tools.noop:run",
}

_AGENT_REGISTRY: dict[str, str] = {
    "agents.noop:run": "backend.orchestration.agents.noop_agent:run",
}

_RUNNER_REGISTRY: dict[str, str] = {
    "anthropic":   "backend.orchestration.runners.anthropic_runner:AnthropicRunner",
    "adk":         "backend.orchestration.runners.adk_runner:AdkRunner",
    "pydantic_ai": "backend.orchestration.runners.pydantic_ai_runner:PydanticAiRunner",
}

_CONDITION_REGISTRY: dict[str, str] = {
    "conditions.gating:passes_confidence":
        "backend.orchestration.conditions.gating:passes_confidence",
    "conditions.gating:needs_review":
        "backend.orchestration.conditions.gating:needs_review",
    "conditions.gating:posted":
        "backend.orchestration.conditions.gating:posted",
}


def _import_dotted(dotted: str) -> Any:
    module_path, attr = dotted.rsplit(":", 1)
    module = importlib.import_module(module_path)
    return getattr(module, attr)


@lru_cache(maxsize=None)
def _resolve(registry_name: str, key: str) -> Any:
    registry = _REGISTRIES[registry_name]
    dotted = registry[key]  # KeyError on miss is intentional.
    return _import_dotted(dotted)


def get_tool(key: str) -> Callable[..., Any]:
    return _resolve("tool", key)


def get_agent(key: str) -> Callable[..., Any]:
    return _resolve("agent", key)


def get_runner(key: str) -> Any:
    """Return an *instance* of the runner class.

    Runners are stateless (the AsyncAnthropic singleton lives inside the
    module), so we instantiate once per call and return; the class itself
    is what's cached via `_resolve`.
    """
    cls = _resolve("runner", key)
    return cls()


def get_condition(key: str) -> Callable[..., bool]:
    return _resolve("condition", key)


def register_tool(key: str, dotted: str) -> None:
    _TOOL_REGISTRY[key] = dotted
    _resolve.cache_clear()


def register_agent(key: str, dotted: str) -> None:
    _AGENT_REGISTRY[key] = dotted
    _resolve.cache_clear()


def register_runner(key: str, dotted: str) -> None:
    _RUNNER_REGISTRY[key] = dotted
    _resolve.cache_clear()


def register_condition(key: str, dotted: str) -> None:
    _CONDITION_REGISTRY[key] = dotted
    _resolve.cache_clear()


_REGISTRIES: dict[str, dict[str, str]] = {
    "tool": _TOOL_REGISTRY,
    "agent": _AGENT_REGISTRY,
    "runner": _RUNNER_REGISTRY,
    "condition": _CONDITION_REGISTRY,
}


def default_runner() -> str:
    """Return the runner-registry key for the configured classifier provider.

    Reads ``AGNES_LLM_PROVIDER`` at call time (not import time) so tests
    can flip the env var with ``monkeypatch.setenv`` without re-importing.

    Mapping:
      - ``cerebras`` -> ``pydantic_ai`` (Cerebras runner; registry-name
        misnomer kept for compat with the executor's provider mapping —
        see plan §NOTES design decision #3).
      - ``adk``      -> ``adk`` (no live impl yet; future ADK runner).
      - default / unknown -> ``anthropic``.
    """
    provider = os.environ.get("AGNES_LLM_PROVIDER", "anthropic").lower()
    if provider == "cerebras":
        return "pydantic_ai"
    if provider == "adk":
        return "adk"
    return "anthropic"


def default_cerebras_model(role: str) -> str:
    """Pick a Cerebras model id by agent role.

    Three-tier free-tier-friendly defaults:
      - ``classifier`` (counterparty + GL hot-path, <5s SLA) ->
        ``llama3.1-8b`` ($0.10/$0.10/M, ~2,170 tps; CEREBRAS_STACK_REFERENCE
        §3 deprecation 2026-05-27).
      - ``anomaly`` (off-SLA structured tool call) ->
        ``qwen-3-235b-a22b-instruct-2507`` ($0.60/$1.20/M, free-tier
        accessible; CEREBRAS_STACK_REFERENCE §3 deprecation 2026-05-27).
      - any other role -> the classifier default.

    Both defaults are picked because they're reachable on a free-tier
    Cerebras key — `gpt-oss-120b` and `llama3.3-70b` require Developer
    tier. Override at runtime when you upgrade:
      AGNES_CEREBRAS_CLASSIFIER_MODEL=gpt-oss-120b
      AGNES_CEREBRAS_ANOMALY_MODEL=gpt-oss-120b
    """
    if role == "classifier":
        return os.environ.get("AGNES_CEREBRAS_CLASSIFIER_MODEL", "llama3.1-8b")
    if role == "anomaly":
        return os.environ.get(
            "AGNES_CEREBRAS_ANOMALY_MODEL",
            "qwen-3-235b-a22b-instruct-2507",
        )
    return os.environ.get("AGNES_CEREBRAS_DEFAULT_MODEL", "llama3.1-8b")
