"""Wiki page + revision read endpoints.

Source: PRD-AutonomousCFO §7.3 + §7.4 (DAG-viewer drilldown). The DAG
node trace drawer in `frontend-lovable/src/components/fingent/NodeTraceDrawer.tsx`
hits the revision-pinned read when the operator clicks a citation row;
the Wiki tab in `frontend-lovable/src/pages/WikiPage.tsx` hits the list
+ head endpoints to render the policy corpus.

Routes:

  GET /wiki/pages
      → {items: [{page_id, path, title, head_revision_id,
                  head_revision_number, frontmatter, updated_at}]}

  GET /wiki/pages/{page_id}
      → head revision body for the page (mirrors the revision-pinned
        shape so the frontend can use one renderer)

  GET /wiki/pages/{page_id}/revisions
      → {items: [{revision_id, revision_number, author, created_at}]}

  GET /wiki/pages/{page_id}/revisions/{revision_id}
      → {page_id, revision_id, revision_number, path, title,
         body_md, frontmatter, created_at}

SQL-only — no agent involvement. The body shipped from the revision
endpoint is the SNAPSHOT at `revision_id`, not whatever the head row
currently holds; this is what makes the DAG-viewer time-travel-safe
(the citation pinned to revision N keeps reading the v-N body even
after the page is edited).
"""
from __future__ import annotations

import json
import re
from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field

from backend.orchestration.store.writes import write_tx
from backend.orchestration.wiki import upsert_page
from backend.orchestration.wiki.schema import WikiFrontmatter


router = APIRouter(prefix="/wiki")


class _WikiPageCreate(BaseModel):
    path: str = Field(..., min_length=1)
    title: str = Field(..., min_length=1)
    body_md: str = ""
    frontmatter: dict[str, Any] = Field(default_factory=dict)


class _WikiPageUpdate(BaseModel):
    title: str = Field(..., min_length=1)
    body_md: str = ""
    frontmatter: dict[str, Any] = Field(default_factory=dict)


def _coerce_frontmatter(raw: dict[str, Any]) -> WikiFrontmatter:
    """Validate the request body's frontmatter; raise HTTPException(400) on failure."""
    cleaned = {k: v for k, v in raw.items() if v is not None}
    try:
        return WikiFrontmatter.from_dict(cleaned)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _author_from_request(request: Request) -> str:
    """Pull the author string from `x-fingent-author`; default to `cfo`.

    Auth integration is intentionally out of scope here — the header
    contract is the seam for the future Lovable login flow.
    """
    return request.headers.get("x-fingent-author") or "cfo"


def _parse_frontmatter(raw: Any) -> Any:
    if raw is None:
        return None
    if isinstance(raw, (dict, list)):
        return raw
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return None


_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


@router.get("/snapshot")
async def wiki_snapshot(
    request: Request,
    as_of: Annotated[str | None, Query()] = None,
) -> Response:
    """Concatenated markdown bundle of all current wiki pages.

    Without `as_of`: each page's head revision (current state).
    With `as_of=YYYY-MM-DD`: the latest revision per page where
    `created_at <= as_of` (pages with no qualifying revision are skipped).

    Pages are emitted ordered by `path ASC`, separated by `---` lines so
    a downstream renderer can split the bundle on the divider.
    """
    if as_of is not None and not _DATE_RE.match(as_of):
        raise HTTPException(
            status_code=422, detail=f"as_of must be YYYY-MM-DD; got {as_of!r}",
        )

    store = request.app.state.store

    if as_of is None:
        cur = await store.orchestration.execute(
            "SELECT p.path, p.title, r.body_md, r.created_at "
            "  FROM wiki_pages p "
            "  JOIN wiki_revisions r ON r.page_id = p.id "
            " WHERE r.revision_number = ("
            "   SELECT MAX(revision_number) FROM wiki_revisions WHERE page_id = p.id"
            " ) "
            " ORDER BY p.path ASC"
        )
    else:
        # Latest revision per page with created_at <= as_of (end-of-day cap).
        cap = f"{as_of}T99:99:99"
        cur = await store.orchestration.execute(
            "SELECT p.path, p.title, r.body_md, r.created_at "
            "  FROM wiki_pages p "
            "  JOIN wiki_revisions r ON r.page_id = p.id "
            " WHERE r.created_at < ? "
            "   AND r.revision_number = ("
            "     SELECT MAX(revision_number) FROM wiki_revisions "
            "      WHERE page_id = p.id AND created_at < ?"
            "   ) "
            " ORDER BY p.path ASC",
            (cap, cap),
        )
    rows = await cur.fetchall()
    await cur.close()

    chunks: list[str] = []
    for row in rows:
        path = str(row[0])
        title = str(row[1])
        body_md = str(row[2])
        created_at = row[3]
        chunks.append(
            f"# {title}\n"
            f"_path: {path}_\n"
            f"_updated_at: {created_at}_\n\n"
            f"{body_md}\n"
        )

    bundle = "\n---\n".join(chunks)
    body = bundle.encode("utf-8")
    suffix = f"_{as_of}" if as_of else ""
    headers = {
        "Content-Disposition": f'attachment; filename="wiki_snapshot{suffix}.md"',
        "X-Content-Type-Options": "nosniff",
    }
    return Response(
        content=body, media_type="text/markdown; charset=utf-8", headers=headers,
    )


@router.get("/pages")
async def list_wiki_pages(request: Request) -> dict[str, Any]:
    """List every wiki page with its head revision metadata.

    Used by the Wiki tab in the Lovable frontend (PRD-AutonomousCFO
    §7.3) — the operator gets a left-rail list of policies, ordered by
    `path` so the deterministic prompt-byte order matches what agents
    actually see at the top of the corpus.
    """
    store = request.app.state.store

    cur = await store.orchestration.execute(
        "SELECT p.id, p.path, p.title, p.frontmatter_json, p.updated_at, "
        "       r.id, r.revision_number, r.created_at "
        "  FROM wiki_pages p "
        "  JOIN wiki_revisions r ON r.page_id = p.id "
        " WHERE r.revision_number = ("
        "   SELECT MAX(revision_number) FROM wiki_revisions WHERE page_id = p.id"
        " ) "
        " ORDER BY p.path ASC"
    )
    rows = await cur.fetchall()
    await cur.close()

    items = [
        {
            "page_id": int(row[0]),
            "path": str(row[1]),
            "title": str(row[2]),
            "frontmatter": _parse_frontmatter(row[3]),
            "updated_at": row[4],
            "head_revision_id": int(row[5]),
            "head_revision_number": int(row[6]),
            "head_revision_created_at": row[7],
        }
        for row in rows
    ]
    return {"items": items}


@router.get("/pages/{page_id}")
async def get_wiki_page_head(
    page_id: int,
    request: Request,
) -> dict[str, Any]:
    """Return the head revision of one wiki page.

    The shape matches `get_wiki_revision` so the frontend renderer is
    one component; only the revision selection differs.
    """
    store = request.app.state.store

    cur = await store.orchestration.execute(
        "SELECT p.id, r.id, r.revision_number, p.path, p.title, "
        "       r.body_md, r.frontmatter_json, r.created_at "
        "  FROM wiki_pages p "
        "  JOIN wiki_revisions r ON r.page_id = p.id "
        " WHERE p.id = ? AND r.revision_number = ("
        "   SELECT MAX(revision_number) FROM wiki_revisions WHERE page_id = p.id"
        " )",
        (page_id,),
    )
    row = await cur.fetchone()
    await cur.close()
    if row is None:
        raise HTTPException(
            status_code=404, detail=f"wiki page #{page_id} not found"
        )

    return {
        "page_id": int(row[0]),
        "revision_id": int(row[1]),
        "revision_number": int(row[2]),
        "path": str(row[3]),
        "title": str(row[4]),
        "body_md": str(row[5]),
        "frontmatter": _parse_frontmatter(row[6]),
        "created_at": row[7],
    }


@router.get("/pages/{page_id}/revisions")
async def list_wiki_revisions(
    page_id: int,
    request: Request,
) -> dict[str, Any]:
    """List the revision history for a page (newest first)."""
    store = request.app.state.store

    cur = await store.orchestration.execute(
        "SELECT id FROM wiki_pages WHERE id = ?", (page_id,)
    )
    page_row = await cur.fetchone()
    await cur.close()
    if page_row is None:
        raise HTTPException(
            status_code=404, detail=f"wiki page #{page_id} not found"
        )

    cur = await store.orchestration.execute(
        "SELECT id, revision_number, author, created_at, parent_revision_id "
        "  FROM wiki_revisions "
        " WHERE page_id = ? "
        " ORDER BY revision_number DESC",
        (page_id,),
    )
    rows = await cur.fetchall()
    await cur.close()

    items = [
        {
            "revision_id": int(row[0]),
            "revision_number": int(row[1]),
            "author": row[2],
            "created_at": row[3],
            "parent_revision_id": int(row[4]) if row[4] is not None else None,
        }
        for row in rows
    ]
    return {"items": items}


@router.post("/pages")
async def create_wiki_page(
    body: _WikiPageCreate,
    request: Request,
) -> dict[str, Any]:
    """Create a new wiki page (revision 1).

    Body shape mirrors the GET response so the frontend round-trips
    cleanly. Frontmatter is validated via `WikiFrontmatter.from_dict`;
    a malformed payload (`applies_to` not a list, `revision` not int,
    etc.) returns 400 with the validation message.

    The path is the natural key — POSTing the same path twice still
    succeeds, but writes a *new* revision rather than failing. This
    matches `wiki.writer.upsert_page`'s contract; if a callsite needs
    "fail on conflict" semantics, send a PUT instead.
    """
    store = request.app.state.store
    fm = _coerce_frontmatter(body.frontmatter)
    author = _author_from_request(request)

    page_id, revision_id = await upsert_page(
        store.orchestration,
        store.orchestration_lock,
        path=body.path,
        title=body.title,
        frontmatter=fm,
        body_md=body.body_md,
        author=author,
    )
    return await get_wiki_page_head(page_id, request)


@router.put("/pages/{page_id}")
async def update_wiki_page(
    page_id: int,
    body: _WikiPageUpdate,
    request: Request,
) -> dict[str, Any]:
    """Write a new revision for an existing page.

    The path is looked up from `wiki_pages` (404 if missing) so the
    client only needs `page_id`. Title + frontmatter + body all live on
    the new revision; the head row in `wiki_pages` is also updated to
    keep the head-row reads cheap.
    """
    store = request.app.state.store

    cur = await store.orchestration.execute(
        "SELECT path FROM wiki_pages WHERE id = ?", (page_id,)
    )
    row = await cur.fetchone()
    await cur.close()
    if row is None:
        raise HTTPException(
            status_code=404, detail=f"wiki page #{page_id} not found"
        )
    path = str(row[0])

    fm = _coerce_frontmatter(body.frontmatter)
    author = _author_from_request(request)

    await upsert_page(
        store.orchestration,
        store.orchestration_lock,
        path=path,
        title=body.title,
        frontmatter=fm,
        body_md=body.body_md,
        author=author,
    )
    return await get_wiki_page_head(page_id, request)


@router.get("/ratifications")
async def list_wiki_ratifications(
    request: Request,
    status: Annotated[str, Query()] = "pending",
) -> dict[str, Any]:
    """List `review_queue` rows of kind=wiki_rule_change.

    The post-mortem agent files these when it proposes a change to an
    existing `policies/*` page; the CFO ratifies them in one click via
    `POST /wiki/ratifications/{review_id}/approve`. `status=pending`
    (default) returns rows with `resolved_at IS NULL`; `status=resolved`
    returns the rest.
    """
    store = request.app.state.store
    if status == "pending":
        where = "resolved_at IS NULL"
    elif status == "resolved":
        where = "resolved_at IS NOT NULL"
    else:
        raise HTTPException(
            status_code=422, detail="status must be 'pending' or 'resolved'",
        )
    cur = await store.accounting.execute(
        f"SELECT id, kind, confidence, reason, created_at, "
        f"       resolved_at, resolved_by "
        f"  FROM review_queue "
        f" WHERE kind = 'wiki_rule_change' AND {where} "
        f" ORDER BY id DESC"
    )
    rows = await cur.fetchall()
    await cur.close()
    items = [
        {
            "review_id": int(r[0]),
            "kind": r[1],
            "confidence": r[2],
            "reason": r[3],
            "created_at": r[4],
            "resolved_at": r[5],
            "resolved_by": r[6],
        }
        for r in rows
    ]
    return {"items": items}


@router.post("/ratifications/{review_id}/approve")
async def approve_wiki_ratification(
    review_id: int,
    request: Request,
) -> dict[str, Any]:
    """Mark a wiki_rule_change review_queue row as resolved.

    The actual wiki edit can be made separately via `PUT /wiki/pages/{id}`
    — this endpoint just closes the loop on the agent's proposal so the
    pending-ratifications panel doesn't grow unbounded. The CFO's email
    (from `x-fingent-author`) is recorded as `resolved_by`.
    """
    store = request.app.state.store
    author = _author_from_request(request)
    async with write_tx(store.accounting, store.accounting_lock) as conn:
        cur = await conn.execute(
            "SELECT resolved_at FROM review_queue "
            "WHERE id = ? AND kind = 'wiki_rule_change'",
            (review_id,),
        )
        row = await cur.fetchone()
        await cur.close()
        if row is None:
            raise HTTPException(
                status_code=404,
                detail=f"wiki_rule_change review #{review_id} not found",
            )
        if row[0] is not None:
            raise HTTPException(
                status_code=409,
                detail=f"review #{review_id} already resolved at {row[0]}",
            )
        await conn.execute(
            "UPDATE review_queue "
            "SET resolved_at = CURRENT_TIMESTAMP, resolved_by = NULL "
            "WHERE id = ?",
            (review_id,),
        )
    return {"review_id": review_id, "resolved_by": author, "status": "approved"}


@router.get("/pages/{page_id}/revisions/{revision_id}")
async def get_wiki_revision(
    page_id: int,
    revision_id: int,
    request: Request,
) -> dict[str, Any]:
    """Return one wiki page at a specific revision.

    404 if the (page_id, revision_id) pair doesn't resolve — either the
    page was deleted, the revision doesn't belong to that page, or the
    IDs were never valid.
    """
    store = request.app.state.store

    cur = await store.orchestration.execute(
        "SELECT p.id, r.id, r.revision_number, p.path, p.title, "
        "       r.body_md, r.frontmatter_json, r.created_at "
        "  FROM wiki_pages p "
        "  JOIN wiki_revisions r ON r.page_id = p.id "
        " WHERE p.id = ? AND r.id = ?",
        (page_id, revision_id),
    )
    row = await cur.fetchone()
    await cur.close()
    if row is None:
        raise HTTPException(
            status_code=404,
            detail=(
                f"wiki page #{page_id} revision #{revision_id} not found"
            ),
        )

    return {
        "page_id": int(row[0]),
        "revision_id": int(row[1]),
        "revision_number": int(row[2]),
        "path": str(row[3]),
        "title": str(row[4]),
        "body_md": str(row[5]),
        "frontmatter": _parse_frontmatter(row[6]),
        "created_at": row[7],
    }
