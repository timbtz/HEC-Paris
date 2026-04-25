"""Shared helpers for migration `up()` functions."""
from __future__ import annotations

from pathlib import Path

import aiosqlite

from . import split_sql_statements

_SCHEMA_DIR = Path(__file__).resolve().parent.parent / "schema"


async def apply_schema_file(conn: aiosqlite.Connection, schema_name: str) -> None:
    """Read `schema/<schema_name>.sql` and exec it inside the open transaction.

    Statement-by-statement on purpose — `executescript` would COMMIT the
    enclosing BEGIN IMMEDIATE (REF-SQLITE-BACKBONE.md:609-642).
    """
    sql = (_SCHEMA_DIR / f"{schema_name}.sql").read_text(encoding="utf-8")
    for stmt in split_sql_statements(sql):
        await conn.execute(stmt)
