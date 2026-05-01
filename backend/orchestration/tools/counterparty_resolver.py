"""Four-stage counterparty resolution cascade with cache writeback.

Source: RealMetaPRD §7.4 (the cascade); 04_AGENT_PATTERNS.md:214-305 (closed-list
resolver shape + stage names).

Stages, in order:
  1. IBAN exact (deterministic; confidence 1.0).
  2. Virtual IBAN (inbound only; deterministic; confidence 1.0).
  3a. Card merchant id (deterministic; confidence 0.95).
  3b. MCC + fuzzy on merchant name (rapidfuzz; confidence ≤ 0.85).
  4. Pure fuzzy on the counterparty label (rapidfuzz; confidence ≤ 0.85).

A miss returns `counterparty_id=None` so the caller's `unresolved` gate
routes to the AI fallback (`agents/counterparty_classifier.py`).

Cache writeback (stages 1–4 hit): we INSERT OR IGNORE into
`counterparty_identifiers` so the next deterministic lookup short-circuits.
The unique constraint on (identifier_type, identifier) makes the writeback
idempotent.
"""
from __future__ import annotations

from typing import Any

from rapidfuzz import fuzz

from ..context import FingentContext
from ..store.writes import write_tx


# Fuzzy threshold; below this we report a miss and route to the AI fallback.
_FUZZY_THRESHOLD = 85
# Hard ceiling on fuzzy confidence — a token-set ratio of 100 still represents
# a heuristic match, not a deterministic one. Bumps to 0.85 max.
_FUZZY_CONFIDENCE_CAP = 0.85


def _extract_ibans(tx: dict[str, Any]) -> tuple[str | None, bool]:
    """Return (iban, inbound) given a Swan transaction dict.

    Outbound (`Debit`) → creditor IBAN; inbound (`Credit`) → debtor IBAN.
    Defensive on shape: accepts `counterparty.iban`, `creditor.iban`, or
    `debtor.iban` depending on what Swan / the test fixture provided.
    """
    side = tx.get("side")
    inbound = side == "Credit"

    cp = tx.get("counterparty")
    if isinstance(cp, dict) and cp.get("iban"):
        return cp["iban"], inbound

    if inbound:
        debtor = tx.get("debtor") or {}
        if isinstance(debtor, dict) and debtor.get("iban"):
            return debtor["iban"], inbound
    else:
        creditor = tx.get("creditor") or {}
        if isinstance(creditor, dict) and creditor.get("iban"):
            return creditor["iban"], inbound

    return None, inbound


async def _lookup_identifier(
    ctx: FingentContext, identifier_type: str, identifier: str
) -> int | None:
    """Return `counterparty_id` for an exact (type, identifier) match, or None."""
    cur = await ctx.store.accounting.execute(
        "SELECT counterparty_id FROM counterparty_identifiers "
        "WHERE identifier_type = ? AND identifier = ?",
        (identifier_type, identifier),
    )
    row = await cur.fetchone()
    await cur.close()
    return int(row[0]) if row else None


async def _fetch_counterparty(
    ctx: FingentContext, counterparty_id: int
) -> tuple[str | None, str | None]:
    """Return (legal_name, envelope_category) for a counterparty id."""
    cur = await ctx.store.accounting.execute(
        "SELECT legal_name, envelope_category FROM counterparties WHERE id = ?",
        (counterparty_id,),
    )
    row = await cur.fetchone()
    await cur.close()
    if row is None:
        return None, None
    return row[0], row[1]


async def _fuzzy_match(
    ctx: FingentContext, label: str
) -> tuple[int | None, str | None, int]:
    """Token-set fuzzy match `label` against the top-1000 counterparties.

    Returns (counterparty_id, legal_name, score) — score is the raw rapidfuzz
    integer 0..100. Falls back to (None, None, 0) on miss.
    """
    cur = await ctx.store.accounting.execute(
        "SELECT id, legal_name FROM counterparties "
        "ORDER BY created_at DESC LIMIT 1000"
    )
    rows = await cur.fetchall()
    await cur.close()
    if not rows:
        return None, None, 0

    best_id: int | None = None
    best_name: str | None = None
    best_score = 0
    for row in rows:
        score = int(fuzz.token_set_ratio(label, row[1] or ""))
        if score > best_score:
            best_score = score
            best_id = int(row[0])
            best_name = row[1]
    return best_id, best_name, best_score


async def _writeback_identifier(
    ctx: FingentContext,
    counterparty_id: int,
    identifier_type: str,
    identifier: str,
    source: str,
    confidence: float,
) -> None:
    """INSERT OR IGNORE into counterparty_identifiers (idempotent on dup)."""
    async with write_tx(ctx.store.accounting, ctx.store.accounting_lock) as conn:
        await conn.execute(
            "INSERT OR IGNORE INTO counterparty_identifiers "
            "(counterparty_id, identifier_type, identifier, source, confidence) "
            "VALUES (?, ?, ?, ?, ?)",
            (counterparty_id, identifier_type, identifier, source, confidence),
        )


def _miss(method: str = "miss") -> dict[str, Any]:
    return {
        "counterparty_id": None,
        "counterparty_legal_name": None,
        "confidence": 0.0,
        "method": method,
        "envelope_category": None,
    }


async def _hit(
    ctx: FingentContext,
    counterparty_id: int,
    confidence: float,
    method: str,
) -> dict[str, Any]:
    legal_name, envelope = await _fetch_counterparty(ctx, counterparty_id)
    return {
        "counterparty_id": counterparty_id,
        "counterparty_legal_name": legal_name,
        "confidence": confidence,
        "method": method,
        "envelope_category": envelope,
    }


async def run(ctx: FingentContext) -> dict[str, Any]:
    """Execute the four-stage cascade. Returns the resolution envelope.

    `tx = ctx.get('fetch-transaction')` — Swan webhook path.
    `extraction = ctx.get('extract')` — document path (Stage 4 only).
    """
    tx = ctx.get("fetch-transaction") or {}
    extraction = ctx.get("extract") or {}

    has_swan_tx = bool(tx)

    # ---- Stages 1 & 2: IBAN exact (outbound creditor / inbound debtor) ----
    if has_swan_tx:
        iban, inbound = _extract_ibans(tx)
        if iban:
            counterparty_id = await _lookup_identifier(ctx, "iban", iban)
            if counterparty_id is not None:
                method = "virtual_iban" if inbound else "iban"
                # Cache writeback is a no-op (identifier already exists),
                # but keep the call for symmetry; INSERT OR IGNORE swallows.
                await _writeback_identifier(
                    ctx, counterparty_id, "iban", iban, method, 1.0
                )
                return await _hit(ctx, counterparty_id, 1.0, method)

        # ---- Stage 3a: merchant id (card transactions) ----
        tx_type = tx.get("type") or ""
        merchant_id = tx.get("merchantId") or tx.get("merchant_id")
        if merchant_id and ("Card" in tx_type or merchant_id):
            counterparty_id = await _lookup_identifier(
                ctx, "merchant_id", merchant_id
            )
            if counterparty_id is not None:
                await _writeback_identifier(
                    ctx, counterparty_id, "merchant_id", merchant_id,
                    "merchant_id", 0.95,
                )
                return await _hit(ctx, counterparty_id, 0.95, "merchant_id")

        # ---- Stage 3b: MCC + fuzzy on merchant name ----
        # MVP: skip MCC filter, fuzzy across all counterparties on the
        # merchant label / counterparty_label.
        if tx.get("mcc"):
            mcc_label: str | None = tx.get("merchantName") or tx.get("counterparty_label")
            if not mcc_label:
                raw_cp = tx.get("counterparty")
                if isinstance(raw_cp, dict):
                    mcc_label = raw_cp.get("name")
                elif isinstance(raw_cp, str):
                    mcc_label = raw_cp
            if mcc_label:
                cp_id, _legal_name, score = await _fuzzy_match(ctx, mcc_label)
                if cp_id is not None and score >= _FUZZY_THRESHOLD:
                    confidence = min(_FUZZY_CONFIDENCE_CAP, score / 100.0)
                    await _writeback_identifier(
                        ctx, cp_id, "name_alias", mcc_label, "mcc_fuzzy", confidence,
                    )
                    return await _hit(ctx, cp_id, confidence, "mcc_fuzzy")

    # ---- Stage 4: pure fuzzy on counterparty label / supplier name ----
    label: str | None = None
    if has_swan_tx:
        raw_cp = tx.get("counterparty")
        label = tx.get("counterparty_label")
        if not label and isinstance(raw_cp, dict):
            label = raw_cp.get("name")
        elif not label and isinstance(raw_cp, str):
            label = raw_cp
    if not label and extraction:
        label = extraction.get("supplier_name") or extraction.get("counterparty_name")

    if label:
        cp_id, _legal_name, score = await _fuzzy_match(ctx, label)
        if cp_id is not None and score >= _FUZZY_THRESHOLD:
            confidence = min(_FUZZY_CONFIDENCE_CAP, score / 100.0)
            await _writeback_identifier(
                ctx, cp_id, "name_alias", label, "fuzzy", confidence,
            )
            return await _hit(ctx, cp_id, confidence, "fuzzy")

    return _miss()
