"""Replay the seeded Swan transactions through `/swan/webhook`.

Usage:
    uv run python -m backend.scripts.replay_swan_seed
    uv run python -m backend.scripts.replay_swan_seed --host localhost --port 8000

Iterates `accounting.swan_transactions` ASC by `(execution_date, id)` and
POSTs a synthetic Swan webhook envelope per row. The pipeline trigger
runs end-to-end against the local seed (no Swan API creds needed —
`tools.swan_query.fetch_transaction` falls back to the local row when
`SWAN_CLIENT_ID` is unset).

Idempotency: `external_events.UNIQUE(provider, event_id)` (orchestration
schema) makes a re-run a no-op — the second run reports every row as
``duplicate`` and zero new `journal_entries` are created.
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import time
from pathlib import Path
from typing import Any

import httpx

from ..orchestration.store.bootstrap import open_dbs

logger = logging.getLogger(__name__)


_TX_TYPE_TO_EVENT_TYPE = {
    "CardOutDebit":           "Transaction.Booked",
    "SepaCreditTransferIn":   "Transaction.Booked",
    "SepaCreditTransferOut":  "Transaction.Booked",
    "Fees":                   "Transaction.Booked",
    "InternalCreditTransfer": "Transaction.Booked",
}


async def _list_seeded_transactions(store) -> list[dict[str, Any]]:
    cur = await store.accounting.execute(
        "SELECT id, swan_event_id, execution_date, type "
        "FROM swan_transactions "
        "ORDER BY execution_date ASC, id ASC"
    )
    rows = await cur.fetchall()
    await cur.close()
    return [
        {
            "id": r[0],
            "swan_event_id": r[1],
            "execution_date": r[2],
            "type": r[3],
        }
        for r in rows
    ]


async def _is_already_posted(store, tx_id: str) -> bool:
    """Has any journal_line already referenced this swan_transaction_id?

    Defensive secondary dedup — `swan_webhook` already drops duplicates
    via `external_events.UNIQUE(provider, event_id)`, but this guards the
    edge case where a swan_event_id was rotated between runs.
    """
    cur = await store.accounting.execute(
        "SELECT 1 FROM journal_lines WHERE swan_transaction_id = ? LIMIT 1",
        (tx_id,),
    )
    row = await cur.fetchone()
    await cur.close()
    return row is not None


def _build_envelope(row: dict[str, Any]) -> dict[str, Any]:
    """Build the synthetic webhook envelope.

    Mirrors `swan_webhook._REQUIRED_FIELDS` shape: eventType, eventId,
    eventDate, projectId, resourceId. The pipeline's `fetch_transaction`
    looks up `resourceId` against the local DB when `SWAN_CLIENT_ID` is
    unset.
    """
    event_type = _TX_TYPE_TO_EVENT_TYPE.get(row["type"], "Transaction.Booked")
    return {
        "eventType":  event_type,
        "eventId":    row["swan_event_id"],
        "eventDate":  row["execution_date"],
        "projectId":  "demo-replay",
        "resourceId": row["id"],
    }


async def _post_one(
    client: httpx.AsyncClient,
    url: str,
    secret: str,
    envelope: dict[str, Any],
) -> tuple[str, int]:
    """Returns `(outcome, status_code)`."""
    try:
        resp = await client.post(
            url,
            json=envelope,
            headers={"x-swan-secret": secret},
            timeout=10.0,
        )
    except httpx.HTTPError as exc:
        logger.warning("replay.post_failed eventId=%s err=%s", envelope["eventId"], exc)
        return ("failed", 0)
    if resp.status_code != 200:
        return ("failed", resp.status_code)
    body = resp.json() if resp.content else {}
    if body.get("status") == "duplicate":
        return ("skipped", 200)
    return ("posted", 200)


async def main(host: str, port: int, data_dir: Path) -> dict[str, int]:
    url = f"http://{host}:{port}/swan/webhook"
    secret = os.environ.get("SWAN_WEBHOOK_SECRET", "")
    # Tell `tools.swan_query.fetch_transaction` to skip the Swan API and
    # read from the locally-persisted seed instead.
    os.environ.setdefault("AGNES_SWAN_LOCAL_REPLAY", "1")

    store = await open_dbs(data_dir, run_migrations=False)
    try:
        rows = await _list_seeded_transactions(store)
        stats = {"posted": 0, "skipped": 0, "failed": 0, "total": len(rows)}

        async with httpx.AsyncClient() as client:
            for row in rows:
                t0 = time.monotonic()

                if await _is_already_posted(store, row["id"]):
                    stats["skipped"] += 1
                    elapsed_ms = int((time.monotonic() - t0) * 1000)
                    logger.info(
                        "replay.skipped tx_id=%s elapsed_ms=%d (already in journal_lines)",
                        row["id"], elapsed_ms,
                    )
                    continue

                envelope = _build_envelope(row)
                outcome, status = await _post_one(client, url, secret, envelope)
                stats[outcome] += 1
                elapsed_ms = int((time.monotonic() - t0) * 1000)
                logger.info(
                    "replay.%s tx_id=%s status=%d elapsed_ms=%d",
                    outcome, row["id"], status, elapsed_ms,
                )
    finally:
        await store.close()

    return stats


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--host", default=os.environ.get("AGNES_HOST", "localhost"))
    p.add_argument("--port", type=int, default=int(os.environ.get("AGNES_PORT", "8000")))
    p.add_argument(
        "--data-dir",
        type=Path,
        default=Path(os.environ.get("AGNES_DATA_DIR", "./data")).resolve(),
    )
    return p.parse_args()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    args = _parse_args()
    summary = asyncio.run(main(args.host, args.port, args.data_dir))
    logger.info(
        "replay.complete posted=%(posted)d skipped=%(skipped)d "
        "failed=%(failed)d total=%(total)d",
        summary,
    )
