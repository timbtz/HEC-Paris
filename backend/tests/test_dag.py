"""Topological-layer + cycle detection invariants."""
from __future__ import annotations

import pytest

from backend.orchestration.dag import topological_layers
from backend.orchestration.yaml_loader import PipelineLoadError, PipelineNode


def _node(nid: str, *deps: str) -> PipelineNode:
    return PipelineNode(id=nid, tool="tools.noop:run", depends_on=tuple(deps))


def test_diamond():
    nodes = (_node("a"), _node("b", "a"), _node("c", "a"), _node("d", "b", "c"))
    layers = topological_layers(nodes)
    ids = [[n.id for n in layer] for layer in layers]
    assert ids == [["a"], ["b", "c"], ["d"]]


def test_cycle_two_nodes():
    nodes = (
        PipelineNode(id="a", tool="tools.noop:run", depends_on=("b",)),
        PipelineNode(id="b", tool="tools.noop:run", depends_on=("a",)),
    )
    with pytest.raises(PipelineLoadError, match="cycle"):
        topological_layers(nodes)


def test_cycle_self_loop():
    nodes = (PipelineNode(id="x", tool="tools.noop:run", depends_on=("x",)),)
    with pytest.raises(PipelineLoadError):
        topological_layers(nodes)


def test_linear_chain():
    nodes = (_node("a"), _node("b", "a"), _node("c", "b"))
    layers = topological_layers(nodes)
    assert [[n.id for n in layer] for layer in layers] == [["a"], ["b"], ["c"]]


def test_single_node():
    nodes = (_node("only"),)
    assert topological_layers(nodes) == [list(nodes)]
