"""Wiki upsert — every edit creates a new immutable revision.

Source: PRD-AutonomousCFO §7.3 ("Wiki revisions are tracked; every agent
decision cites `(page_id, revision_id)`"). All writes go through
`store.writes.write_tx` (CLAUDE.md hard rule — single chokepoint).

Contract:
- New page → INSERT into `wiki_pages` + INSERT revision_number=1.
- Existing page → UPDATE `wiki_pages.{title,body_md,frontmatter_json,
  updated_at}` + INSERT revision_number = max+1 with `parent_revision_id`
  pointing at the previous head.
- Returns `(page_id, revision_id)`.
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from typing import Any

import aiosqlite

from .schema import WikiFrontmatter
from ..store.writes import write_tx


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


async def upsert_page(
    orchestration_db: aiosqlite.Connection,
    lock: asyncio.Lock,
    *,
    path: str,
    title: str,
    frontmatter: WikiFrontmatter | dict[str, Any],
    body_md: str,
    author: str | None,
) -> tuple[int, int]:
    """Insert or update a wiki page and append a revision row.

    Returns `(page_id, revision_id)` — caller threads `revision_id` into
    `prompt_hash` and the audit row via the `wiki_reader` tool.

    Side-effect: after the commit, appends a `log.md` entry and (when the
    page is structurally new) regenerates `index.md`. Both are wrapped
    inside the maintenance module's own try/except, so a maintenance bug
    cannot crash the actual edit.
    """
    if isinstance(frontmatter, WikiFrontmatter):
        fm_dict = frontmatter.to_dict()
    else:
        fm_dict = frontmatter
    fm_json = json.dumps(fm_dict, separators=(",", ":"), sort_keys=True)
    now = _iso_now()

    async with write_tx(orchestration_db, lock) as conn:
        # 1. Page row — INSERT or UPDATE.
        cur = await conn.execute(
            "SELECT id FROM wiki_pages WHERE path = ?",
            (path,),
        )
        row = await cur.fetchone()
        await cur.close()

        if row is None:
            cur = await conn.execute(
                "INSERT INTO wiki_pages "
                "(path, title, frontmatter_json, body_md, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (path, title, fm_json, body_md, now, now),
            )
            page_id = cur.lastrowid
            if page_id is None:  # pragma: no cover - defensive
                raise RuntimeError("wiki_pages insert returned no rowid")
        else:
            page_id = int(row[0])
            await conn.execute(
                "UPDATE wiki_pages "
                "   SET title = ?, frontmatter_json = ?, body_md = ?, updated_at = ? "
                " WHERE id = ?",
                (title, fm_json, body_md, now, page_id),
            )

        # 2. Compute next revision_number + parent_revision_id atomically.
        cur = await conn.execute(
            "SELECT id, revision_number FROM wiki_revisions "
            " WHERE page_id = ? "
            " ORDER BY revision_number DESC LIMIT 1",
            (page_id,),
        )
        prev = await cur.fetchone()
        await cur.close()

        if prev is None:
            next_rev_no = 1
            parent_rev_id: int | None = None
        else:
            parent_rev_id = int(prev[0])
            next_rev_no = int(prev[1]) + 1

        cur = await conn.execute(
            "INSERT INTO wiki_revisions "
            "(page_id, revision_number, body_md, frontmatter_json, "
            " author, created_at, parent_revision_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (page_id, next_rev_no, body_md, fm_json, author, now, parent_rev_id),
        )
        revision_id = cur.lastrowid
        if revision_id is None:  # pragma: no cover - defensive
            raise RuntimeError("wiki_revisions insert returned no rowid")

    # Maintenance side-effects — outside the write_tx so the meta-page
    # upserts acquire the lock fresh. Both calls are soft-fail.
    from . import maintenance

    is_new_page = parent_rev_id is None
    log_kind = "ingest" if is_new_page else "update"
    log_entry = (
        f"## [{maintenance._now_prefix()}] {log_kind} | {title}\n"
        f"- `{path}` rev {next_rev_no} (page_id={int(page_id)}, "
        f"revision_id={int(revision_id)}); author={author or '<unknown>'}"
    )
    await maintenance.append_log(
        orchestration_db,
        lock,
        entry=log_entry,
        triggering_path=path,
    )
    if is_new_page:
        await maintenance.rebuild_index(
            orchestration_db,
            lock,
            triggering_path=path,
        )

    return int(page_id), int(revision_id)
