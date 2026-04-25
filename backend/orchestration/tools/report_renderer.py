"""Persist a generated report to disk + `period_reports` + dashboard bus.

The pipeline upstream nodes (period_aggregator, vat_calculator, anomaly
agent) build a serializable summary; this tool is the single side-effect
node that writes it out. Mirrors `gl_poster.post`'s `write_tx +
publish_event_dashboard` sequence.

Confidence floor for `final` status: 0.75 (Phase 3 reporting threshold,
distinct from the per-transaction GL classification floor of 0.50).
Below 0.75 → status is `flagged` and downstream `review_queue.enqueue`
picks it up.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..context import AgnesContext
from ..event_bus import publish_event_dashboard
from ..store.writes import write_tx

logger = logging.getLogger(__name__)

_REPORT_CONFIDENCE_FLOOR = 0.75
# TODO: move to confidence_thresholds table (scope='report:*').


async def render(ctx: AgnesContext) -> dict[str, Any]:
    """Serialize the report payload, write to disk + DB, emit dashboard event.

    Reads:
      - `report_type` from `ctx.trigger_payload` (e.g. 'period_close',
        'vat_return', 'year_end_close')
      - the upstream summary from `ctx.get('summarize-period')` (period
        close & year-end pipelines) OR `ctx.get('compute-vat')` (VAT)

    Writes:
      - JSON blob to `data/blobs/reports/<period_code>/<report_type>.json`
      - Markdown summary alongside the JSON for human review
      - `period_reports` row (status=draft|flagged based on confidence)
      - `report.rendered` event on the dashboard bus
    """
    payload = ctx.trigger_payload or {}
    report_type = payload.get("report_type") or ctx.pipeline_name
    if not report_type:
        raise ValueError("report_renderer: cannot determine report_type")

    summary = ctx.get("summarize-period") or ctx.get("compute-vat") or {}
    if not summary:
        raise RuntimeError("report_renderer: no upstream summary to render")

    period_code = summary.get("period_code") or payload.get("period_code") or "unknown"
    confidence = float(summary.get("confidence", 1.0))
    status = "draft" if confidence >= _REPORT_CONFIDENCE_FLOOR else "flagged"

    blob_dir = ctx.store.data_dir / "blobs" / "reports" / period_code
    blob_dir.mkdir(parents=True, exist_ok=True)
    json_path = blob_dir / f"{report_type}.json"
    md_path = blob_dir / f"{report_type}.md"

    json_payload = json.dumps(summary, indent=2, default=str)
    json_path.write_text(json_payload, encoding="utf-8")
    md_path.write_text(_render_markdown(report_type, period_code, summary), encoding="utf-8")

    async with write_tx(ctx.store.accounting, ctx.store.accounting_lock) as conn:
        cur = await conn.execute(
            "INSERT INTO period_reports "
            "(period_code, report_type, status, confidence, source_run_id, "
            " blob_path, payload_json) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                period_code,
                report_type,
                status,
                confidence,
                ctx.run_id,
                str(json_path),
                json_payload,
            ),
        )
        report_id = int(cur.lastrowid)
        await cur.close()

    await publish_event_dashboard({
        "event_type": "report.rendered",
        "ts": datetime.now(timezone.utc).isoformat(),
        "data": {
            "report_id": report_id,
            "period_code": period_code,
            "report_type": report_type,
            "status": status,
            "confidence": confidence,
            "blob_path": str(json_path),
            "run_id": ctx.run_id,
        },
    })

    logger.info(
        "report_renderer.rendered period=%s type=%s status=%s confidence=%.3f",
        period_code, report_type, status, confidence,
    )

    return {
        "report_id": report_id,
        "period_code": period_code,
        "report_type": report_type,
        "status": status,
        "confidence": confidence,
        "blob_path": str(json_path),
    }


def _render_markdown(report_type: str, period_code: str, summary: dict[str, Any]) -> str:
    """Render a small human-readable summary for the dashboard / drawer."""
    lines = [
        f"# {report_type}  —  period {period_code}",
        "",
        f"Confidence: {summary.get('confidence', 1.0):.3f}",
        "",
    ]
    if "trial_balance_totals" in summary:
        tot = summary["trial_balance_totals"]
        lines.extend([
            "## Trial balance totals",
            "",
            f"- Total debit:  {tot.get('total_debit_cents', 0)} cents",
            f"- Total credit: {tot.get('total_credit_cents', 0)} cents",
            f"- Balanced:     {tot.get('balanced', False)}",
            "",
        ])
    if "totals" in summary:
        tot = summary["totals"]
        lines.append("## Totals")
        for k, v in tot.items():
            lines.append(f"- {k}: {v}")
        lines.append("")
    if summary.get("anomalies"):
        lines.append("## Anomalies")
        for a in summary["anomalies"]:
            lines.append(
                f"- [{a.get('kind', '?')}] "
                f"conf={a.get('confidence', '?')}: "
                f"{a.get('evidence', a.get('description', ''))}"
            )
        lines.append("")
    return "\n".join(lines)
