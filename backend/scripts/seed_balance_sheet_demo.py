"""Seed a fuller balance sheet for the demo.

The base Swan replay (`replay_swan_seed.py`) only books **cash-out
expenses** — every entry is `DR 6xx / CR 512`. That leaves the bank
account credit-heavy (negative as a debit-natural asset) and every other
balance-sheet account at zero. The balance sheet page therefore shows
"-58 660,82 € · balanced" with empty Clients / Fournisseurs / TVA lines.

This script adds ~16 hand-crafted entries that round out the picture
under both reporting bases:

  CASH basis (`basis='cash'`)
    - Opening capital injection (DR 512 / CR 120)
    - Direct cash sales recognised on receipt (DR 512 / CR 706 + 445)
    - Quarterly TVA settlement (DR 445 / CR 512)
    - Bank fees (DR 627 / CR 512)

  ACCRUAL basis (`basis='accrual'`)
    - Open sales invoices to populate Clients / output VAT
        (DR 411 / CR 706 + 445)
    - Supplier invoices to populate Fournisseurs / input VAT
        (DR 6xx + 4456 / CR 401), with a partial cash settlement
        (DR 401 / CR 512) so 401 carries a realistic open balance

Every entry is balanced (SUM(debit_cents) == SUM(credit_cents)) and
tagged with `source_pipeline='demo:balance_sheet_seed'`. The first
action of `seed()` deletes prior rows carrying that sentinel, so
re-running the script wipes and re-inserts cleanly — idempotent by
construction.

Notes on the chokepoint rule
----------------------------
RealMetaPRD §6.4 makes `tools.gl_poster.post` the only runtime path
that writes `journal_entries`. That rule explicitly carves out
migrations and demo seeders (the existing `enrich_demo_seed.py`,
`seed_adoption_demo.py`, etc. all use direct sqlite3 inserts on their
respective tables). This file follows the same pattern: it is a demo
data utility, not a runtime path, and it never runs in production.

Run from project root::

    .venv/bin/python -m backend.scripts.seed_balance_sheet_demo
    .venv/bin/python -m backend.scripts.seed_balance_sheet_demo --data-dir ./data
"""
from __future__ import annotations

import argparse
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SENTINEL_PIPELINE = "demo:balance_sheet_seed"
SENTINEL_RUN_ID = 0


# Counterparty IDs from the seeded `counterparties` table
# (see backend/orchestration/store/migrations/accounting/0005_demo_counterparties.py).
CP = {
    "Anthropic": 1,
    "Notion": 2,
    "OFI": 3,
    "Boulangerie Paul": 4,
    "SNCF": 5,
    "Hostelo": 6,
    "Linear": 7,
}


def _line(account_code: str, debit: int = 0, credit: int = 0,
          counterparty_id: int | None = None,
          description: str | None = None) -> dict[str, Any]:
    return {
        "account_code": account_code,
        "debit_cents": debit,
        "credit_cents": credit,
        "counterparty_id": counterparty_id,
        "description": description,
    }


# Each entry: (basis, entry_date, description, [lines])
ENTRIES: list[tuple[str, str, str, list[dict[str, Any]]]] = [
    # --- CASH basis: capital, direct sales, VAT settlement, bank fees ---
    ("cash", "2025-04-01", "Apport en capital initial des fondateurs", [
        _line("512", debit=12_000_000, description="Capital deposit"),
        _line("120", credit=12_000_000, description="Apport en capital"),
    ]),
    ("cash", "2025-06-30", "Recette client — Notion (mission conseil Q2)", [
        _line("512", debit=1_800_000, counterparty_id=CP["Notion"]),
        _line("706000", credit=1_500_000, counterparty_id=CP["Notion"],
              description="Prestation conseil Q2"),
        _line("445", credit=300_000, description="TVA collectée 20%"),
    ]),
    ("cash", "2025-08-25", "Recette client — OFI (déploiement plateforme)", [
        _line("512", debit=2_400_000, counterparty_id=CP["OFI"]),
        _line("706000", credit=2_000_000, counterparty_id=CP["OFI"],
              description="Mission implémentation"),
        _line("445", credit=400_000, description="TVA collectée 20%"),
    ]),
    ("cash", "2025-10-15", "Acquittement TVA Q3 2025", [
        _line("445", debit=1_400_000, description="Solde TVA Q3"),
        _line("512", credit=1_400_000),
    ]),
    ("cash", "2025-10-30", "Recette client — Linear (retainer)", [
        _line("512", debit=1_200_000, counterparty_id=CP["Linear"]),
        _line("706000", credit=1_000_000, counterparty_id=CP["Linear"],
              description="Retainer trimestriel"),
        _line("445", credit=200_000, description="TVA collectée 20%"),
    ]),
    ("cash", "2025-12-20", "Recette client — Hostelo (livraison projet)", [
        _line("512", debit=3_000_000, counterparty_id=CP["Hostelo"]),
        _line("706000", credit=2_500_000, counterparty_id=CP["Hostelo"],
              description="Livraison projet entreprise"),
        _line("445", credit=500_000, description="TVA collectée 20%"),
    ]),
    ("cash", "2025-12-31", "Frais bancaires annuels", [
        _line("627", debit=8_500, description="Commissions tenue de compte"),
        _line("512", credit=8_500),
    ]),
    ("cash", "2026-04-05", "Recette client — SNCF (données analytiques)", [
        _line("512", debit=1_800_000, counterparty_id=CP["SNCF"]),
        _line("706000", credit=1_500_000, counterparty_id=CP["SNCF"],
              description="Livrable dataset Q1"),
        _line("445", credit=300_000, description="TVA collectée 20%"),
    ]),

    # --- ACCRUAL basis: open AR + AP cycles ---
    ("accrual", "2025-11-10", "Facture émise INV-2025-004 — Hostelo", [
        _line("411", debit=3_600_000, counterparty_id=CP["Hostelo"]),
        _line("706000", credit=3_000_000, counterparty_id=CP["Hostelo"],
              description="License entreprise annuelle"),
        _line("445", credit=600_000, description="TVA collectée 20%"),
    ]),
    ("accrual", "2025-12-20", "Facture émise INV-2025-005 — SNCF", [
        _line("411", debit=1_440_000, counterparty_id=CP["SNCF"]),
        _line("706000", credit=1_200_000, counterparty_id=CP["SNCF"],
              description="Projet data Q4"),
        _line("445", credit=240_000, description="TVA collectée 20%"),
    ]),
    ("accrual", "2026-04-10", "Facture émise INV-2026-001 — Boulangerie Paul", [
        _line("411", debit=480_000, counterparty_id=CP["Boulangerie Paul"]),
        _line("706000", credit=400_000, counterparty_id=CP["Boulangerie Paul"],
              description="Abonnement plateforme SaaS"),
        _line("445", credit=80_000, description="TVA collectée 20%"),
    ]),
    ("accrual", "2025-04-25", "Facture fournisseur — Anthropic API (avril)", [
        _line("626100", debit=250_000, counterparty_id=CP["Anthropic"],
              description="API monthly burn"),
        _line("4456", debit=50_000, description="TVA déductible 20%"),
        _line("401", credit=300_000, counterparty_id=CP["Anthropic"]),
    ]),
    ("accrual", "2025-05-25", "Règlement fournisseur — Anthropic", [
        _line("401", debit=300_000, counterparty_id=CP["Anthropic"]),
        _line("512", credit=300_000),
    ]),
    ("accrual", "2025-11-30", "Facture fournisseur — OFI (services juridiques)", [
        _line("626200", debit=450_000, counterparty_id=CP["OFI"],
              description="Conseil juridique annuel"),
        _line("4456", debit=90_000, description="TVA déductible 20%"),
        _line("401", credit=540_000, counterparty_id=CP["OFI"]),
    ]),
    ("accrual", "2025-12-15", "Facture fournisseur — Hostelo (license entreprise)", [
        _line("626200", debit=600_000, counterparty_id=CP["Hostelo"],
              description="License Hostelo annuelle"),
        _line("4456", debit=120_000, description="TVA déductible 20%"),
        _line("401", credit=720_000, counterparty_id=CP["Hostelo"]),
    ]),
    ("accrual", "2026-04-15", "Facture fournisseur — agrégat cloud Q1", [
        _line("626100", debit=380_000, counterparty_id=CP["Anthropic"],
              description="Cloud + API Q1 2026"),
        _line("4456", debit=76_000, description="TVA déductible 20%"),
        _line("401", credit=456_000, counterparty_id=CP["Anthropic"]),
    ]),
]


def _validate(entries: list[tuple[str, str, str, list[dict[str, Any]]]]) -> None:
    """Assert SUM(debit) == SUM(credit) per entry. Demo-time invariant."""
    for basis, date, desc, lines in entries:
        dr = sum(int(l.get("debit_cents", 0)) for l in lines)
        cr = sum(int(l.get("credit_cents", 0)) for l in lines)
        if dr != cr:
            raise AssertionError(
                f"unbalanced demo entry {date} '{desc}' "
                f"(basis={basis}): dr={dr} cr={cr}"
            )
        if dr == 0:
            raise AssertionError(f"empty demo entry {date} '{desc}'")


def seed(data_dir: Path) -> dict[str, int]:
    _validate(ENTRIES)
    counts = {"deleted_entries": 0, "deleted_lines": 0,
              "inserted_entries": 0, "inserted_lines": 0}

    acct = data_dir / "accounting.db"
    if not acct.is_file():
        raise SystemExit(f"accounting.db not found under {data_dir}")

    posted_at = datetime.now(timezone.utc).isoformat()

    with closing(sqlite3.connect(str(acct))) as conn:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("BEGIN IMMEDIATE")
        try:
            # Idempotency: nuke prior rows carrying our sentinel pipeline.
            cur = conn.execute(
                "SELECT id FROM journal_entries WHERE source_pipeline = ?",
                (SENTINEL_PIPELINE,),
            )
            prior_ids = [r[0] for r in cur.fetchall()]
            if prior_ids:
                placeholders = ",".join("?" for _ in prior_ids)
                cur = conn.execute(
                    f"DELETE FROM journal_lines WHERE entry_id IN ({placeholders})",
                    prior_ids,
                )
                counts["deleted_lines"] = cur.rowcount
                cur = conn.execute(
                    f"DELETE FROM journal_entries WHERE id IN ({placeholders})",
                    prior_ids,
                )
                counts["deleted_entries"] = cur.rowcount
                # Drop matching decision_traces too (line ids are gone).
                # Use ON DELETE CASCADE if present; otherwise prune by line absence.
                conn.execute(
                    "DELETE FROM decision_traces WHERE line_id NOT IN "
                    "(SELECT id FROM journal_lines)"
                )

            # Insert fresh.
            for basis, entry_date, description, lines in ENTRIES:
                cur = conn.execute(
                    "INSERT INTO journal_entries "
                    "(basis, entry_date, description, source_pipeline, "
                    " source_run_id, status, posted_at) "
                    "VALUES (?, ?, ?, ?, ?, 'posted', ?)",
                    (basis, entry_date, description, SENTINEL_PIPELINE,
                     SENTINEL_RUN_ID, posted_at),
                )
                entry_id = cur.lastrowid
                counts["inserted_entries"] += 1
                for line in lines:
                    cur = conn.execute(
                        "INSERT INTO journal_lines "
                        "(entry_id, account_code, debit_cents, credit_cents, "
                        " counterparty_id, description) "
                        "VALUES (?, ?, ?, ?, ?, ?)",
                        (entry_id, line["account_code"],
                         int(line.get("debit_cents", 0)),
                         int(line.get("credit_cents", 0)),
                         line.get("counterparty_id"),
                         line.get("description")),
                    )
                    line_id = cur.lastrowid
                    counts["inserted_lines"] += 1
                    conn.execute(
                        "INSERT INTO decision_traces "
                        "(line_id, source, rule_id, confidence) "
                        "VALUES (?, 'rule', NULL, 1.0)",
                        (line_id,),
                    )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
    return counts


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--data-dir", type=Path, default=Path("./data"),
        help="Directory containing accounting.db.",
    )
    args = parser.parse_args()
    counts = seed(args.data_dir.resolve())
    print(
        "Balance sheet demo seed complete:\n"
        f"  prior demo entries deleted: {counts['deleted_entries']} "
        f"({counts['deleted_lines']} lines)\n"
        f"  inserted: {counts['inserted_entries']} entries, "
        f"{counts['inserted_lines']} lines"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
