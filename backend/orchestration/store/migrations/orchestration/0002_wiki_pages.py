"""Phase 4.A: wiki_pages table — the persistent Living Rule Wiki layer.

Source: PRD-AutonomousCFO §7.3 + §12 Phase 4.A. Implements Karpathy's
LLM-Wiki pattern (`Orchestration/research/llm-wiki.md`): markdown pages
read verbatim by reasoning agents as system-prompt input.

Schema:
- `id` PK
- `path` UNIQUE — wiki-relative path, e.g. `policies/fr-bewirtung.md`
- `title` — page title (extracted from frontmatter or first heading)
- `frontmatter_json` — JSON-serialized YAML frontmatter; CHECK json_valid
- `body_md` — page body (without the frontmatter block)
- `created_at` / `updated_at` — ISO timestamps

Revisions live in a sibling table `wiki_revisions` (migration 0003).
The "latest revision" per page is whatever `wiki_revisions.revision_number`
maxes out at for that `page_id`; this table only carries the head copy
for fast tag-routing reads.
"""
from __future__ import annotations

import aiosqlite


async def up(conn: aiosqlite.Connection) -> None:
    await conn.execute(
        "CREATE TABLE IF NOT EXISTS wiki_pages ("
        " id INTEGER PRIMARY KEY,"
        " path TEXT NOT NULL UNIQUE,"
        " title TEXT NOT NULL,"
        " frontmatter_json TEXT NOT NULL,"
        " body_md TEXT NOT NULL,"
        " created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,"
        " updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,"
        " CHECK (json_valid(frontmatter_json))"
        ") STRICT"
    )
    await conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_wiki_pages_path "
        "ON wiki_pages(path)"
    )
