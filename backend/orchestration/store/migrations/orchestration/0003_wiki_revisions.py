"""Phase 4.A: wiki_revisions — append-only revision log per page.

Source: PRD-AutonomousCFO §7.3 + §12 Phase 4.A.

Every `upsert_page` writes a new row here with a monotonic `revision_number`
scoped per page. The `(page_id, revision_id)` pair is what `prompt_hash.py`
incorporates so a wiki edit invalidates exactly the agents that read that
page.

Schema:
- `id` PK
- `page_id` FK → `wiki_pages(id)`
- `revision_number` — monotonic per page (1, 2, 3, …)
- `body_md` — body at this revision
- `frontmatter_json` — frontmatter at this revision; CHECK json_valid
- `author` — free-form author identifier (email or actor name)
- `created_at` — ISO timestamp
- `parent_revision_id` — FK → `wiki_revisions(id)`, nullable for the first
  revision of a page

Constraints:
- UNIQUE (page_id, revision_number)
- INDEX on (page_id, created_at DESC) — for the "most recent edit" query
"""
from __future__ import annotations

import aiosqlite


async def up(conn: aiosqlite.Connection) -> None:
    await conn.execute(
        "CREATE TABLE IF NOT EXISTS wiki_revisions ("
        " id INTEGER PRIMARY KEY,"
        " page_id INTEGER NOT NULL REFERENCES wiki_pages(id),"
        " revision_number INTEGER NOT NULL,"
        " body_md TEXT NOT NULL,"
        " frontmatter_json TEXT NOT NULL,"
        " author TEXT,"
        " created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,"
        " parent_revision_id INTEGER REFERENCES wiki_revisions(id),"
        " UNIQUE (page_id, revision_number),"
        " CHECK (json_valid(frontmatter_json))"
        ") STRICT"
    )
    await conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_wiki_revisions_recent "
        "ON wiki_revisions(page_id, created_at DESC)"
    )
