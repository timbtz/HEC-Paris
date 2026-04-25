"""Confirm every pipeline node references a registered tool/agent/condition."""
from __future__ import annotations
from pathlib import Path

import pytest
import backend.orchestration  # noqa: F401  — triggers registration
from backend.orchestration.registries import get_tool, get_agent, get_condition
from backend.orchestration.yaml_loader import load


PIPELINES_DIR = Path(__file__).resolve().parents[1] / "orchestration" / "pipelines"


@pytest.mark.parametrize("path", sorted(PIPELINES_DIR.glob("*.yaml")))
def test_pipeline_references_resolve(path: Path) -> None:
    pipeline = load(path)
    for node in pipeline.nodes:
        if node.tool is not None:
            assert get_tool(node.tool), f"{path.stem}.{node.id}: tool {node.tool}"
        if node.agent is not None:
            assert get_agent(node.agent), f"{path.stem}.{node.id}: agent {node.agent}"
        if node.when is not None:
            assert get_condition(node.when), f"{path.stem}.{node.id}: condition {node.when}"
