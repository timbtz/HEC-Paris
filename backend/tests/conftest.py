"""Shared pytest fixtures.

`asyncio_mode = auto` (pytest.ini) — every async test is auto-collected.
Each test gets its own tmp_path-based DB stack so there's zero shared state.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import pytest_asyncio

from backend.orchestration import event_bus
from backend.orchestration.runners import anthropic_runner
from backend.orchestration.runners.base import AgentResult, TokenUsage
from backend.orchestration.store.bootstrap import open_dbs


@pytest_asyncio.fixture
async def store(tmp_path: Path):
    """Open three fresh DBs in tmp_path with all migrations applied."""
    handles = await open_dbs(tmp_path)
    try:
        yield handles
    finally:
        await handles.close()


@pytest_asyncio.fixture(autouse=True)
async def _reset_event_bus():
    """Keep event-bus state isolated between tests."""
    await event_bus.reset_for_tests()
    yield
    await event_bus.reset_for_tests()


@pytest.fixture
def fake_anthropic_message():
    """Return a default fake AsyncAnthropic.messages.create response."""
    def _build(
        text: str = "ok",
        usage: dict[str, int] | None = None,
        stop_reason: str = "end_turn",
        msg_id: str = "msg_test_001",
        model: str = "claude-haiku-4-5",
        tool_input: dict[str, Any] | None = None,
        tool_name: str = "submit_test",
    ) -> Any:
        usage_obj = SimpleNamespace(
            input_tokens=(usage or {}).get("input_tokens", 100),
            output_tokens=(usage or {}).get("output_tokens", 50),
            cache_creation_input_tokens=(usage or {}).get("cache_write_tokens", 0),
            cache_read_input_tokens=(usage or {}).get("cache_read_tokens", 0),
        )
        if tool_input is not None:
            content = [SimpleNamespace(type="tool_use", id="tu_1", name=tool_name, input=tool_input)]
        else:
            content = [SimpleNamespace(type="text", text=text)]
        return SimpleNamespace(
            id=msg_id,
            model=model,
            stop_reason=stop_reason,
            content=content,
            usage=usage_obj,
        )
    return _build


@pytest.fixture
def fake_anthropic(monkeypatch, fake_anthropic_message):
    """Replace `anthropic_runner._client` with a recorded stub.

    Yields the call-log list. Each call is a dict of the kwargs.
    """
    calls: list[dict[str, Any]] = []
    default_response = fake_anthropic_message()

    class _FakeMessages:
        async def create(self_inner, **kwargs):  # noqa: N805
            calls.append(kwargs)
            return getattr(self_inner, "_response", default_response)

    class _FakeClient:
        def __init__(self):
            self.messages = _FakeMessages()
            self.messages._response = default_response  # type: ignore[attr-defined]

    fake = _FakeClient()
    monkeypatch.setattr(anthropic_runner, "_client", fake)
    yield calls, fake
    monkeypatch.setattr(anthropic_runner, "_client", None)
