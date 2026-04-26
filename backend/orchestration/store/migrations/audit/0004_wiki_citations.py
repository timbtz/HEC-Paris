"""Phase 4.A: cite a wiki revision on every reasoning-agent decision.

Source: PRD-AutonomousCFO §7.3 + §12 Phase 4.A + §14 risk #1.

Adds two nullable columns to `agent_decisions`:
- `wiki_page_id`     INTEGER — logical FK → orchestration.wiki_pages.id
- `wiki_revision_id` INTEGER — logical FK → orchestration.wiki_revisions.id

These are *logical* references — SQLite cannot enforce FKs across
attached databases (we run three separate connections, never ATTACH
across them, per RealMetaPRD §6.5). The columns are integers; correctness
is guaranteed by the wiki_reader tool (which writes them) + the periodic
lint job (which audits orphans).

The Week-1 cut stores only the **first** `(page_id, revision_id)` pair
when an agent cited multiple. A multi-citation join table is deferred —
see PRD §7.3 deferred research item #1.

Index on the pair lets us answer "which decisions cited revision N?"
in O(log) for the lint / replay tooling.
"""
from __future__ import annotations

import aiosqlite


async def _has_column(conn: aiosqlite.Connection, table: str, column: str) -> bool:
    cur = await conn.execute(f"PRAGMA table_info({table})")
    rows = await cur.fetchall()
    await cur.close()
    return any(r[1] == column for r in rows)


async def up(conn: aiosqlite.Connection) -> None:
    if not await _has_column(conn, "agent_decisions", "wiki_page_id"):
        await conn.execute(
            "ALTER TABLE agent_decisions ADD COLUMN wiki_page_id INTEGER"
        )
    if not await _has_column(conn, "agent_decisions", "wiki_revision_id"):
        await conn.execute(
            "ALTER TABLE agent_decisions ADD COLUMN wiki_revision_id INTEGER"
        )
    await conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_decisions_wiki "
        "ON agent_decisions(wiki_page_id, wiki_revision_id)"
    )
