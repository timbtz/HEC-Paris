"""Seed budget envelopes for the 3 demo employees + the company.

4 scopes (Tim id=1, Marie id=2, Paul id=3, company scope_id=NULL)
× 5 categories (food, travel, saas, ai_tokens, leasing)
× 3 periods (2026-02, 2026-03, 2026-04)
= 60 rows.

Caps in integer cents (RealMetaPRD: money is always cents):
  ai_tokens =  €200, food = €150, travel = €500, saas = €300, leasing = €800.
soft_threshold_pct=80 (the warning trigger before the hard cap).

Idempotent via NOT EXISTS guard — `budget_envelopes` has no UNIQUE
constraint covering (scope_kind, scope_id, category, period).
"""
from __future__ import annotations

import aiosqlite


_CAPS_CENTS: dict[str, int] = {
    "ai_tokens": 20000_00,
    "food":      15000_00,
    "travel":    50000_00,
    "saas":      30000_00,
    "leasing":   80000_00,
}

_PERIODS: tuple[str, ...] = ("2026-02", "2026-03", "2026-04")
_CATEGORIES: tuple[str, ...] = ("food", "travel", "saas", "ai_tokens", "leasing")

# (scope_kind, scope_id)
_SCOPES: tuple[tuple[str, int | None], ...] = (
    ("employee", 1),
    ("employee", 2),
    ("employee", 3),
    ("company",  None),
)


async def up(conn: aiosqlite.Connection) -> None:
    for scope_kind, scope_id in _SCOPES:
        for category in _CATEGORIES:
            cap = _CAPS_CENTS[category]
            for period in _PERIODS:
                # NULL-safe equality: `scope_id IS ?` works for both NULL and ints.
                await conn.execute(
                    "INSERT INTO budget_envelopes "
                    "(scope_kind, scope_id, category, period, cap_cents, "
                    " soft_threshold_pct) "
                    "SELECT ?, ?, ?, ?, ?, 80 "
                    "WHERE NOT EXISTS ("
                    "  SELECT 1 FROM budget_envelopes "
                    "  WHERE scope_kind = ? AND scope_id IS ? "
                    "    AND category = ? AND period = ?"
                    ")",
                    (
                        scope_kind, scope_id, category, period, cap,
                        scope_kind, scope_id, category, period,
                    ),
                )
