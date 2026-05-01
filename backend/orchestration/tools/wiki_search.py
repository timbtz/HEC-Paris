"""wiki_search — BM25 free-text search over the latest revision of every wiki page.

Source: PRD-AutonomousCFO §7.3 (Living Rule Wiki) + Karpathy LLM-wiki "query"
operation (`Orchestration/research/llm-wiki.md` §"Ingest / Query / Lint").

Where `tools.wiki_reader` looks up pages by frontmatter tags (set
intersection on `applies_to`), this tool walks the FTS5 index built by
migration 0004 — useful when the right tag isn't known up front.

Tool contract (mirrors `wiki_reader`):
- Input  : query: str, limit: int = 10
- Output : {"hits": [{page_id, revision_id, revision_number, path, title,
                      snippet, score}]}

The query is passed as a parameter to the FTS5 MATCH clause, so SQL
injection is not possible; FTS5's own query language (operators like
`AND`, `NEAR(...)`) is the only thing the user controls.

Latest-revision pinning: the JOIN constrains to revisions whose
`revision_number` matches `MAX(revision_number)` per page, so a search hit
on a superseded revision is dropped. (FTS5 retains old rows only until
the AFTER UPDATE trigger has emitted the `'delete'` shadow row, which is
synchronous within the upsert transaction.)
"""
from __future__ import annotations

from typing import Any

from ..context import FingentContext


_SEARCH_SQL = (
    "SELECT r.id AS revision_id,"
    "       r.page_id,"
    "       r.revision_number,"
    "       p.path,"
    "       p.title,"
    "       snippet(wiki_fts, 0, '«', '»', '…', 16) AS snippet,"
    "       bm25(wiki_fts) AS score "
    "FROM wiki_fts "
    "JOIN wiki_revisions r ON r.id = wiki_fts.rowid "
    "JOIN wiki_pages p ON p.id = r.page_id "
    "WHERE wiki_fts MATCH ? "
    "  AND r.revision_number = ("
    "      SELECT MAX(rr.revision_number) FROM wiki_revisions rr "
    "       WHERE rr.page_id = r.page_id"
    "  ) "
    "ORDER BY bm25(wiki_fts) "
    "LIMIT ?"
)


async def fetch(
    ctx: FingentContext,
    *,
    query: str,
    limit: int = 10,
) -> dict[str, Any]:
    """Run a BM25 search; stash citation pairs onto `ctx.metadata`.

    An empty / whitespace-only query returns `{"hits": []}` without
    touching the DB — FTS5 raises on empty MATCH expressions.
    """
    q = (query or "").strip()
    if not q:
        return {"hits": []}

    safe_limit = max(1, min(int(limit), 50))
    cur = await ctx.store.orchestration.execute(_SEARCH_SQL, (q, safe_limit))
    rows = await cur.fetchall()
    await cur.close()

    hits: list[dict[str, Any]] = []
    refs: list[tuple[int, int]] = []
    for row in rows:
        revision_id = int(row[0])
        page_id = int(row[1])
        hits.append(
            {
                "page_id": page_id,
                "revision_id": revision_id,
                "revision_number": int(row[2]),
                "path": row[3],
                "title": row[4],
                "snippet": row[5],
                "score": float(row[6]),
            }
        )
        refs.append((page_id, revision_id))

    # Mirror wiki_reader: extend ctx.metadata["wiki_references"] so a
    # downstream agent that read these results can credit the citations.
    existing = ctx.metadata.get("wiki_references")
    if isinstance(existing, list):
        existing.extend(refs)
    else:
        ctx.metadata["wiki_references"] = list(refs)

    return {"hits": hits}


async def run(ctx: FingentContext) -> dict[str, Any]:
    """Pipeline-node entry point.

    Reads `query` from `ctx.trigger_payload["wiki_query"]` or
    `ctx.metadata["wiki_query"]`. Optional integer `limit` from the same
    sources falls back to 10. A missing query is *not* an error — the
    node returns `{"hits": []}` so a wiki_search node can be wired
    eagerly into a pipeline without a strict trigger contract.
    """
    payload = ctx.trigger_payload if isinstance(ctx.trigger_payload, dict) else {}
    metadata = ctx.metadata if isinstance(ctx.metadata, dict) else {}
    query = payload.get("wiki_query") or metadata.get("wiki_query") or ""
    limit_raw = payload.get("wiki_limit") or metadata.get("wiki_limit") or 10
    try:
        limit = int(limit_raw)
    except (TypeError, ValueError):
        limit = 10
    return await fetch(ctx, query=str(query), limit=limit)
