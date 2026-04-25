"""Wire the three demo employees to their per-employee virtual Swan IBAN.

Both `swan_iban` and `swan_account_id` are UNIQUE in `audit.employees`,
so each employee needs distinct identifiers. Conceptually all three
operate on the same company account `acc_demo_company` (which is what
`accounting/0006_demo_swan_transactions.py` populates), but at the
employee-attribution layer they each carry a virtual sub-account id
(`acc_demo_company__tim`, etc.) that survives the UNIQUE constraint.

IBAN literals live in the `FR76…03xx` range to avoid colliding with the
supplier (`01xx`) and customer (`02xx`) ranges from
`accounting/0005_demo_counterparties.py`.
"""
from __future__ import annotations

import aiosqlite


_EMPLOYEE_LINKS: tuple[tuple[str, str, str], ...] = (
    # (email, swan_iban, swan_account_id)
    ("tim@hec.example",   "FR7610278060610001020480301", "acc_demo_company__tim"),
    ("marie@hec.example", "FR7610278060610001020480302", "acc_demo_company__marie"),
    ("paul@hec.example",  "FR7610278060610001020480303", "acc_demo_company__paul"),
)


async def up(conn: aiosqlite.Connection) -> None:
    # UPDATE-only: the rows already exist from 0002_seed_employees.
    for email, iban, account_id in _EMPLOYEE_LINKS:
        await conn.execute(
            "UPDATE employees "
            "SET swan_iban = ?, swan_account_id = ? "
            "WHERE email = ?",
            (iban, account_id, email),
        )
