"""AgnesContext — the per-run handoff between executor, tools, and agents.

Source: 01_ORCHESTRATION_REFERENCE.md:114-124, RealMetaPRD §6.5 line 547.
Adapted: drop the per-DB-path fields (we hold StoreHandles instead) and add
`employee_id` for cost attribution (RealMetaPRD §11 line 1542-1544).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from .store.bootstrap import StoreHandles


@dataclass
class AgnesContext:
    run_id: int
    pipeline_name: str
    trigger_source: str
    trigger_payload: dict[str, Any]
    node_outputs: dict[str, Any]
    store: "StoreHandles"
    employee_id: int | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def get(self, node_id: str, default: Any = None) -> Any:
        """Read another node's output. By convention, never write here."""
        return self.node_outputs.get(node_id, default)
