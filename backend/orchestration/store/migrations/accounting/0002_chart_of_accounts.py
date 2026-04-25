"""Seed the demo PCG chart of accounts.

Source: RealMetaPRD §15.3 (lines 1773-1791) — the French PCG subset used by
the MVP demo. Codes are TEXT (PCG accounts are zero-padded strings, not
integers). Hierarchy: '4456' rolls up to '445'; '626100' / '626200' are kept
as flat parents for MVP simplicity (no '626' category row).

Idempotent via INSERT OR IGNORE on the PRIMARY KEY `code`.
"""
from __future__ import annotations

import aiosqlite


# (code, name, type, parent)
_ACCOUNTS: tuple[tuple[str, str, str, str | None], ...] = (
    ("411",    "Clients",                       "asset",     None),
    ("401",    "Fournisseurs",                  "liability", None),
    ("421",    "Personnel - rémunérations dues", "liability", None),
    ("445",    "TVA - à décaisser",             "liability", None),
    ("4456",   "TVA déductible",                "asset",     "445"),
    ("512",    "Banque",                        "asset",     None),
    ("606100", "Fournitures de bureau",         "expense",   None),
    ("613",    "Locations / Leasing",           "expense",   None),
    ("624",    "Transports / Voyages",          "expense",   None),
    ("6257",   "Réceptions / Restauration",     "expense",   None),
    ("626100", "Services API / cloud",          "expense",   None),
    ("626200", "Abonnements SaaS",              "expense",   None),
    ("627",    "Services bancaires",            "expense",   None),
    ("706000", "Prestations de services",       "revenue",   None),
)


async def up(conn: aiosqlite.Connection) -> None:
    # Insert rows without a parent first so the FK on `4456 -> 445` resolves
    # cleanly regardless of executemany ordering quirks.
    parents_first = sorted(_ACCOUNTS, key=lambda r: 0 if r[3] is None else 1)
    await conn.executemany(
        "INSERT OR IGNORE INTO chart_of_accounts (code, name, type, parent) "
        "VALUES (?, ?, ?, ?)",
        parents_first,
    )
