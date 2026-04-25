"""Create the `review_queue` table — low-confidence work tray.

Phase 2 introduces a human-in-the-loop fallback: any low-confidence
classification or unresolved counterparty is queued here for an operator
to triage. `entry_id` is nullable because some review items (e.g.,
unresolved Swan webhooks) exist before any journal entry is created.
"""
from __future__ import annotations

import aiosqlite


async def up(conn: aiosqlite.Connection) -> None:
    await conn.execute(
        "CREATE TABLE IF NOT EXISTS review_queue ("
        " id INTEGER PRIMARY KEY,"
        " entry_id INTEGER REFERENCES journal_entries(id),"
        " kind TEXT NOT NULL,"
        " confidence REAL,"
        " reason TEXT,"
        " created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,"
        " resolved_at TEXT,"
        " resolved_by INTEGER"
        ") STRICT"
    )
