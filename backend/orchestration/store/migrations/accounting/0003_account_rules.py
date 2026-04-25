"""Seed deterministic account_rules for the demo counterparties + MCC fallbacks.

Source: RealMetaPRD §15.2 (counterparty list) + §15.3 (chart of accounts).

Precedence ordering (lower wins): counterparty rules @ 10 beat MCC rules @ 50,
which would beat any future generic catch-alls. Idempotent via INSERT OR IGNORE
on a synthetic uniqueness — `account_rules` has no UNIQUE constraint in the
schema, so we guard re-application with NOT EXISTS.
"""
from __future__ import annotations

import aiosqlite


# (pattern_kind, pattern_value, gl_account, precedence, source)
_RULES: tuple[tuple[str, str, str, int, str], ...] = (
    # Counterparty rules (precedence 10).
    ("counterparty", "Anthropic",        "626100", 10, "config"),
    ("counterparty", "Notion",           "626200", 10, "config"),
    ("counterparty", "OFI",              "626200", 10, "config"),
    ("counterparty", "Boulangerie Paul", "6257",   10, "config"),
    ("counterparty", "SNCF",             "624",    10, "config"),
    ("counterparty", "Linear",           "626200", 10, "config"),
    ("counterparty", "Fin",              "613",    10, "config"),
    ("counterparty", "OpenAI",           "626100", 10, "config"),
    ("counterparty", "Hostelo",          "624",    10, "config"),
    # MCC fallbacks (precedence 50).
    ("mcc", "5814", "6257", 50, "config"),  # Eating places / fast food
    ("mcc", "5811", "6257", 50, "config"),  # Caterers
    ("mcc", "4112", "624",  50, "config"),  # Passenger railways
    ("mcc", "7011", "624",  50, "config"),  # Lodging / hotels
)


async def up(conn: aiosqlite.Connection) -> None:
    for kind, value, gl, prec, src in _RULES:
        await conn.execute(
            "INSERT INTO account_rules "
            "(pattern_kind, pattern_value, gl_account, precedence, source) "
            "SELECT ?, ?, ?, ?, ? "
            "WHERE NOT EXISTS ("
            "  SELECT 1 FROM account_rules "
            "  WHERE pattern_kind = ? AND pattern_value = ? AND gl_account = ?"
            ")",
            (kind, value, gl, prec, src, kind, value, gl),
        )
