"""Cross-run node cache.

Source: RealMetaPRD §6.4 line 525 (key shape), §7.5 lines 1092-1104
(`node_cache` schema), PRD1_VALIDATION_BRIEFING A2 (float canonicalization).

Cache key formula:
    sha256(f"{node_id}|{CODE_VERSION}|{canonical_json(input)}")

`json.dumps(sort_keys=True)` does NOT canonicalize floats across
platforms (`1.0` vs `1` representation drift); we use `repr(float(x))`
through the `default=` hook to defeat that.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import math
from datetime import datetime, timezone
from typing import Any

import aiosqlite

from .store.writes import write_tx


CODE_VERSION = "v1"


def _canonical_default(obj: Any) -> str:
    """Custom encoder for objects json.dumps can't natively handle.

    We canonicalize floats here; everything else falls back to the
    default `TypeError`.
    """
    if isinstance(obj, float):
        if not math.isfinite(obj):
            raise ValueError("non-finite float not allowed in cache key")
        return repr(obj)
    raise TypeError(f"unsupported type for cache key: {type(obj).__name__}")


def _normalize(value: Any) -> Any:
    """Walk the tree and pre-canonicalize floats.

    `json.dumps` only invokes `default=` for objects it doesn't know how to
    encode, and floats fall in the "knows how" set — so we replace floats
    with their `repr()` strings before serializing. NaN/Inf raise.
    """
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("non-finite float not allowed in cache key")
        return repr(value)
    if isinstance(value, dict):
        return {k: _normalize(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_normalize(v) for v in value]
    return value


def cache_key(node_id: str, canonical_input: Any) -> str:
    """Compute the deterministic cache key for a node-input pair."""
    payload = json.dumps(
        _normalize(canonical_input),
        sort_keys=True,
        separators=(",", ":"),
        default=_canonical_default,
        allow_nan=False,
    )
    raw = f"{node_id}|{CODE_VERSION}|{payload}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


async def lookup(
    orchestration_db: aiosqlite.Connection,
    key: str,
) -> dict[str, Any] | None:
    """Return the cached output dict, or None on miss. Read-only — no lock."""
    cur = await orchestration_db.execute(
        "SELECT output_json FROM node_cache WHERE cache_key = ?",
        (key,),
    )
    row = await cur.fetchone()
    await cur.close()
    if row is None:
        return None
    return json.loads(row[0])


async def store(
    orchestration_db: aiosqlite.Connection,
    lock: asyncio.Lock,
    *,
    key: str,
    node_id: str,
    pipeline_name: str,
    input_json: Any,
    output_json: Any,
) -> None:
    """Insert a cache row. Idempotent: a second store of the same key is a no-op."""
    async with write_tx(orchestration_db, lock) as conn:
        await conn.execute(
            "INSERT OR IGNORE INTO node_cache "
            "(cache_key, node_id, pipeline_name, code_version, "
            " input_json, output_json) VALUES (?, ?, ?, ?, ?, ?)",
            (
                key,
                node_id,
                pipeline_name,
                CODE_VERSION,
                json.dumps(_normalize(input_json), sort_keys=True, separators=(",", ":")),
                json.dumps(_normalize(output_json), sort_keys=True, separators=(",", ":")),
            ),
        )


async def record_hit(
    orchestration_db: aiosqlite.Connection,
    lock: asyncio.Lock,
    key: str,
) -> None:
    """Bump hit_count + last_hit_at on a cache row."""
    async with write_tx(orchestration_db, lock) as conn:
        await conn.execute(
            "UPDATE node_cache SET hit_count = hit_count + 1, last_hit_at = ? "
            "WHERE cache_key = ?",
            (datetime.now(timezone.utc).isoformat(), key),
        )
