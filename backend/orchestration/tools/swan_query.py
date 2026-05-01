"""Swan GraphQL re-query tool — fetches the canonical transaction/account state.

Source: RealMetaPRD §7.4 (re-query pattern); 05_swan_integration.md:206-207.

The webhook handler stores the raw envelope and triggers a pipeline; the
pipeline immediately re-queries Swan via these tools to read canonical
state (webhooks are a notification, not a payload). Re-query results land
in `accounting.swan_transactions` via INSERT OR REPLACE so downstream
nodes see the freshest version.
"""
from __future__ import annotations

import json
import os
from typing import Any

from ..context import FingentContext
from ..store.writes import write_tx
from ..swan.graphql import SwanGraphQLClient
from ..swan.oauth import SwanOAuthClient


# Module-level lazy singleton. Tests monkey-patch `_get_client` to swap
# in a fake; production code calls it once per pipeline node.
_client: SwanGraphQLClient | None = None


def _get_client() -> SwanGraphQLClient:
    """Build (or return cached) `SwanGraphQLClient` from env vars.

    GOTCHA: Construct lazily — env may not be loaded at import time. Tests
    bypass this entirely by monkey-patching the symbol.
    """
    global _client
    if _client is not None:
        return _client

    oauth = SwanOAuthClient(
        client_id=os.environ["SWAN_CLIENT_ID"],
        client_secret=os.environ["SWAN_CLIENT_SECRET"],
        oauth_url=os.environ["SWAN_OAUTH_URL"],
    )
    _client = SwanGraphQLClient(
        graphql_url=os.environ["SWAN_GRAPHQL_URL"],
        oauth=oauth,
    )
    return _client


def _coerce_amount_cents(amount: Any) -> int:
    """Defensive: parse Swan's `{value, currency}` amount into integer cents.

    Swan returns `value` as a decimal-string (e.g. "12.50"). We multiply by
    100 and round, never trusting float math directly. Missing amount → 0.
    """
    if not isinstance(amount, dict):
        return 0
    raw_value = amount.get("value")
    if raw_value is None:
        return 0
    try:
        return int(round(float(raw_value) * 100))
    except (TypeError, ValueError):
        return 0


async def _persist_transaction(
    ctx: FingentContext,
    tx: dict[str, Any],
    *,
    swan_event_id: str,
) -> None:
    """INSERT OR REPLACE the Swan transaction into accounting.swan_transactions.

    NOT-NULL columns get sensible defaults so partial test fixtures don't
    blow up on insert. `raw` is always the JSON-serialised full payload.
    """
    amount = tx.get("amount") or {}
    amount_cents = _coerce_amount_cents(amount)
    currency = amount.get("currency") if isinstance(amount, dict) else None

    # Counterparty label: explicit `counterparty_label` wins, then fall back
    # to `counterparty.name` (canonical query shape), then to `counterparty`
    # if Swan returned a bare string.
    raw_cp = tx.get("counterparty")
    if "counterparty_label" in tx and tx.get("counterparty_label"):
        cp_label: Any = tx.get("counterparty_label")
    elif isinstance(raw_cp, dict):
        cp_label = raw_cp.get("name")
    else:
        cp_label = raw_cp if isinstance(raw_cp, str) else None

    row = (
        tx.get("id"),
        swan_event_id,
        tx.get("side") or "Debit",
        tx.get("type") or "Unknown",
        tx.get("status") or "Booked",
        amount_cents,
        currency or "EUR",
        cp_label,
        tx.get("paymentReference"),
        None,  # external_reference — not surfaced by the canonical query
        tx.get("executionDate") or "1970-01-01",
        tx.get("bookedBalanceAfter"),
        json.dumps(tx, separators=(",", ":"), default=str),
    )

    async with write_tx(ctx.store.accounting, ctx.store.accounting_lock) as conn:
        await conn.execute(
            "INSERT OR REPLACE INTO swan_transactions "
            "(id, swan_event_id, side, type, status, amount_cents, currency, "
            " counterparty_label, payment_reference, external_reference, "
            " execution_date, booked_balance_after, raw) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            row,
        )


async def _read_local_transaction(
    ctx: FingentContext, tx_id: str
) -> dict[str, Any] | None:
    """Read a previously-persisted transaction from `accounting.swan_transactions`.

    The demo seed (migration 0006) already populates 200 rows; the replay
    script (`backend/scripts/replay_swan_seed.py`) leans on this so it can
    drive the pipeline locally without Swan API credentials.
    """
    cur = await ctx.store.accounting.execute(
        "SELECT raw FROM swan_transactions WHERE id = ?", (tx_id,)
    )
    row = await cur.fetchone()
    await cur.close()
    if row is None:
        return None
    raw = row[0]
    if not raw:
        return None
    parsed = json.loads(raw)
    return parsed if isinstance(parsed, dict) else None


async def fetch_transaction(ctx: FingentContext) -> dict[str, Any]:
    """Re-query Swan for the canonical transaction state.

    Reads `tx_id` from the trigger payload (`resourceId` is Swan's webhook
    field name; `id` is the local-test fallback). Persists into
    `swan_transactions` and returns the dict augmented with `__local_id`
    so downstream nodes can correlate without re-parsing the Swan payload.

    Demo / replay mode: if `SWAN_CLIENT_ID` is unset, fall back to the
    locally-persisted row in `accounting.swan_transactions` (populated by
    the seed migration or a prior webhook). This keeps the pipeline
    end-to-end runnable for the hackathon demo without Swan creds.
    """
    payload = ctx.trigger_payload or {}
    tx_id = payload.get("resourceId") or payload.get("id")
    if not tx_id:
        raise ValueError(
            "fetch_transaction: trigger_payload missing 'resourceId' / 'id'"
        )

    swan_event_id = payload.get("eventId") or tx_id

    # Demo / replay mode: opt in via `FINGENT_SWAN_LOCAL_REPLAY=1`. Reads
    # from the locally-persisted row (seed migration 0006) and skips the
    # network. Production / CI never sets this, preserving the canonical
    # re-query behavior.
    if os.environ.get("FINGENT_SWAN_LOCAL_REPLAY") == "1":
        local = await _read_local_transaction(ctx, tx_id)
        if local is not None:
            out = dict(local)
            out["__local_id"] = tx_id
            return out
        raise LookupError(
            f"fetch_transaction: FINGENT_SWAN_LOCAL_REPLAY=1 but no local row for {tx_id}"
        )

    client = _get_client()
    tx = await client.fetch_transaction(tx_id)

    await _persist_transaction(ctx, tx, swan_event_id=swan_event_id)

    out = dict(tx)
    out["__local_id"] = tx_id
    return out


async def fetch_account(ctx: FingentContext) -> dict[str, Any]:
    """Re-query Swan for the canonical account state.

    Prefers `fetch-transaction.account.id` (chained pipeline) and falls
    back to the trigger payload's `account_id` (manual / test triggers).
    """
    chained = ctx.get("fetch-transaction") or {}
    account_block = chained.get("account") if isinstance(chained, dict) else None
    account_id: str | None = None
    if isinstance(account_block, dict):
        account_id = account_block.get("id")
    if not account_id:
        account_id = (ctx.trigger_payload or {}).get("account_id")
    if not account_id:
        raise ValueError(
            "fetch_account: no account id in fetch-transaction output or trigger_payload"
        )

    client = _get_client()
    return await client.fetch_account(account_id)
