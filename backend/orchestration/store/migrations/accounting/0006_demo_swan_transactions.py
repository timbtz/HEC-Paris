"""Generate ~200 deterministic synthetic Swan transactions across 12 months.

Source: RealMetaPRD §15.2 — "~12 months of synthetic activity, ~200 rows".
Distribution per spec:
- 30% CardOutDebit       (supplier, MCC populated)
- 20% SepaCreditTransferIn  (customer, Credit)
- 30% SepaCreditTransferOut (Anthropic / Notion / OFI / Fin, Debit)
- 10% Fees               (Debit, no counterparty)
- 10% InternalCreditTransfer (mixed direction; treated as Debit for balance math)

Determinism: `random.seed(42)` + sorted iteration. Re-running produces
identical rows (so INSERT OR IGNORE on `id` keeps re-application clean).

Balance invariant: a single Swan account `acc_demo_company` carries the
running balance; we sort by date ascending and compute
`booked_balance_after` cumulatively so RealMetaPRD §7.6 invariant 2
(monotonic balance) holds for the seed data.
"""
from __future__ import annotations

import json
import random
import uuid
from datetime import date, timedelta

import aiosqlite


SWAN_ACCOUNT_ID = "acc_demo_company"
_OPENING_BALANCE_CENTS = 5_000_000  # €50,000
_TOTAL_TX = 200

# Counterparty pools per type.
_CARD_SUPPLIERS_WITH_MCC: tuple[tuple[str, str], ...] = (
    ("Boulangerie Paul", "5814"),
    ("Boulangerie Paul", "5811"),
    ("SNCF",             "4112"),
    ("Hostelo",          "7011"),
    ("Anthropic",        "5734"),
    ("Notion",           "5734"),
    ("Linear",           "5734"),
    ("OpenAI",           "5734"),
)
_SEPA_OUT_SUPPLIERS: tuple[str, ...] = ("Anthropic", "Notion", "OFI", "Fin")
_SEPA_IN_CUSTOMERS: tuple[str, ...] = (
    "Acme SAS", "Beta GmbH", "Gamma Ltd", "Delta SARL", "Epsilon Corp",
)


def _daterange_iso(rng: random.Random) -> str:
    """Pick an ISO date uniformly between 2025-04-01 and 2026-04-01."""
    start = date(2025, 4, 1)
    end = date(2026, 4, 1)
    days = (end - start).days
    return (start + timedelta(days=rng.randint(0, days))).isoformat()


def _generate_rows(rng: random.Random) -> list[dict]:
    rows: list[dict] = []

    counts = {
        "CardOutDebit":            int(_TOTAL_TX * 0.30),
        "SepaCreditTransferIn":    int(_TOTAL_TX * 0.20),
        "SepaCreditTransferOut":   int(_TOTAL_TX * 0.30),
        "Fees":                    int(_TOTAL_TX * 0.10),
        "InternalCreditTransfer":  int(_TOTAL_TX * 0.10),
    }

    # CardOutDebit: small spends, supplier+mcc.
    for _ in range(counts["CardOutDebit"]):
        cp, mcc = rng.choice(_CARD_SUPPLIERS_WITH_MCC)
        rows.append({
            "tx_type": "CardOutDebit",
            "side":    "Debit",
            "amount_cents": rng.randint(500, 12_000),  # €5–€120
            "counterparty_label": cp,
            "mcc": mcc,
            "execution_date": _daterange_iso(rng),
        })

    # SepaCreditTransferIn: revenue from a customer.
    for _ in range(counts["SepaCreditTransferIn"]):
        cp = rng.choice(_SEPA_IN_CUSTOMERS)
        rows.append({
            "tx_type": "SepaCreditTransferIn",
            "side":    "Credit",
            "amount_cents": rng.randint(100_000, 800_000),  # €1k–€8k
            "counterparty_label": cp,
            "mcc": None,
            "execution_date": _daterange_iso(rng),
        })

    # SepaCreditTransferOut: outbound supplier payment.
    for _ in range(counts["SepaCreditTransferOut"]):
        cp = rng.choice(_SEPA_OUT_SUPPLIERS)
        rows.append({
            "tx_type": "SepaCreditTransferOut",
            "side":    "Debit",
            "amount_cents": rng.randint(20_000, 250_000),  # €200–€2.5k
            "counterparty_label": cp,
            "mcc": None,
            "execution_date": _daterange_iso(rng),
        })

    # Fees: small bank charges.
    for _ in range(counts["Fees"]):
        rows.append({
            "tx_type": "Fees",
            "side":    "Debit",
            "amount_cents": rng.randint(50, 1500),  # €0.50–€15
            "counterparty_label": "Swan",
            "mcc": None,
            "execution_date": _daterange_iso(rng),
        })

    # InternalCreditTransfer: between own accounts; debit for balance math.
    for _ in range(counts["InternalCreditTransfer"]):
        rows.append({
            "tx_type": "InternalCreditTransfer",
            "side":    "Debit",
            "amount_cents": rng.randint(10_000, 200_000),
            "counterparty_label": "Internal Transfer",
            "mcc": None,
            "execution_date": _daterange_iso(rng),
        })

    # Sort chronologically so booked_balance_after is monotonically derivable.
    rows.sort(key=lambda r: r["execution_date"])
    return rows


async def up(conn: aiosqlite.Connection) -> None:
    rng = random.Random(42)
    rows = _generate_rows(rng)

    balance = _OPENING_BALANCE_CENTS
    insert_args: list[tuple] = []
    for row in rows:
        if row["side"] == "Debit":
            balance -= row["amount_cents"]
        else:
            balance += row["amount_cents"]

        # Deterministic UUIDs from the rng so re-runs produce identical ids.
        tx_id = str(uuid.UUID(int=rng.getrandbits(128)))
        event_id = str(uuid.UUID(int=rng.getrandbits(128)))
        payment_ref = str(uuid.UUID(int=rng.getrandbits(128)))

        raw = {
            "id": tx_id,
            "type": row["tx_type"],
            "side": row["side"],
            "status": "Booked",
            "amount": {"value": row["amount_cents"] / 100, "currency": "EUR"},
            "counterparty": {"name": row["counterparty_label"]},
            "executionDate": row["execution_date"],
            "account": {"id": SWAN_ACCOUNT_ID},
            "mcc": row["mcc"],
            "paymentReference": payment_ref,
            "bookedBalanceAfter": balance,
        }

        insert_args.append((
            tx_id,
            event_id,
            row["side"],
            row["tx_type"],
            "Booked",
            row["amount_cents"],
            "EUR",
            row["counterparty_label"],
            payment_ref,
            row["execution_date"],
            balance,
            json.dumps(raw, separators=(",", ":")),
        ))

    await conn.executemany(
        "INSERT OR IGNORE INTO swan_transactions "
        "(id, swan_event_id, side, type, status, amount_cents, currency, "
        " counterparty_label, payment_reference, execution_date, "
        " booked_balance_after, raw) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        insert_args,
    )
