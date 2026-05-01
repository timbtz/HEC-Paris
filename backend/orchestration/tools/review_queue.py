"""Review queue — entries that fail confidence, totals, or invariants land here.

Source: RealMetaPRD §7.4; Phase 2 plan task 33. Companion table created in
migration 0008_review_queue.

The pipeline routes here when `conditions.gating:needs_review` (low compound
confidence) or `conditions.documents:totals_mismatch` (PDF totals don't tie
out) fires. `entry_id` is nullable: extraction-stage failures may have no
journal entry yet.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from ..context import FingentContext
from ..event_bus import publish_event_dashboard
from ..store.writes import write_tx


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _classify(ctx: FingentContext) -> tuple[str, float | None]:
    """Pick a `kind` + extract a confidence to log.

    Order of precedence:
      1. `gate-confidence` failed       -> 'low_confidence'
      2. `validate` (totals) failed     -> 'totals_mismatch'
      3. fallback                        -> 'manual'
    """
    gate = ctx.get("gate-confidence") or {}
    if gate and not gate.get("ok", True):
        c = gate.get("computed_confidence")
        return "low_confidence", float(c) if isinstance(c, (int, float)) else None

    validate = ctx.get("validate") or {}
    if validate and not validate.get("ok", True):
        return "totals_mismatch", None

    return "manual", None


def _build_reason(ctx: FingentContext, kind: str) -> str:
    if kind == "low_confidence":
        gate = ctx.get("gate-confidence") or {}
        return (
            f"compound_confidence={gate.get('computed_confidence')!r} "
            f"floor={gate.get('floor')!r}"
        )
    if kind == "totals_mismatch":
        validate = ctx.get("validate") or {}
        return f"totals_mismatch: {validate.get('failures') or validate}"
    return "queued for manual review"


async def enqueue(ctx: FingentContext) -> dict[str, Any]:
    posted = ctx.get("post-entry") or {}
    entry_id = posted.get("entry_id")  # may be None

    kind, confidence = _classify(ctx)
    reason = _build_reason(ctx, kind)

    async with write_tx(ctx.store.accounting, ctx.store.accounting_lock) as conn:
        cur = await conn.execute(
            "INSERT INTO review_queue (entry_id, kind, confidence, reason) "
            "VALUES (?, ?, ?, ?)",
            (entry_id, kind, confidence, reason),
        )
        review_id = cur.lastrowid
        await cur.close()

    assert review_id is not None
    review_id = int(review_id)

    await publish_event_dashboard({
        "event_type": "review.enqueued",
        "data": {
            "review_id": review_id,
            "entry_id": entry_id,
            "kind": kind,
            "confidence": confidence,
            "reason": reason,
        },
        "ts": _iso_now(),
    })

    return {"review_id": review_id, "kind": kind}
