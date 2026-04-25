"""Seed three demo employees: Tim, Marie, Paul.

Source: RealMetaPRD §15.2 line 1757. `swan_iban` and `swan_account_id`
left NULL for now — Phase D fills them in once Swan sandbox accounts are
provisioned. The `email UNIQUE` constraint + `INSERT OR IGNORE` keeps
re-application safe.
"""
from __future__ import annotations

import aiosqlite


async def up(conn: aiosqlite.Connection) -> None:
    await conn.executemany(
        "INSERT OR IGNORE INTO employees (email, full_name, department, active) "
        "VALUES (?, ?, ?, 1)",
        [
            ("tim@hec.example",   "Tim",   "Founder"),
            ("marie@hec.example", "Marie", "Engineering"),
            ("paul@hec.example",  "Paul",  "Operations"),
        ],
    )
