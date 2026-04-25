"""Generic external provider webhook ingress.

Source: RealMetaPRD §7.2; verifier registry pattern.

The endpoint `/external/webhook/{provider}` is a thin wrapper around a
verifier registry. Each verifier takes the raw body, the request headers,
and the provider's shared secret, and returns
`(is_valid, normalized_event_id, normalized_event_type)`. We persist the
envelope in `orchestration.external_events`, dedupe, then dispatch any
configured pipelines via `routing.yaml`.

Stripe is wired today (HMAC-SHA256, `Stripe-Signature` header). Other
providers are added by registering a new verifier; no executor surgery.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
from pathlib import Path
from typing import Any, Callable

from fastapi import APIRouter, HTTPException, Request

from .. import ingress
from ..orchestration.executor import execute_pipeline
from ..orchestration.store.writes import write_tx
from ..orchestration.yaml_loader import PipelineLoadError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/external")

_ROUTING_PATH = Path(__file__).resolve().parent.parent / "ingress" / "routing.yaml"

_routing_cache: dict[str, Any] | None = None


def _get_routing() -> dict[str, Any]:
    global _routing_cache
    if _routing_cache is None:
        _routing_cache = ingress.load_routing(_ROUTING_PATH)
    return _routing_cache


# --------------------------------------------------------------------------- #
# Verifiers
# --------------------------------------------------------------------------- #

VerifierResult = tuple[bool, str | None, str]
Verifier = Callable[[bytes, dict[str, str], str], VerifierResult]


def _normalize_headers(headers: dict[str, str]) -> dict[str, str]:
    """Lowercase keys for case-insensitive lookup."""
    return {k.lower(): v for k, v in headers.items()}


def _verify_stripe(
    body: bytes, headers: dict[str, str], secret: str,
) -> VerifierResult:
    """Stripe webhook signature verification.

    Header format: `t=<timestamp>,v1=<sig>[,v1=<sig2>]`. HMAC is computed
    over `f"{ts}.{body}"`. Multiple `v1=` entries handle key rotation
    grace; we accept the first match.
    """
    h = _normalize_headers(headers)
    sig_header = h.get("stripe-signature")
    if not sig_header or not secret:
        return (False, None, "")

    parts = [p.strip() for p in sig_header.split(",") if p.strip()]
    timestamp: str | None = None
    v1_sigs: list[str] = []
    for part in parts:
        if "=" not in part:
            continue
        k, v = part.split("=", 1)
        if k == "t":
            timestamp = v
        elif k == "v1":
            v1_sigs.append(v)

    if timestamp is None or not v1_sigs:
        return (False, None, "")

    signed_payload = f"{timestamp}.{body.decode('utf-8', errors='replace')}".encode()
    expected = hmac.new(secret.encode(), signed_payload, hashlib.sha256).hexdigest()

    matched = any(hmac.compare_digest(expected, candidate) for candidate in v1_sigs)
    if not matched:
        return (False, None, "")

    # Parse body for normalized event_id / event_type.
    event_id: str | None = None
    event_type: str = ""
    try:
        payload = json.loads(body.decode("utf-8") or "{}")
        if isinstance(payload, dict):
            raw_id = payload.get("id")
            event_id = str(raw_id) if raw_id is not None else None
            raw_type = payload.get("type")
            event_type = str(raw_type) if raw_type is not None else ""
    except (UnicodeDecodeError, json.JSONDecodeError):
        # Signature was valid but body is not JSON — the upstream will likely
        # 400 downstream. Keep event_id None / event_type empty.
        pass

    return (True, event_id, event_type)


_VERIFIER_REGISTRY: dict[str, Verifier] = {
    "stripe": _verify_stripe,
}


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

def _resolve_pipelines(
    routing: dict[str, Any], provider: str, event_type: str,
) -> list[str]:
    routes = routing.get("routes", {}) or {}
    key = f"external.{provider}.{event_type}"
    if key in routes:
        return list(routes[key])
    defaults = routing.get("defaults", {}) or {}
    return list(defaults.get("unknown_event", []) or [])


# --------------------------------------------------------------------------- #
# Endpoint
# --------------------------------------------------------------------------- #

@router.post("/webhook/{provider}")
async def external_webhook(provider: str, request: Request) -> dict[str, Any]:
    verifier = _VERIFIER_REGISTRY.get(provider)
    if verifier is None:
        raise HTTPException(status_code=404, detail="unknown provider")

    raw_body = await request.body()
    headers = dict(request.headers)
    secret = os.environ.get(f"{provider.upper()}_WEBHOOK_SECRET", "")

    is_valid, event_id, event_type = verifier(raw_body, headers, secret)
    if not is_valid:
        raise HTTPException(status_code=401, detail="invalid signature")

    if not event_id:
        raise HTTPException(status_code=400, detail="missing event id")

    store = request.app.state.store
    payload_json = raw_body.decode("utf-8", errors="replace") or "{}"
    # Ensure the persisted column is valid JSON (the table CHECKs it).
    try:
        json.loads(payload_json)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="invalid json body")

    is_duplicate = False
    async with write_tx(store.orchestration, store.orchestration_lock) as conn:
        await conn.execute(
            "INSERT OR IGNORE INTO external_events "
            "(provider, event_id, event_type, resource_id, payload) "
            "VALUES (?, ?, ?, ?, ?)",
            (provider, event_id, event_type, None, payload_json),
        )
        cur = await conn.execute(
            "SELECT id, processed FROM external_events "
            "WHERE provider = ? AND event_id = ?",
            (provider, event_id),
        )
        row = await cur.fetchone()
        await cur.close()
        if row is None:  # pragma: no cover
            raise HTTPException(
                status_code=500, detail="external_events row vanished",
            )
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

    routing = _get_routing()
    pipeline_names = _resolve_pipelines(routing, provider, event_type)

    # Build the trigger payload from the parsed JSON body so pipelines can read
    # provider-shaped fields directly.
    try:
        envelope = json.loads(payload_json)
    except json.JSONDecodeError:  # pragma: no cover — guarded above
        envelope = {}

    run_ids: list[int] = []
    for name in pipeline_names:
        try:
            rid = await execute_pipeline(
                name,
                trigger_source=f"external.{provider}.{event_type}",
                trigger_payload=envelope if isinstance(envelope, dict) else {"raw": envelope},
                store=store,
                employee_id=None,
            )
            run_ids.append(rid)
        except (FileNotFoundError, PipelineLoadError) as exc:
            logger.warning(
                "external_webhook.pipeline_skipped",
                extra={
                    "pipeline": name,
                    "provider": provider,
                    "event_type": event_type,
                    "event_id": event_id,
                    "error": str(exc),
                },
            )

    return {"status": "ok", "event_id": event_id, "run_ids": run_ids}
