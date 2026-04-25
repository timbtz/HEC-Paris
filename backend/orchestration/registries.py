"""Four flat-dict registries — tools, agents, runners, conditions.

Source: 01_ORCHESTRATION_REFERENCE.md:138-151 + RealMetaPRD §6.4.
A KeyError on miss is the right behavior — it signals a misconfigured
pipeline and we fail loudly rather than silently routing to a default.
"""
from __future__ import annotations

import importlib
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
