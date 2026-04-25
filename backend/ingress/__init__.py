"""Routing-table loader for inbound webhooks.

Source: RealMetaPRD §7.1. The router resolves an inbound `eventType` to a
list of pipeline names via `routing.yaml`. Unknown event types fall back to
`defaults.unknown_event`.

The loader is deliberately tiny — strict-keys validation only, no caching.
Callers that want caching wrap it themselves; webhooks read on each call so
that operators can hot-edit the YAML without restarting the worker.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml


_TOP_LEVEL_KEYS = frozenset({"routes", "defaults"})


class RoutingLoadError(ValueError):
    """Raised when `routing.yaml` is structurally invalid."""


def _ensure(condition: bool, msg: str) -> None:
    if not condition:
        raise RoutingLoadError(msg)


def load_routing(path: str | Path) -> dict[str, Any]:
    """Load and validate the routing YAML. Returns the full mapping.

    Validates:
      - Top level is a mapping.
      - Only `routes` and `defaults` are allowed at the top level.
      - `routes` is a mapping of `event_type -> list[str]`.
      - `defaults` is a mapping; `unknown_event` (if present) is `list[str]`.
    """
    p = Path(path)
    source = str(p)
    with p.open("r", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh)

    _ensure(isinstance(raw, dict), f"{source}: top-level must be a mapping")

    unknown = set(raw.keys()) - _TOP_LEVEL_KEYS
    _ensure(not unknown, f"{source}: unknown top-level keys: {sorted(unknown)}")

    routes_raw = raw.get("routes", {})
    defaults_raw = raw.get("defaults", {})

    _ensure(isinstance(routes_raw, dict), f"{source}: 'routes' must be a mapping")
    _ensure(isinstance(defaults_raw, dict), f"{source}: 'defaults' must be a mapping")

    for event_type, pipelines in routes_raw.items():
        _ensure(
            isinstance(event_type, str) and event_type,
            f"{source}: routes key must be a non-empty string, got {event_type!r}",
        )
        _ensure(
            isinstance(pipelines, list),
            f"{source}: routes[{event_type!r}] must be a list, got {type(pipelines).__name__}",
        )
        for name in pipelines:
            _ensure(
                isinstance(name, str) and name,
                f"{source}: routes[{event_type!r}] entries must be non-empty strings, got {name!r}",
            )

    if "unknown_event" in defaults_raw:
        unknown_pipelines = defaults_raw["unknown_event"]
        _ensure(
            isinstance(unknown_pipelines, list),
            f"{source}: defaults.unknown_event must be a list",
        )
        for name in unknown_pipelines:
            _ensure(
                isinstance(name, str) and name,
                f"{source}: defaults.unknown_event entries must be non-empty strings, got {name!r}",
            )

    return {"routes": routes_raw, "defaults": defaults_raw}


def routes(path: str | Path) -> dict[str, list[str]]:
    """Return just the `routes` table from the routing YAML."""
    return load_routing(path)["routes"]


def defaults(path: str | Path) -> list[str]:
    """Return the `defaults.unknown_event` pipelines list (empty if absent)."""
    return load_routing(path)["defaults"].get("unknown_event", [])


__all__ = ["load_routing", "routes", "defaults", "RoutingLoadError"]
