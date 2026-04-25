"""Swan webhook ingress.

Source: RealMetaPRD §7.1, §9.2 (constant-time signature compare).

The handler verifies the Swan-shared-secret header *before* parsing the
JSON body (so a malformed body cannot bypass auth), records the envelope
in `orchestration.external_events` with `INSERT OR IGNORE` for at-least
once safety, resolves the originating employee from `audit.employees`
(NULL is a legitimate value for company-account events), and dispatches
the configured pipelines via `routing.yaml`.

Pipeline dispatch is defensive: missing pipeline files are logged and
skipped so an unfinished route never 500s the inbound webhook.
"""
from __future__ import annotations

import hmac
import json
import logging
import os
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from .. import ingress
from ..orchestration.executor import execute_pipeline
from ..orchestration.store.writes import write_tx
from ..orchestration.yaml_loader import PipelineLoadError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/swan")

_REQUIRED_FIELDS: tuple[str, ...] = (
    "eventType", "eventId", "eventDate", "projectId", "resourceId",
)

_ROUTING_PATH = Path(__file__).resolve().parent.parent / "ingress" / "routing.yaml"

# Module-level cache; tests may monkey-patch `_get_routing` to substitute.
_routing_cache: dict[str, Any] | None = None


def _get_routing() -> dict[str, Any]:
    """Lazy-load and cache the routing table."""
    global _routing_cache
    if _routing_cache is None:
        _routing_cache = ingress.load_routing(_ROUTING_PATH)
    return _routing_cache


def _resolve_pipelines(routing: dict[str, Any], event_type: str) -> list[str]:
    routes = routing.get("routes", {}) or {}
    key = f"swan.{event_type}"
    if key in routes:
        return list(routes[key])
    defaults = routing.get("defaults", {}) or {}
    return list(defaults.get("unknown_event", []) or [])


async def _resolve_employee_id(store, resource_id: str) -> int | None:
    cur = await store.audit.execute(
        "SELECT id FROM employees WHERE swan_account_id = ? OR swan_iban = ?",
        (resource_id, resource_id),
    )
    row = await cur.fetchone()
    await cur.close()
    if row is None:
        return None
    return int(row[0])


@router.post("/webhook")
async def swan_webhook(request: Request) -> dict[str, Any]:
    raw_body = await request.body()

    # 1. Constant-time signature check BEFORE parsing JSON.
    expected_secret = os.environ.get("SWAN_WEBHOOK_SECRET", "")
    provided = request.headers.get("x-swan-secret")
    if provided is None or not hmac.compare_digest(
        provided.encode(), expected_secret.encode()
    ):
        raise HTTPException(status_code=401, detail="invalid signature")

    # 2. Parse + validate envelope.
    try:
        envelope = json.loads(raw_body.decode("utf-8") or "{}")
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=400, detail=f"invalid json: {exc}") from exc
    if not isinstance(envelope, dict):
        raise HTTPException(status_code=400, detail="envelope must be an object")

    missing = [f for f in _REQUIRED_FIELDS if f not in envelope]
    if missing:
        raise HTTPException(
            status_code=400,
            detail=f"missing required envelope field(s): {','.join(missing)}",
        )

    event_id = envelope["eventId"]
    event_type = envelope["eventType"]
    resource_id = envelope["resourceId"]

    store = request.app.state.store
    payload_json = json.dumps(envelope, separators=(",", ":"))

    # 3+5. INSERT OR IGNORE then check duplicate; mark processed in same txn.
    is_duplicate = False
    async with write_tx(store.orchestration, store.orchestration_lock) as conn:
        await conn.execute(
            "INSERT OR IGNORE INTO external_events "
            "(provider, event_id, event_type, resource_id, payload) "
            "VALUES (?, ?, ?, ?, ?)",
            ("swan", event_id, event_type, resource_id, payload_json),
        )
        cur = await conn.execute(
            "SELECT id, processed FROM external_events "
            "WHERE provider = ? AND event_id = ?",
            ("swan", event_id),
        )
        row = await cur.fetchone()
        await cur.close()
        if row is None:  # pragma: no cover — INSERT OR IGNORE just ran
            raise HTTPException(status_code=500, detail="external_events row vanished")
        row_id, processed = int(row[0]), int(row[1])
        if processed == 1:
            is_duplicate = True
        else:
            await conn.execute(
                "UPDATE external_events SET processed = 1 WHERE id = ?",
                (row_id,),
            )

    if is_duplicate:
        return {"status": "duplicate", "event_id": event_id}

    # 6. Resolve employee (NULL allowed).
    employee_id = await _resolve_employee_id(store, resource_id)

    # 7. Dispatch pipelines (defensively).
    routing = _get_routing()
    pipeline_names = _resolve_pipelines(routing, event_type)

    run_ids: list[int] = []
    for name in pipeline_names:
        try:
            rid = await execute_pipeline(
                name,
                trigger_source=f"swan.{event_type}",
                trigger_payload=envelope,
                store=store,
                employee_id=employee_id,
            )
            run_ids.append(rid)
        except (FileNotFoundError, PipelineLoadError) as exc:
            logger.warning(
                "swan_webhook.pipeline_skipped",
                extra={
                    "pipeline": name,
                    "event_type": event_type,
                    "event_id": event_id,
                    "error": str(exc),
                },
            )

    return {"status": "ok", "event_id": event_id, "run_ids": run_ids}
