"""Kahn's algorithm — layer-grouped topological sort.

Source: 01_ORCHESTRATION_REFERENCE.md:19-42 verbatim. Layers run in parallel
under `asyncio.gather`; ordering across layers is the dependency closure.
"""
from __future__ import annotations

from collections import defaultdict, deque

from .yaml_loader import PipelineLoadError, PipelineNode


def topological_layers(
    nodes: tuple[PipelineNode, ...] | list[PipelineNode],
) -> list[list[PipelineNode]]:
    """Return nodes grouped into parallelizable layers.

    Layer 0 is roots (no deps). Layer N+1 contains nodes whose `depends_on`
    are all satisfied by layers 0..N. Cycles raise `PipelineLoadError`
    naming the offending node ids.
    """
    by_id: dict[str, PipelineNode] = {n.id: n for n in nodes}
    indegree: dict[str, int] = {n.id: len(n.depends_on) for n in nodes}
    children: dict[str, list[str]] = defaultdict(list)
    for n in nodes:
        for d in n.depends_on:
            children[d].append(n.id)

    queue: deque[str] = deque(nid for nid, deg in indegree.items() if deg == 0)
    layers: list[list[PipelineNode]] = []
    placed = 0

    while queue:
        layer_size = len(queue)
        layer: list[PipelineNode] = []
        next_queue: deque[str] = deque()
        for _ in range(layer_size):
            nid = queue.popleft()
            layer.append(by_id[nid])
            placed += 1
            for child in children[nid]:
                indegree[child] -= 1
                if indegree[child] == 0:
                    next_queue.append(child)
        # Preserve YAML order inside a layer for deterministic logs.
        layer.sort(key=lambda n: list(by_id).index(n.id))
        layers.append(layer)
        queue = next_queue

    if placed != len(nodes):
        remaining = sorted(nid for nid, d in indegree.items() if d > 0)
        raise PipelineLoadError(
            f"cycle detected; nodes still depending on something: {remaining}"
        )

    return layers
