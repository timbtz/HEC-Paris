"""Phase 3 schema delta + VAT/period seed.

Adds:
1. `accounting_periods` — fiscal periods with status (open / closing / closed).
2. `period_reports`     — one row per generated report (balance sheet, P&L,
   VAT return, period close summary, year-end close).
3. `journal_entries.posted_at` (TEXT) — posting timestamp, distinct from the
   original `created_at`. Backfilled to `created_at` for existing rows.

Seeds:
4. `vat_rates` — French TVA standard 20% (deductible 4456) and collected
   20% (445), valid from 2025-01-01.
5. `accounting_periods` — three demo periods: 2026-Q1 (closed),
   2026-Q2 (closing), 2026-Q3 (open).
6. `chart_of_accounts.120` — French PCG retained-earnings account, used
   by the year-end closing entry that the `year_end_close.yaml` pipeline
   posts via `gl_poster.post`.

All inserts are idempotent (NOT EXISTS guard or INSERT OR IGNORE).
"""
from __future__ import annotations

import aiosqlite


async def _has_column(conn: aiosqlite.Connection, table: str, column: str) -> bool:
    cur = await conn.execute(f"PRAGMA table_info({table})")
    rows = await cur.fetchall()
    await cur.close()
    return any(r[1] == column for r in rows)


async def up(conn: aiosqlite.Connection) -> None:
    # 1. accounting_periods --------------------------------------------------
    await conn.execute(
        "CREATE TABLE IF NOT EXISTS accounting_periods ("
        " id INTEGER PRIMARY KEY,"
        " code TEXT NOT NULL UNIQUE,"
        " start_date TEXT NOT NULL,"
        " end_date TEXT NOT NULL,"
        " status TEXT NOT NULL CHECK(status IN ('open','closing','closed')) DEFAULT 'open',"
        " closed_at TEXT,"
        " closed_by INTEGER"
        ") STRICT"
    )
    await conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_periods_status_end "
        "ON accounting_periods(status, end_date)"
    )

    # 2. period_reports ------------------------------------------------------
    await conn.execute(
        "CREATE TABLE IF NOT EXISTS period_reports ("
        " id INTEGER PRIMARY KEY,"
        " period_code TEXT NOT NULL,"
        " report_type TEXT NOT NULL,"
        " status TEXT NOT NULL CHECK(status IN ('draft','final','flagged')) DEFAULT 'draft',"
        " confidence REAL,"
        " source_run_id INTEGER,"
        " blob_path TEXT,"
        " payload_json TEXT,"
        " created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,"
        " approved_at TEXT,"
        " approved_by INTEGER,"
        " CHECK (payload_json IS NULL OR json_valid(payload_json))"
        ") STRICT"
    )
    await conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_period_reports_period "
        "ON period_reports(period_code, report_type)"
    )
    await conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_period_reports_status "
        "ON period_reports(status, created_at)"
    )

    # 3. journal_entries.posted_at ------------------------------------------
    if not await _has_column(conn, "journal_entries", "posted_at"):
        # STRICT-table column-add: SQLite supports adding nullable TEXT
        # columns to STRICT tables; default value of `created_at` backfilled
        # below.
        await conn.execute(
            "ALTER TABLE journal_entries ADD COLUMN posted_at TEXT"
        )
        await conn.execute(
            "UPDATE journal_entries "
            "SET posted_at = created_at WHERE posted_at IS NULL"
        )

    # 4. CoA: 120 retained earnings -----------------------------------------
    # PCG account 120 ("Résultat de l'exercice") — used by the year-end
    # closing entry to absorb net income from revenue/expense accounts.
    await conn.execute(
        "INSERT OR IGNORE INTO chart_of_accounts (code, name, type, parent) "
        "VALUES ('120', \"Résultat de l'exercice\", 'equity', NULL)"
    )

    # 5. vat_rates seed ------------------------------------------------------
    # Standard French TVA 20% (2000bp), valid 2025-01-01 onwards.
    # Deductible (input VAT, asset side, account 4456).
    # Collected  (output VAT, liability side, account 445).
    _VAT_SEED = (
        ("4456", 2000),  # TVA déductible — standard
        ("445",  2000),  # TVA à décaisser — standard
    )
    for gl_account, rate_bp in _VAT_SEED:
        await conn.execute(
            "INSERT INTO vat_rates (gl_account, rate_bp, valid_from, valid_to) "
            "SELECT ?, ?, '2025-01-01', NULL "
            "WHERE NOT EXISTS ("
            "  SELECT 1 FROM vat_rates "
            "  WHERE gl_account = ? AND rate_bp = ? AND valid_from = '2025-01-01'"
            ")",
            (gl_account, rate_bp, gl_account, rate_bp),
        )

    # 6. accounting_periods seed --------------------------------------------
    _PERIOD_SEED = (
        ("2026-Q1", "2026-01-01", "2026-03-31", "closed"),
        ("2026-Q2", "2026-04-01", "2026-06-30", "closing"),
        ("2026-Q3", "2026-07-01", "2026-09-30", "open"),
    )
    for code, start, end, status in _PERIOD_SEED:
        await conn.execute(
            "INSERT OR IGNORE INTO accounting_periods "
            "(code, start_date, end_date, status) "
            "VALUES (?, ?, ?, ?)",
            (code, start, end, status),
        )
