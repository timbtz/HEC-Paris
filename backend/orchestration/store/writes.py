"""Single sanctioned write path.

Source: REF-SQLITE-BACKBONE.md:295-316. `BEGIN IMMEDIATE` is mandatory —
`BEGIN DEFERRED` (the default) upgrades to writer mid-transaction and
can fail with SQLITE_BUSY even with `busy_timeout` set
(REF-SQLITE-BACKBONE.md:237-288).
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import AsyncIterator

import aiosqlite


@asynccontextmanager
async def write_tx(
    conn: aiosqlite.Connection,
    lock: asyncio.Lock,
) -> AsyncIterator[aiosqlite.Connection]:
    """Acquire the per-DB lock and run a BEGIN IMMEDIATE transaction.

    Commits on normal exit; rolls back if the body raises.
    """
    async with lock:
        await conn.execute("BEGIN IMMEDIATE")
        try:
            yield conn
        except BaseException:
            await conn.rollback()
            raise
        else:
            await conn.commit()
