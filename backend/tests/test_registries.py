"""Registry resolution semantics."""
from __future__ import annotations

import pytest

from backend.orchestration import registries


def test_get_tool_resolves():
    fn = registries.get_tool("tools.noop:run")
    assert callable(fn)


def test_get_agent_resolves():
    fn = registries.get_agent("agents.noop:run")
    assert callable(fn)


def test_get_runner_returns_instance():
    r = registries.get_runner("anthropic")
    assert hasattr(r, "run")


def test_get_condition_resolves():
    fn = registries.get_condition("conditions.gating:posted")
    assert callable(fn)


def test_unknown_tool_raises_keyerror():
    with pytest.raises(KeyError):
        registries.get_tool("tools.does_not_exist:run")


def test_unknown_runner_raises_keyerror():
    with pytest.raises(KeyError):
        registries.get_runner("not_a_runner")


def test_resolution_is_cached():
    fn1 = registries.get_tool("tools.noop:run")
    fn2 = registries.get_tool("tools.noop:run")
    assert fn1 is fn2


def test_register_then_get():
    registries.register_tool("tools.noop:run_alias", "backend.orchestration.tools.noop:run")
    fn = registries.get_tool("tools.noop:run_alias")
    assert callable(fn)
