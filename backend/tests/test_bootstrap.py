"""PRAGMA + lock + write_tx invariants on a fresh tmp DB stack."""
from __future__ import annotations

import asyncio

import pytest

from backend.orchestration.store.bootstrap import open_dbs
from backend.orchestration.store.writes import write_tx


_EXPECTED_PRAGMAS = {
    "journal_mode": "wal",
    "foreign_keys": 1,
    "synchronous": 1,        # NORMAL
    "busy_timeout": 5000,
    "temp_store": 2,         # MEMORY
    "wal_autocheckpoint": 1000,
}


async def test_pragmas_applied(store):
    for db in (store.accounting, store.orchestration, store.audit):
        for pragma, expected in _EXPECTED_PRAGMAS.items():
            cur = await db.execute(f"PRAGMA {pragma}")
            row = await cur.fetchone()
            assert row is not None
            actual = row[0]
            if isinstance(expected, str):
                assert str(actual).lower() == expected, f"{pragma}: {actual!r} != {expected!r}"
            else:
                assert actual == expected, f"{pragma}: {actual!r} != {expected!r}"


async def test_three_distinct_locks(store):
    locks = {id(store.accounting_lock), id(store.orchestration_lock), id(store.audit_lock)}
    assert len(locks) == 3


async def test_write_tx_commits(store):
    async with write_tx(store.audit, store.audit_lock) as conn:
        await conn.execute(
            "INSERT INTO employees (email, full_name, active) VALUES (?, ?, 1)",
            ("commit@example.com", "Commit Tester"),
        )
    cur = await store.audit.execute(
        "SELECT email FROM employees WHERE email = ?", ("commit@example.com",))
    row = await cur.fetchone()
    assert row is not None and row[0] == "commit@example.com"


async def test_write_tx_rolls_back(store):
    with pytest.raises(RuntimeError):
        async with write_tx(store.audit, store.audit_lock) as conn:
            await conn.execute(
                "INSERT INTO employees (email, full_name, active) VALUES (?, ?, 1)",
                ("rollback@example.com", "Rollback Tester"),
            )
            raise RuntimeError("simulated body failure")

    cur = await store.audit.execute(
        "SELECT email FROM employees WHERE email = ?", ("rollback@example.com",))
    assert await cur.fetchone() is None


async def test_open_dbs_creates_data_dir(tmp_path):
    target = tmp_path / "doesnotexist" / "nested"
    handles = await open_dbs(target)
    try:
        assert target.exists() and target.is_dir()
        for name in ("accounting", "orchestration", "audit"):
            assert (target / f"{name}.db").exists()
    finally:
        await handles.close()
