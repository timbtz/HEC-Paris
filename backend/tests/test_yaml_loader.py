"""YAML loader strict-key + structural validation."""
from __future__ import annotations

from pathlib import Path

import pytest

from backend.orchestration.yaml_loader import (
    Pipeline, PipelineLoadError, load, parse,
)


def _good() -> dict:
    return {
        "name": "p",
        "version": 1,
        "trigger": {"source": "manual"},
        "nodes": [
            {"id": "a", "tool": "tools.noop:run"},
            {"id": "b", "tool": "tools.noop:run", "depends_on": ["a"]},
        ],
    }


def test_parse_happy_path():
    p = parse(_good())
    assert isinstance(p, Pipeline)
    assert p.name == "p" and len(p.nodes) == 2
    assert p.nodes[1].depends_on == ("a",)


def test_unknown_top_level_key_rejected():
    raw = _good() | {"oops": True}
    with pytest.raises(PipelineLoadError, match="unknown top-level keys"):
        parse(raw)


def test_unknown_node_key_rejected():
    raw = _good()
    raw["nodes"][0]["params"] = {"foo": 1}
    with pytest.raises(PipelineLoadError, match="unknown keys"):
        parse(raw)


def test_both_tool_and_agent_rejected():
    raw = _good()
    raw["nodes"][0]["agent"] = "agents.x:run"
    with pytest.raises(PipelineLoadError, match="exactly one of"):
        parse(raw)


def test_neither_tool_nor_agent_rejected():
    raw = _good()
    del raw["nodes"][0]["tool"]
    with pytest.raises(PipelineLoadError, match="exactly one of"):
        parse(raw)


def test_duplicate_id_rejected():
    raw = _good()
    raw["nodes"][1]["id"] = "a"
    with pytest.raises(PipelineLoadError, match="duplicate node id"):
        parse(raw)


def test_missing_dep_rejected():
    raw = _good()
    raw["nodes"][1]["depends_on"] = ["zzz"]
    with pytest.raises(PipelineLoadError, match="missing id"):
        parse(raw)


def test_agent_requires_runner():
    raw = _good()
    raw["nodes"][0] = {"id": "a", "agent": "agents.noop:run"}  # no runner
    with pytest.raises(PipelineLoadError, match="require a 'runner'"):
        parse(raw)


def test_load_filename_must_match_name(tmp_path: Path):
    bad = tmp_path / "wrongname.yaml"
    bad.write_text(
        "name: noop_demo\nversion: 1\ntrigger: {source: manual}\n"
        "nodes:\n  - {id: a, tool: tools.noop:run}\n",
        encoding="utf-8",
    )
    with pytest.raises(PipelineLoadError, match="filename stem"):
        load(bad)


def test_load_real_noop_demo():
    """The shipped pipeline parses cleanly."""
    from backend.orchestration.executor import _PIPELINES_DIR
    p = load(_PIPELINES_DIR / "noop_demo.yaml")
    assert p.name == "noop_demo"
    ids = [n.id for n in p.nodes]
    assert ids == ["tool-a", "agent-b", "tool-c"]
    assert p.nodes[0].cacheable is True
    assert p.nodes[1].is_agent and p.nodes[1].runner == "anthropic"
