"""Add `envelope_category` to counterparties.

This is the lookup column the budget resolver reads to know which envelope
to debit when a Swan transaction settles against a known counterparty.

`ALTER TABLE … ADD COLUMN` is supported on STRICT tables but the runner
re-applies migrations on a fresh DB only — to keep this idempotent against
manual reruns we swallow the `duplicate column` OperationalError.
"""
from __future__ import annotations

import aiosqlite


async def up(conn: aiosqlite.Connection) -> None:
    try:
        await conn.execute(
            "ALTER TABLE counterparties ADD COLUMN envelope_category TEXT"
        )
    except aiosqlite.OperationalError as exc:
        if "duplicate column" not in str(exc).lower():
            raise
