"""Seed the demo counterparties (suppliers + customers) and their IBANs.

Source: RealMetaPRD §15.2 — the supplier list (Anthropic, Notion, OFI,
Boulangerie Paul, SNCF, Hostelo, Linear, Fin) plus 5 customers with
virtual IBANs.

IBAN strings are syntactically plausible French IBANs (`FR76` + 23 chars =
27 total). Checksums are not validated; the resolver only does exact-string
lookup. The `_DEMO_COMPANY_*` and per-employee IBAN constants here are the
canonical source — `audit/0003_seed_swan_links.py` references the
`acc_demo_company` account_id used by `0006_demo_swan_transactions.py`.
"""
from __future__ import annotations

import aiosqlite


# Synthetic IBANs — exposed module-level so other migrations can import them.
SUPPLIER_IBANS: dict[str, str] = {
    "Anthropic":        "FR7610278060610001020480101",
    "Notion":           "FR7610278060610001020480102",
    "OFI":              "FR7610278060610001020480103",
    "Boulangerie Paul": "FR7610278060610001020480104",
    "SNCF":             "FR7610278060610001020480105",
    "Hostelo":          "FR7610278060610001020480106",
    "Linear":           "FR7610278060610001020480107",
    "Fin":              "FR7610278060610001020480108",
    "OpenAI":           "FR7610278060610001020480109",
}

CUSTOMER_IBANS: dict[str, str] = {
    "Acme SAS":     "FR7610278060610001020480201",
    "Beta GmbH":    "FR7610278060610001020480202",
    "Gamma Ltd":    "FR7610278060610001020480203",
    "Delta SARL":   "FR7610278060610001020480204",
    "Epsilon Corp": "FR7610278060610001020480205",
}

# (legal_name, kind, envelope_category)
_SUPPLIERS: tuple[tuple[str, str, str], ...] = (
    ("Anthropic",        "supplier", "ai_tokens"),
    ("Notion",           "supplier", "saas"),
    ("OFI",              "supplier", "saas"),
    ("Boulangerie Paul", "supplier", "food"),
    ("SNCF",             "supplier", "travel"),
    ("Hostelo",          "supplier", "travel"),
    ("Linear",           "supplier", "saas"),
    ("Fin",              "supplier", "leasing"),
    ("OpenAI",           "supplier", "ai_tokens"),
)

_CUSTOMERS: tuple[tuple[str, str, str | None], ...] = (
    ("Acme SAS",     "customer", None),
    ("Beta GmbH",    "customer", None),
    ("Gamma Ltd",    "customer", None),
    ("Delta SARL",   "customer", None),
    ("Epsilon Corp", "customer", None),
)


async def _insert_counterparty(
    conn: aiosqlite.Connection,
    legal_name: str,
    kind: str,
    primary_iban: str,
    envelope_category: str | None,
) -> int:
    """Insert (or fetch) a counterparty row + return its id."""
    cur = await conn.execute(
        "SELECT id FROM counterparties WHERE legal_name = ? AND kind = ?",
        (legal_name, kind),
    )
    row = await cur.fetchone()
    await cur.close()
    if row is not None:
        return int(row[0])

    cur = await conn.execute(
        "INSERT INTO counterparties "
        "(legal_name, kind, primary_iban, confidence, sources, envelope_category) "
        "VALUES (?, ?, ?, 1.0, '[\"config\"]', ?)",
        (legal_name, kind, primary_iban, envelope_category),
    )
    cp_id = cur.lastrowid
    await cur.close()
    assert cp_id is not None
    return int(cp_id)


async def up(conn: aiosqlite.Connection) -> None:
    for legal_name, kind, env_cat in _SUPPLIERS:
        iban = SUPPLIER_IBANS[legal_name]
        cp_id = await _insert_counterparty(conn, legal_name, kind, iban, env_cat)
        await conn.execute(
            "INSERT OR IGNORE INTO counterparty_identifiers "
            "(counterparty_id, identifier_type, identifier, source, confidence) "
            "VALUES (?, 'iban', ?, 'config', 1.0)",
            (cp_id, iban),
        )

    for legal_name, kind, env_cat in _CUSTOMERS:
        iban = CUSTOMER_IBANS[legal_name]
        cp_id = await _insert_counterparty(conn, legal_name, kind, iban, env_cat)
        await conn.execute(
            "INSERT OR IGNORE INTO counterparty_identifiers "
            "(counterparty_id, identifier_type, identifier, source, confidence) "
            "VALUES (?, 'iban', ?, 'config', 1.0)",
            (cp_id, iban),
        )
