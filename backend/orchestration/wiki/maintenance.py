"""Wiki maintenance — auto-maintained `log.md` and `index.md`.

Source: Karpathy LLM-Wiki convention (`Orchestration/research/llm-wiki.md`
§"Indexing and logging"). Two lightweight side-effects keep the corpus
navigable for humans and future agents:

- `log.md` — append-only changelog. Each upsert appends one entry with
  the prefix `## [YYYY-MM-DD HH:MM] <kind> | <title>` so a `grep "^## \\["`
  walks history.
- `index.md` — table-of-contents grouped by top-level directory. Rebuilt
  on first-creation of any non-meta page. (Pure rev-bumps don't trigger
  a rebuild — only structurally new pages do — to keep the hot path light.)

Both pages are themselves wiki rows (path `log.md` / `index.md`), so the
GET endpoints render them and the loader picks them up. A recursion
guard in each function returns early when the path being maintained is
itself one of these meta-pages, so a rebuild never re-enters.

Soft-fail: any exception is logged (warning) but never propagates — a
maintenance bug must not crash the underlying upsert.
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Any

import aiosqlite


logger = logging.getLogger(__name__)


_META_PATHS: frozenset[str] = frozenset({"index.md", "log.md"})
_LOG_PATH = "log.md"
_INDEX_PATH = "index.md"


def _now_prefix() -> str:
    """ISO-ish timestamp suitable for the `## [YYYY-MM-DD HH:MM]` header."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")


async def _read_latest_body(
    orchestration_db: aiosqlite.Connection,
    *,
    path: str,
) -> tuple[int | None, str]:
    """Return `(page_id, body_md)` for the head revision of `path`.

    `(None, "")` when the page does not exist yet.
    """
    cur = await orchestration_db.execute(
        "SELECT p.id, r.body_md "
        "  FROM wiki_pages p "
        "  JOIN wiki_revisions r ON r.page_id = p.id "
        " WHERE p.path = ? "
        "   AND r.revision_number = ("
        "     SELECT MAX(revision_number) FROM wiki_revisions WHERE page_id = p.id"
        "   )",
        (path,),
    )
    row = await cur.fetchone()
    await cur.close()
    if row is None:
        return None, ""
    return int(row[0]), str(row[1])


async def append_log(
    orchestration_db: aiosqlite.Connection,
    lock: asyncio.Lock,
    *,
    entry: str,
    triggering_path: str | None = None,
) -> None:
    """Append a single entry to `log.md` (creating it if missing).

    `triggering_path` is the path of the page whose upsert triggered
    this call. When that path is itself `log.md` / `index.md` we
    return early to avoid infinite recursion.
    """
    if triggering_path in _META_PATHS:
        return
    try:
        # Lazy import — avoids a writer<->maintenance import cycle at
        # module load time.
        from .writer import upsert_page
        from .schema import WikiFrontmatter

        _, body = await _read_latest_body(orchestration_db, path=_LOG_PATH)
        new_body = (body.rstrip() + "\n\n" + entry.rstrip() + "\n") if body else entry.rstrip() + "\n"
        fm = WikiFrontmatter(applies_to=["meta", "log"], revision=1)
        await upsert_page(
            orchestration_db,
            lock,
            path=_LOG_PATH,
            title="Wiki change log",
            frontmatter=fm,
            body_md=new_body,
            author="agent:wiki_maintenance",
        )
    except Exception:  # noqa: BLE001
        logger.warning("wiki.maintenance.append_log failed", exc_info=True)


async def rebuild_index(
    orchestration_db: aiosqlite.Connection,
    lock: asyncio.Lock,
    *,
    triggering_path: str | None = None,
) -> None:
    """Regenerate `index.md` from current `wiki_pages` head revisions.

    Idempotent: if the rendered body is unchanged, no new revision is
    written. Skips when triggered by an upsert of `index.md` / `log.md`.
    """
    if triggering_path in _META_PATHS:
        return
    try:
        from .writer import upsert_page
        from .schema import WikiFrontmatter

        cur = await orchestration_db.execute(
            "SELECT p.id, p.path, p.title, p.frontmatter_json, "
            "       r.revision_number, r.created_at "
            "  FROM wiki_pages p "
            "  JOIN wiki_revisions r ON r.page_id = p.id "
            " WHERE r.revision_number = ("
            "   SELECT MAX(revision_number) FROM wiki_revisions WHERE page_id = p.id"
            " ) "
            " ORDER BY p.path ASC"
        )
        rows = await cur.fetchall()
        await cur.close()

        groups: dict[str, list[dict[str, Any]]] = {}
        for row in rows:
            _, path, title, fm_json, rev_no, created_at = row
            if path in _META_PATHS:
                continue
            top = path.split("/", 1)[0] if "/" in path else "(root)"
            try:
                fm = json.loads(fm_json) if fm_json else {}
            except json.JSONDecodeError:
                fm = {}
            tags = fm.get("applies_to") or []
            groups.setdefault(top, []).append(
                {
                    "path": path,
                    "title": title,
                    "revision_number": int(rev_no),
                    "created_at": created_at,
                    "tags": ", ".join(str(t) for t in tags) if isinstance(tags, list) else "",
                }
            )

        lines: list[str] = ["# Wiki index", "", f"_Generated {_now_prefix()} UTC_", ""]
        if not groups:
            lines.append("_No pages yet._")
        for top in sorted(groups):
            lines.append(f"## {top}")
            lines.append("")
            lines.append("| path | title | rev | tags |")
            lines.append("|------|-------|-----|------|")
            for entry in groups[top]:
                lines.append(
                    f"| `{entry['path']}` | {entry['title']} | "
                    f"{entry['revision_number']} | {entry['tags']} |"
                )
            lines.append("")
        new_body = "\n".join(lines).rstrip() + "\n"

        _, existing = await _read_latest_body(orchestration_db, path=_INDEX_PATH)
        if existing == new_body:
            return  # idempotent — nothing changed.

        fm = WikiFrontmatter(applies_to=["meta", "index"], revision=1)
        await upsert_page(
            orchestration_db,
            lock,
            path=_INDEX_PATH,
            title="Wiki index",
            frontmatter=fm,
            body_md=new_body,
            author="agent:wiki_maintenance",
        )
    except Exception:  # noqa: BLE001
        logger.warning("wiki.maintenance.rebuild_index failed", exc_info=True)
