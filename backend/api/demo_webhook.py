"""Demo trigger that fires a seeded Swan transaction through the live pipeline.

Used by the frontend "Simulate Swan event" button. Picks the next unprocessed
row from `accounting.swan_transactions` (or one specified by id), dispatches
the same pipelines that `/swan/webhook` would, and returns enough metadata
for the UI to show what just fired.

Bypasses the `x-swan-secret` check because it is a server-internal trigger.
Sets `FINGENT_SWAN_LOCAL_REPLAY=1` so `tools.swan_query.fetch_transaction`
reads from the local seed instead of calling the Swan API.
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from ..orchestration.executor import execute_pipeline
from ..orchestration.store.writes import write_tx
from ..orchestration.yaml_loader import PipelineLoadError
from .swan_webhook import _get_routing, _resolve_employee_id, _resolve_pipelines

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/demo")


_TX_TYPE_TO_EVENT_TYPE = {
    "CardOutDebit":           "Transaction.Booked",
    "SepaCreditTransferIn":   "Transaction.Booked",
    "SepaCreditTransferOut":  "Transaction.Booked",
    "Fees":                   "Transaction.Booked",
    "InternalCreditTransfer": "Transaction.Booked",
}


class SimulateRequest(BaseModel):
    tx_id: str | None = None


async def _pick_seed_row(store, tx_id: str | None) -> dict[str, Any] | None:
    """Return the next seeded transaction to fire.

    If `tx_id` is given, fetch that one directly. Otherwise pick the oldest
    seeded row whose `swan_event_id` has not yet been recorded in
    `orchestration.external_events` — i.e. the next un-fired transaction.
    """
    if tx_id is not None:
        cur = await store.accounting.execute(
            "SELECT id, swan_event_id, execution_date, type, side, "
            "amount_cents, currency, counterparty_label "
            "FROM swan_transactions WHERE id = ?",
            (tx_id,),
        )
        row = await cur.fetchone()
        await cur.close()
        if row is None:
            return None
        return _row_to_dict(row)

    cur = await store.orchestration.execute(
        "SELECT event_id FROM external_events WHERE provider = 'swan'"
    )
    rows = await cur.fetchall()
    await cur.close()
    fired = {str(r[0]) for r in rows}

    cur = await store.accounting.execute(
        "SELECT id, swan_event_id, execution_date, type, side, "
        "amount_cents, currency, counterparty_label "
        "FROM swan_transactions ORDER BY execution_date ASC, id ASC"
    )
    rows = await cur.fetchall()
    await cur.close()
    for r in rows:
        if str(r[1]) not in fired:
            return _row_to_dict(r)
    return None


def _row_to_dict(r: Any) -> dict[str, Any]:
    return {
        "id":                  r[0],
        "swan_event_id":       r[1],
        "execution_date":      r[2],
        "type":                r[3],
        "side":                r[4],
        "amount_cents":        int(r[5]),
        "currency":            r[6],
        "counterparty_label":  r[7],
    }


_SCENARIOS: tuple[dict[str, Any], ...] = (
    {
        "key":         "customer_payment",
        "title":       "Customer payment received",
        "description": "Inbound SEPA — books to revenue + receivable",
        "where":       "type = 'SepaCreditTransferIn'",
        "args":        (),
        "order":       "amount_cents DESC, execution_date DESC",
    },
    {
        "key":         "ai_invoice_anthropic",
        "title":       "Anthropic invoice paid",
        "description": "Outbound SEPA to AI vendor — AI-credit cost line",
        "where":       "type = 'SepaCreditTransferOut' AND counterparty_label = ?",
        "args":        ("Anthropic",),
        "order":       "execution_date DESC",
    },
    {
        "key":         "ai_card_openai",
        "title":       "OpenAI card charge",
        "description": "Card debit — software subscription / AI usage",
        "where":       "type = 'CardOutDebit' AND counterparty_label = ?",
        "args":        ("OpenAI",),
        "order":       "execution_date DESC",
    },
    {
        "key":         "travel_sncf",
        "title":       "SNCF train ticket",
        "description": "Card debit — employee travel expense",
        "where":       "type = 'CardOutDebit' AND counterparty_label = ?",
        "args":        ("SNCF",),
        "order":       "execution_date DESC",
    },
    {
        "key":         "supplier_notion",
        "title":       "Notion subscription",
        "description": "Outbound SEPA — recurring SaaS supplier payment",
        "where":       "type = 'SepaCreditTransferOut' AND counterparty_label = ?",
        "args":        ("Notion",),
        "order":       "execution_date DESC",
    },
    {
        "key":         "bank_fee",
        "title":       "Bank fee",
        "description": "Swan fee — bank charges expense",
        "where":       "type = 'Fees'",
        "args":        (),
        "order":       "execution_date DESC",
    },
)


async def _fired_event_ids(store) -> set[str]:
    cur = await store.orchestration.execute(
        "SELECT event_id FROM external_events WHERE provider = 'swan'"
    )
    rows = await cur.fetchall()
    await cur.close()
    return {str(r[0]) for r in rows}


@router.get("/swan/scenarios")
async def list_scenarios(request: Request) -> dict[str, Any]:
    """Curated demo scenarios. Each carries the next un-fired matching row.

    The UI uses this to render a labelled dropdown so the presenter can pick
    a meaningful event ("customer payment €4,500") rather than firing
    chronologically through random card debits.
    """
    store = request.app.state.store
    fired = await _fired_event_ids(store)

    out: list[dict[str, Any]] = []
    for scen in _SCENARIOS:
        cur = await store.accounting.execute(
            f"SELECT id, swan_event_id, execution_date, type, side, "
            f"amount_cents, currency, counterparty_label "
            f"FROM swan_transactions WHERE {scen['where']} "
            f"ORDER BY {scen['order']}",
            scen["args"],
        )
        rows = await cur.fetchall()
        await cur.close()
        next_row: dict[str, Any] | None = None
        remaining = 0
        for r in rows:
            if str(r[1]) in fired:
                continue
            remaining += 1
            if next_row is None:
                next_row = _row_to_dict(r)
        out.append({
            "key":         scen["key"],
            "title":       scen["title"],
            "description": scen["description"],
            "next":        next_row,
            "remaining":   remaining,
            "total":       len(rows),
        })
    return {"scenarios": out}


@router.post("/swan/simulate")
async def simulate_swan_event(req: SimulateRequest, request: Request) -> dict[str, Any]:
    store = request.app.state.store

    row = await _pick_seed_row(store, req.tx_id)
    if row is None:
        if req.tx_id is not None:
            raise HTTPException(status_code=404, detail=f"unknown swan tx id: {req.tx_id}")
        raise HTTPException(
            status_code=409,
            detail=(
                "all seeded swan transactions have been fired. "
                "Reset with: DELETE FROM external_events WHERE provider='swan'."
            ),
        )

    # Make sure the pipeline reads from local seed, not Swan API.
    os.environ.setdefault("FINGENT_SWAN_LOCAL_REPLAY", "1")

    event_type = _TX_TYPE_TO_EVENT_TYPE.get(row["type"], "Transaction.Booked")
    envelope = {
        "eventType":  event_type,
        "eventId":    row["swan_event_id"],
        "eventDate":  row["execution_date"],
        "projectId":  "demo-simulate",
        "resourceId": row["id"],
    }
    payload_json = json.dumps(envelope, separators=(",", ":"))

    # Same dedup contract as `/swan/webhook`: INSERT OR IGNORE then mark processed.
    is_duplicate = False
    async with write_tx(store.orchestration, store.orchestration_lock) as conn:
        await conn.execute(
            "INSERT OR IGNORE INTO external_events "
            "(provider, event_id, event_type, resource_id, payload) "
            "VALUES (?, ?, ?, ?, ?)",
            ("swan", row["swan_event_id"], event_type, row["id"], payload_json),
        )
        cur = await conn.execute(
            "SELECT id, processed FROM external_events "
            "WHERE provider = ? AND event_id = ?",
            ("swan", row["swan_event_id"]),
        )
        ev_row = await cur.fetchone()
        await cur.close()
        if ev_row is None:  # pragma: no cover
            raise HTTPException(status_code=500, detail="external_events row vanished")
        ev_id, processed = int(ev_row[0]), int(ev_row[1])
        if processed == 1:
            is_duplicate = True
        else:
            await conn.execute(
                "UPDATE external_events SET processed = 1 WHERE id = ?",
                (ev_id,),
            )

    if is_duplicate:
        return {
            "status":         "duplicate",
            "event_id":       row["swan_event_id"],
            "swan_id":        row["id"],
            "amount_cents":   row["amount_cents"],
            "currency":       row["currency"],
            "side":           row["side"],
            "label":          row["counterparty_label"],
            "run_ids":        [],
        }

    employee_id = await _resolve_employee_id(store, row["id"])
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
                "demo_simulate.pipeline_skipped",
                extra={"pipeline": name, "event_type": event_type, "error": str(exc)},
            )

    return {
        "status":         "ok",
        "event_id":       row["swan_event_id"],
        "swan_id":        row["id"],
        "event_type":     event_type,
        "amount_cents":   row["amount_cents"],
        "currency":       row["currency"],
        "side":           row["side"],
        "label":          row["counterparty_label"],
        "run_ids":        run_ids,
    }
