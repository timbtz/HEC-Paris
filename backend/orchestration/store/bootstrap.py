"""DB connection lifecycle.

Source patterns:
- REF-SQLITE-BACKBONE.md:149-195 (long-lived connections on app.state)
- REF-SQLITE-BACKBONE.md:199-230 (PRAGMA list)
- RealMetaPRD §6.6 (canonical PRAGMA block)
- RealMetaPRD §6.5 (three-DB layout)
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path

import aiosqlite

# PRAGMAs run on every connection at open time, in order.
# foreign_keys is per-connection, not per-DB; if we ever close and reopen,
# this must be re-issued. (REF-SQLITE-BACKBONE.md:186-190)
_PRAGMAS: tuple[tuple[str, str], ...] = (
    ("journal_mode",       "WAL"),
    ("foreign_keys",       "ON"),
    ("synchronous",        "NORMAL"),         # REF-SQLITE-BACKBONE:209
    ("busy_timeout",       "5000"),
    ("temp_store",         "MEMORY"),         # REF-SQLITE-BACKBONE:213
    ("cache_size",         "-65536"),         # 64 MB; REF-SQLITE-BACKBONE:214
    ("mmap_size",          "134217728"),      # 128 MB; REF-SQLITE-BACKBONE:215
    ("wal_autocheckpoint", "1000"),
    ("journal_size_limit", "67108864"),       # 64 MB
)

_DB_NAMES: tuple[str, ...] = ("accounting", "orchestration", "audit")


@dataclass(frozen=True)
class StoreHandles:
    """Three connections + three locks. Single-writer per DB.

    All writes must go through `store.writes.write_tx`; never call
    `conn.commit()` directly. (REF-SQLITE-BACKBONE.md:295-316)
    """
    accounting: aiosqlite.Connection
    orchestration: aiosqlite.Connection
    audit: aiosqlite.Connection
    accounting_lock: asyncio.Lock
    orchestration_lock: asyncio.Lock
    audit_lock: asyncio.Lock
    data_dir: Path

    def conn_for(self, name: str) -> aiosqlite.Connection:
        return getattr(self, name)

    def lock_for(self, name: str) -> asyncio.Lock:
        return getattr(self, f"{name}_lock")

    async def close(self) -> None:
        for name in _DB_NAMES:
            conn = getattr(self, name)
            try:
                await conn.close()
            except Exception:  # noqa: BLE001
                pass


async def _apply_pragmas(conn: aiosqlite.Connection) -> None:
    for pragma, value in _PRAGMAS:
        # `PRAGMA journal_mode = WAL;` returns the resulting mode; consume cursor.
        cur = await conn.execute(f"PRAGMA {pragma} = {value};")
        await cur.fetchall()
        await cur.close()


async def _open_one(path: Path) -> aiosqlite.Connection:
    conn = await aiosqlite.connect(str(path))
    conn.row_factory = aiosqlite.Row
    await _apply_pragmas(conn)
    return conn


async def open_dbs(data_dir: Path, *, run_migrations: bool = True) -> StoreHandles:
    """Open the three project DBs and (optionally) bring them up to schema head.

    The three connections are long-lived; each is paired with an
    `asyncio.Lock` enforcing the single-writer invariant inside the
    process.
    """
    data_dir.mkdir(parents=True, exist_ok=True)

    accounting    = await _open_one(data_dir / "accounting.db")
    orchestration = await _open_one(data_dir / "orchestration.db")
    audit         = await _open_one(data_dir / "audit.db")

    handles = StoreHandles(
        accounting=accounting,
        orchestration=orchestration,
        audit=audit,
        accounting_lock=asyncio.Lock(),
        orchestration_lock=asyncio.Lock(),
        audit_lock=asyncio.Lock(),
        data_dir=data_dir,
    )

    if run_migrations:
        # Imported lazily to avoid circular import (migrations imports writes,
        # writes is sibling of bootstrap).
        from .migrations import migrate_all
        await migrate_all(handles)

    return handles
