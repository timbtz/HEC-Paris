"""Per-DB migration runner.

Source: REF-SQLITE-BACKBONE.md:598-655 (`_migrations` runner pattern).
Each migration is a Python module exposing `async def up(conn)`; the runner
discovers them by filename, sorts ascending, applies inside `write_tx`, and
appends a row to `_migrations`.

Migrations must use per-statement `await conn.execute(stmt)` — `executescript`
silently `COMMIT`s any open transaction (REF-SQLITE-BACKBONE.md:609-642), which
would break our `BEGIN IMMEDIATE` discipline.
"""
from __future__ import annotations

import asyncio
import importlib
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

import aiosqlite

from ..writes import write_tx

if TYPE_CHECKING:
    from ..bootstrap import StoreHandles


_DB_NAMES: tuple[str, ...] = ("accounting", "orchestration", "audit")
_MIGRATIONS_PKG_BASE = "backend.orchestration.store.migrations"


def split_sql_statements(sql: str) -> list[str]:
    """Split a SQL script into individual statements.

    Handles:
    - `-- line comments`
    - single- and double-quoted string literals (with `''` / `""` escapes)

    Block comments `/* ... */` are not used in our schemas; not parsed.
    """
    statements: list[str] = []
    buf: list[str] = []
    n = len(sql)
    i = 0
    in_string = False
    quote: str | None = None

    while i < n:
        ch = sql[i]
        if not in_string:
            if ch == "-" and i + 1 < n and sql[i + 1] == "-":
                while i < n and sql[i] != "\n":
                    i += 1
                continue
            if ch in ("'", '"'):
                in_string = True
                quote = ch
                buf.append(ch)
                i += 1
                continue
            if ch == ";":
                stmt = "".join(buf).strip()
                if stmt:
                    statements.append(stmt)
                buf = []
                i += 1
                continue
            buf.append(ch)
            i += 1
        else:
            buf.append(ch)
            if ch == quote:
                if i + 1 < n and sql[i + 1] == quote:
                    buf.append(sql[i + 1])
                    i += 2
                    continue
                in_string = False
                quote = None
            i += 1

    tail = "".join(buf).strip()
    if tail:
        statements.append(tail)
    return statements


class MigrationRunner:
    """Brings one DB up to schema head."""

    def __init__(
        self,
        conn: aiosqlite.Connection,
        lock: asyncio.Lock,
        migrations_pkg: str,
    ) -> None:
        self.conn = conn
        self.lock = lock
        self.migrations_pkg = migrations_pkg

    async def applied(self) -> set[str]:
        """Names already in `_migrations`. Empty set if the table is missing."""
        try:
            cur = await self.conn.execute("SELECT name FROM _migrations")
            rows = await cur.fetchall()
            await cur.close()
        except aiosqlite.OperationalError:
            return set()
        return {r[0] for r in rows}

    def _discover(self) -> list[tuple[str, str]]:
        """Sorted list of (migration_name, dotted_module_path)."""
        pkg = importlib.import_module(self.migrations_pkg)
        pkg_dir = Path(pkg.__file__).parent if pkg.__file__ else None
        if pkg_dir is None:
            return []
        names = sorted(
            p.stem
            for p in pkg_dir.iterdir()
            if p.is_file()
            and p.suffix == ".py"
            and p.stem != "__init__"
            and p.stem[:1].isdigit()
        )
        return [(n, f"{self.migrations_pkg}.{n}") for n in names]

    async def run_unapplied(self) -> list[str]:
        """Apply every pending migration. Re-run is a no-op."""
        already = await self.applied()
        applied_now: list[str] = []
        for name, dotted in self._discover():
            if name in already:
                continue
            module = importlib.import_module(dotted)
            up = getattr(module, "up", None)
            if up is None or not asyncio.iscoroutinefunction(up):
                raise RuntimeError(
                    f"Migration {dotted} must define `async def up(conn)`."
                )
            async with write_tx(self.conn, self.lock) as conn:
                await up(conn)
                # `_migrations` is created by 0001_init; INSERT OR IGNORE keeps
                # this safe across re-applications and migrations that already
                # touch the table.
                await conn.execute(
                    "INSERT OR IGNORE INTO _migrations (name, applied_at) "
                    "VALUES (?, ?)",
                    (name, datetime.now(timezone.utc).isoformat()),
                )
            applied_now.append(name)
        return applied_now


async def migrate_all(handles: "StoreHandles") -> dict[str, list[str]]:
    """Bring every DB to head. Returns map of db_name → newly applied names."""
    out: dict[str, list[str]] = {}
    for name in _DB_NAMES:
        runner = MigrationRunner(
            handles.conn_for(name),
            handles.lock_for(name),
            f"{_MIGRATIONS_PKG_BASE}.{name}",
        )
        out[name] = await runner.run_unapplied()
    return out
