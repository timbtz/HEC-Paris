"""Pipeline YAML DSL loader.

Source: 02_YAML_WORKFLOW_DSL.md:18-92, RealMetaPRD §7.3-§7.4. We follow the
RealMetaPRD DSL (Path B from PRD1_VALIDATION_BRIEFING C2): node fields are
`tool` / `agent` with `module.path:symbol` strings, NOT `tool_class:` /
`agent_class:` with CamelCase keys.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


_NODE_KEYS = frozenset({
    "id", "tool", "agent", "runner", "depends_on", "when", "cacheable",
})
_PIPELINE_KEYS = frozenset({"name", "version", "trigger", "nodes"})


class PipelineLoadError(ValueError):
    """Raised when a pipeline YAML is structurally invalid."""


@dataclass(frozen=True)
class PipelineNode:
    id: str
    tool: str | None = None
    agent: str | None = None
    runner: str | None = None
    depends_on: tuple[str, ...] = ()
    when: str | None = None
    cacheable: bool = False

    @property
    def is_agent(self) -> bool:
        return self.agent is not None

    @property
    def is_tool(self) -> bool:
        return self.tool is not None


@dataclass(frozen=True)
class Pipeline:
    name: str
    version: int
    trigger: dict[str, Any]
    nodes: tuple[PipelineNode, ...]


def _ensure(condition: bool, msg: str) -> None:
    if not condition:
        raise PipelineLoadError(msg)


def parse(raw: dict[str, Any], *, source: str = "<dict>") -> Pipeline:
    """Validate the parsed YAML mapping and project it onto the dataclasses."""
    _ensure(isinstance(raw, dict), f"{source}: top-level must be a mapping")

    unknown = set(raw.keys()) - _PIPELINE_KEYS
    _ensure(not unknown, f"{source}: unknown top-level keys: {sorted(unknown)}")

    for required in ("name", "version", "trigger", "nodes"):
        _ensure(required in raw, f"{source}: missing required top-level key '{required}'")

    name = raw["name"]
    version = raw["version"]
    trigger = raw["trigger"]
    nodes_raw = raw["nodes"]

    _ensure(isinstance(name, str) and name, f"{source}: 'name' must be a non-empty string")
    _ensure(isinstance(version, int), f"{source}: 'version' must be an int")
    _ensure(isinstance(trigger, dict), f"{source}: 'trigger' must be a mapping")
    _ensure(isinstance(nodes_raw, list) and nodes_raw, f"{source}: 'nodes' must be a non-empty list")

    seen_ids: set[str] = set()
    nodes: list[PipelineNode] = []

    for idx, n in enumerate(nodes_raw):
        loc = f"{source}: node[{idx}]"
        _ensure(isinstance(n, dict), f"{loc}: must be a mapping")

        unknown = set(n.keys()) - _NODE_KEYS
        _ensure(not unknown, f"{loc}: unknown keys: {sorted(unknown)}")

        nid = n.get("id")
        _ensure(isinstance(nid, str) and nid, f"{loc}: 'id' must be a non-empty string")
        _ensure(nid not in seen_ids, f"{loc}: duplicate node id '{nid}'")
        seen_ids.add(nid)

        tool = n.get("tool")
        agent = n.get("agent")
        _ensure(
            (tool is None) ^ (agent is None),
            f"{loc}: exactly one of 'tool' or 'agent' must be set",
        )
        if tool is not None:
            _ensure(isinstance(tool, str) and ":" in tool,
                    f"{loc}: 'tool' must be 'module.path:symbol'")
        if agent is not None:
            _ensure(isinstance(agent, str) and ":" in agent,
                    f"{loc}: 'agent' must be 'module.path:symbol'")

        runner = n.get("runner")
        if agent is not None:
            _ensure(runner is not None, f"{loc}: agent nodes require a 'runner'")
        if runner is not None:
            _ensure(isinstance(runner, str) and runner,
                    f"{loc}: 'runner' must be a non-empty string")

        deps = n.get("depends_on", [])
        _ensure(isinstance(deps, list), f"{loc}: 'depends_on' must be a list")
        for d in deps:
            _ensure(isinstance(d, str), f"{loc}: 'depends_on' entries must be strings")

        when = n.get("when")
        if when is not None:
            _ensure(isinstance(when, str) and ":" in when,
                    f"{loc}: 'when' must be 'module.path:symbol'")

        cacheable = n.get("cacheable", False)
        _ensure(isinstance(cacheable, bool), f"{loc}: 'cacheable' must be a bool")

        nodes.append(PipelineNode(
            id=nid,
            tool=tool,
            agent=agent,
            runner=runner,
            depends_on=tuple(deps),
            when=when,
            cacheable=cacheable,
        ))

    # Validate depends_on references *after* we've collected all ids.
    for node in nodes:
        for d in node.depends_on:
            _ensure(d in seen_ids,
                    f"{source}: node '{node.id}' depends_on missing id '{d}'")

    return Pipeline(name=name, version=version, trigger=dict(trigger), nodes=tuple(nodes))


def load(path: Path) -> Pipeline:
    """Read and validate a pipeline YAML file.

    The filename stem must equal the pipeline `name:` field
    (02_YAML_WORKFLOW_DSL.md:38).
    """
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    pipeline = parse(raw, source=str(path))
    if path.stem != pipeline.name:
        raise PipelineLoadError(
            f"{path}: filename stem '{path.stem}' must match pipeline name '{pipeline.name}'"
        )
    return pipeline
