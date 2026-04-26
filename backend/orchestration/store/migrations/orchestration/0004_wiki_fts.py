"""Phase 4.A wiki search: contentless FTS5 virtual table over wiki_revisions.body_md.

Source: PRD-AutonomousCFO §7.3 (wiki search) + Karpathy LLM-wiki query op.
SQLite FTS5 docs https://www.sqlite.org/fts5.html ("Contentless FTS5 Tables"
+ "The bm25() Function").

Why contentless (`content=''`):
- The body_md is already authoritative on `wiki_revisions`. Duplicating it
  in `wiki_fts.body_md` would double the on-disk footprint.
- Triggers below keep the FTS5 index coherent on every revision INSERT /
  UPDATE / DELETE, including the `'delete'` shadow row on UPDATE/DELETE
  required by contentless tables.

The search tool (`tools/wiki_search.py`) joins wiki_fts × wiki_revisions ×
wiki_pages and pins to the latest revision per page so query hits never
cite a superseded revision.
"""
from __future__ import annotations

import aiosqlite


async def up(conn: aiosqlite.Connection) -> None:
    await conn.execute(
        "CREATE VIRTUAL TABLE IF NOT EXISTS wiki_fts USING fts5("
        "body_md,"
        " content='',"
        " tokenize='porter unicode61'"
        ")"
    )

    # Triggers — keep wiki_fts in lockstep with wiki_revisions. Contentless
    # tables require an explicit ('delete', rowid, body) shadow row on every
    # UPDATE/DELETE so the inverted index can shed the old tokens.
    await conn.execute(
        "CREATE TRIGGER IF NOT EXISTS trg_wiki_fts_insert "
        "AFTER INSERT ON wiki_revisions BEGIN "
        " INSERT INTO wiki_fts(rowid, body_md) VALUES (NEW.id, NEW.body_md); "
        "END"
    )
    await conn.execute(
        "CREATE TRIGGER IF NOT EXISTS trg_wiki_fts_update "
        "AFTER UPDATE ON wiki_revisions BEGIN "
        " INSERT INTO wiki_fts(wiki_fts, rowid, body_md) "
        "  VALUES ('delete', OLD.id, OLD.body_md); "
        " INSERT INTO wiki_fts(rowid, body_md) VALUES (NEW.id, NEW.body_md); "
        "END"
    )
    await conn.execute(
        "CREATE TRIGGER IF NOT EXISTS trg_wiki_fts_delete "
        "AFTER DELETE ON wiki_revisions BEGIN "
        " INSERT INTO wiki_fts(wiki_fts, rowid, body_md) "
        "  VALUES ('delete', OLD.id, OLD.body_md); "
        "END"
    )

    # Backfill — every existing revision becomes searchable. INSERT OR IGNORE
    # against rowid keeps re-applications idempotent if the migration is
    # somehow run twice on the same DB.
    await conn.execute(
        "INSERT INTO wiki_fts(rowid, body_md) "
        "SELECT id, body_md FROM wiki_revisions "
        "WHERE id NOT IN (SELECT rowid FROM wiki_fts)"
    )
