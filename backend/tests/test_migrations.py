"""Bootstrap-replay parity + idempotency."""
from __future__ import annotations

from pathlib import Path

from backend.orchestration.store.bootstrap import open_dbs
from backend.orchestration.store.migrations import migrate_all


async def _dump_schema(conn) -> list[tuple[str, str]]:
    cur = await conn.execute(
        "SELECT type, name, sql FROM sqlite_master "
        "WHERE name NOT LIKE 'sqlite_%' "
        "ORDER BY type, name"
    )
    rows = await cur.fetchall()
    # (type, name): sql is what matters; ignore None for indexes auto-made
    return [(r[1], (r[2] or "").strip()) for r in rows]


async def test_round_trip_three_dbs(tmp_path: Path):
    """schema-from-bootstrap == schema-from-migration-replay on each DB."""
    h1 = await open_dbs(tmp_path / "first")
    try:
        first = {
            "accounting":    await _dump_schema(h1.accounting),
            "orchestration": await _dump_schema(h1.orchestration),
            "audit":         await _dump_schema(h1.audit),
        }
    finally:
        await h1.close()

    # A second fresh stack should produce identical schema.
    h2 = await open_dbs(tmp_path / "second")
    try:
        second = {
            "accounting":    await _dump_schema(h2.accounting),
            "orchestration": await _dump_schema(h2.orchestration),
            "audit":         await _dump_schema(h2.audit),
        }
    finally:
        await h2.close()

    assert first == second, "Bootstrap-replay parity failed"


async def test_migrations_idempotent(store):
    """Running migrate_all again applies nothing."""
    again = await migrate_all(store)
    assert again == {"accounting": [], "orchestration": [], "audit": []}


async def test_audit_seed_employees(store):
    """0002_seed_employees populates Tim/Marie/Paul."""
    cur = await store.audit.execute("SELECT email FROM employees ORDER BY email")
    rows = [r[0] for r in await cur.fetchall()]
    assert rows == sorted(["tim@hec.example", "marie@hec.example", "paul@hec.example"])
