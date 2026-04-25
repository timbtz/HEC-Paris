"""Bootstrap the orchestration.db schema."""
from __future__ import annotations

import aiosqlite

from .._helpers import apply_schema_file


async def up(conn: aiosqlite.Connection) -> None:
    await apply_schema_file(conn, "orchestration")
