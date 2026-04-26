"""Tag-routed wiki page loader.

Source: PRD-AutonomousCFO §7.3. The MVP routing is a frontmatter-tag
match (`applies_to` × incoming agent tags) plus an optional jurisdiction
filter. `qmd` BM25/vector retrieval is deferred (PRD §13).

Returns the **latest revision** per matching page. The ordering is
deterministic (`ORDER BY path ASC`) so the agent's prompt is byte-stable
across runs that read the same set of pages.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any

import aiosqlite

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class WikiPage:
    """A single wiki page at its current head revision."""
    page_id: int
    path: str
    title: str
    revision_id: int
    revision_number: int
    body_md: str
    applies_to: list[str]


async def load_pages_for_tags(
    orchestration_db: aiosqlite.Connection,
    tags: list[str],
    jurisdiction: str | None = None,
) -> list[WikiPage]:
    """Return every wiki page whose `applies_to` intersects `tags`.

    Args:
      orchestration_db: open `aiosqlite.Connection` to orchestration.db.
      tags: incoming routing tags from the agent (e.g. ['gl_accounts',
        'classification']). Matching is set-intersection on the
        frontmatter's `applies_to` list — any tag overlap counts.
      jurisdiction: when set, exclude pages whose frontmatter
        `jurisdictions` is non-empty AND does not contain this code.
        Pages with no `jurisdictions` field are jurisdiction-agnostic
        and always included.

    Result is ordered by `path` ASC for byte-stable prompts.
    """
    if not tags:
        return []

    # Pull every page + its head revision in one go.
    cur = await orchestration_db.execute(
        "SELECT p.id, p.path, p.title, p.frontmatter_json, "
        "       r.id, r.revision_number, r.body_md "
        "  FROM wiki_pages p "
        "  JOIN wiki_revisions r ON r.page_id = p.id "
        " WHERE r.revision_number = ("
        "   SELECT MAX(revision_number) FROM wiki_revisions WHERE page_id = p.id"
        " ) "
        " ORDER BY p.path ASC"
    )
    rows = await cur.fetchall()
    await cur.close()

    tag_set = {t for t in tags if t}
    matches: list[WikiPage] = []
    for row in rows:
        page_id, path, title, fm_json, rev_id, rev_no, body_md = row
        try:
            fm = json.loads(fm_json) if fm_json else {}
        except json.JSONDecodeError:
            # Defensive — the CHECK json_valid constraint should prevent
            # this, but a corrupted DB shouldn't crash the loader.
            continue

        applies_to_raw = fm.get("applies_to") or []
        if not isinstance(applies_to_raw, list):
            continue
        applies_to = [str(t) for t in applies_to_raw]
        if not (tag_set & set(applies_to)):
            continue

        if jurisdiction is not None:
            page_juris = fm.get("jurisdictions")
            if page_juris and jurisdiction not in page_juris:
                continue

        matches.append(WikiPage(
            page_id=int(page_id),
            path=str(path),
            title=str(title),
            revision_id=int(rev_id),
            revision_number=int(rev_no),
            body_md=str(body_md),
            applies_to=applies_to,
        ))
    return matches


async def resolve_references(
    orchestration_db: aiosqlite.Connection,
    refs: list[tuple[int, int]] | list[list[int]],
) -> list[dict[str, Any]]:
    """Resolve `(page_id, revision_id)` pairs to display dicts for the DAG-viewer.

    Source: PRD-AutonomousCFO §7.4 (DAG-viewer per-decision event carries
    the wiki citations the agent read). The executor calls this right
    after `propose_checkpoint_commit` so the `agent.decision` event ships
    with `path`, `title`, and `revision_number` already attached — the
    frontend never has to round-trip for the citation list itself, only
    for the body when the operator clicks through.

    Args:
      orchestration_db: open connection to orchestration.db.
      refs: list of `(page_id, revision_id)` pairs in the order the agent
        cited them. Tuples and 2-element lists are both accepted (JSON
        deserialization can produce either).

    Returns:
      A list of `{page_id, revision_id, revision_number, path, title}`
      dicts in the same order as `refs`. Pairs that cannot be resolved
      (deleted page, mismatched revision_id) are skipped silently with a
      warning logged — this keeps the event-emit path soft-fail rather
      than blocking the run.
    """
    if not refs:
        return []

    # Normalize + dedup while preserving order so we issue exactly one query.
    norm: list[tuple[int, int]] = []
    seen: set[tuple[int, int]] = set()
    for r in refs:
        if isinstance(r, (tuple, list)) and len(r) >= 2 and r[0] is not None and r[1] is not None:
            pair = (int(r[0]), int(r[1]))
            if pair in seen:
                continue
            seen.add(pair)
            norm.append(pair)
    if not norm:
        return []

    # SQLite doesn't accept tuple-IN, so we OR a series of equality clauses.
    where_clauses = " OR ".join(
        ["(p.id = ? AND r.id = ?)"] * len(norm)
    )
    params: list[int] = []
    for page_id, revision_id in norm:
        params.extend([page_id, revision_id])

    cur = await orchestration_db.execute(
        "SELECT p.id, r.id, r.revision_number, p.path, p.title "
        "  FROM wiki_pages p "
        "  JOIN wiki_revisions r ON r.page_id = p.id "
        f" WHERE {where_clauses}",
        tuple(params),
    )
    rows = await cur.fetchall()
    await cur.close()

    by_pair: dict[tuple[int, int], dict[str, Any]] = {}
    for row in rows:
        page_id, revision_id, revision_number, path, title = row
        by_pair[(int(page_id), int(revision_id))] = {
            "page_id": int(page_id),
            "revision_id": int(revision_id),
            "revision_number": int(revision_number),
            "path": str(path),
            "title": str(title),
        }

    out: list[dict[str, Any]] = []
    for pair in norm:
        resolved = by_pair.get(pair)
        if resolved is None:
            logger.warning(
                "wiki.resolve_references: unresolved (page_id=%d, revision_id=%d)",
                pair[0], pair[1],
            )
            continue
        out.append(resolved)
    return out
