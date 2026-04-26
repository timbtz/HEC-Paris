"""Reset demo-only state so the AI Spend / Runs / period_close demos work again.

What it does (idempotent):

1. Deletes every `external_events` row produced by the Swan demo simulator
   so the "Simulate event" button on the Runs page can re-fire all 200
   seeded transactions from scratch.
2. Backfills `accounting_periods` with the four 2025 quarters so the
   period-close demo has a target with real data — the base seed only
   ships 2026-Q1/Q2/Q3, but the demo journal entries actually live in
   2025-04..2025-12. Without this, "Run period_close on 2026-Q3"
   reports `0 cents balanced` because Q3 is empty by construction.

Run from project root:
    python -m backend.scripts.reset_demo_state
    python -m backend.scripts.reset_demo_state --data-dir ./data
"""
from __future__ import annotations

import argparse
import sqlite3
from contextlib import closing
from pathlib import Path


# Periods that cover the 2025 demo seed range. status='open' so the user
# can run period_close against them in the UI.
EXTRA_PERIODS: list[tuple[str, str, str, str]] = [
    # (code, start_date, end_date, status)
    ("2025-Q2", "2025-04-01", "2025-06-30", "open"),
    ("2025-Q3", "2025-07-01", "2025-09-30", "open"),
    ("2025-Q4", "2025-10-01", "2025-12-31", "open"),
]


def reset(data_dir: Path) -> dict[str, int]:
    counts = {"events_deleted": 0, "periods_added": 0, "periods_skipped": 0}

    # ---- 1. external_events (orchestration.db) -----------------------------
    orch = data_dir / "orchestration.db"
    if not orch.is_file():
        raise SystemExit(f"orchestration.db not found under {data_dir}")
    with closing(sqlite3.connect(str(orch))) as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            cur = conn.execute(
                "DELETE FROM external_events WHERE provider = 'swan'"
            )
            counts["events_deleted"] = cur.rowcount
            conn.commit()
        except Exception:
            conn.rollback()
            raise

    # ---- 2. accounting_periods (accounting.db) -----------------------------
    acct = data_dir / "accounting.db"
    if not acct.is_file():
        raise SystemExit(f"accounting.db not found under {data_dir}")
    with closing(sqlite3.connect(str(acct))) as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            for code, start, end, status in EXTRA_PERIODS:
                cur = conn.execute(
                    "SELECT 1 FROM accounting_periods WHERE code = ?", (code,)
                )
                if cur.fetchone():
                    counts["periods_skipped"] += 1
                    continue
                conn.execute(
                    "INSERT INTO accounting_periods "
                    "(code, start_date, end_date, status) "
                    "VALUES (?, ?, ?, ?)",
                    (code, start, end, status),
                )
                counts["periods_added"] += 1
            conn.commit()
        except Exception:
            conn.rollback()
            raise
    return counts


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--data-dir", type=Path, default=Path("./data"),
        help="Directory containing accounting.db / orchestration.db.",
    )
    args = parser.parse_args()
    counts = reset(args.data_dir.resolve())
    print(
        "Demo state reset complete:\n"
        f"  swan external_events deleted: {counts['events_deleted']}\n"
        f"  accounting periods added:     {counts['periods_added']} "
        f"(already present: {counts['periods_skipped']})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
