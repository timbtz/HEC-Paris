"""wiki_writer — terminal pipeline tool that commits or queues a post-mortem draft.

Source: PRD-AutonomousCFO §7.3 + plan §STEP-BY-STEP Task 16.

Reads the upstream agent's draft (`ctx.get("draft-post-mortem")`):
- If `requires_human_ratification` is true, enqueue a `review_queue` row
  (kind='wiki_rule_change') and skip the wiki write — a CFO commits the
  edit via `PUT /wiki/pages/{id}` after review.
- Otherwise call `wiki.writer.upsert_page` and return the new
  `(page_id, revision_id, path)`.

Author: `"agent:wiki_post_mortem"` so the audit trail reflects the
non-human source.

Empty / malformed drafts (e.g. missing body_md) short-circuit with
`{skipped: true}` rather than crashing — a runner timeout upstream
should not also break this terminal node.
"""
from __future__ import annotations

from typing import Any

from ..context import FingentContext
from ..store.writes import write_tx
from ..wiki import upsert_page
from ..wiki.schema import WikiFrontmatter


_AUTHOR = "agent:wiki_post_mortem"


def _coerce_frontmatter(raw: Any) -> WikiFrontmatter:
    """Accept either dict (JSON-roundtripped) or WikiFrontmatter."""
    if isinstance(raw, WikiFrontmatter):
        return raw
    if isinstance(raw, dict):
        # Drop None values so from_dict's optional-list checks pass cleanly.
        cleaned = {k: v for k, v in raw.items() if v is not None}
        return WikiFrontmatter.from_dict(cleaned)
    raise ValueError(f"frontmatter must be dict or WikiFrontmatter, got {type(raw).__name__}")


async def _enqueue_rule_change(
    ctx: FingentContext,
    *,
    proposed_policy_path: str | None,
    body_md: str,
    title: str,
) -> int:
    reason = (
        f"wiki rule change proposed by post_mortem agent (run {ctx.run_id}); "
        f"target={proposed_policy_path or '<unspecified>'}; title={title!r}; "
        f"draft_len={len(body_md)}"
    )
    async with write_tx(ctx.store.accounting, ctx.store.accounting_lock) as conn:
        cur = await conn.execute(
            "INSERT INTO review_queue (entry_id, kind, confidence, reason) "
            "VALUES (?, ?, ?, ?)",
            (None, "wiki_rule_change", None, reason),
        )
        review_id = cur.lastrowid
        await cur.close()
    assert review_id is not None
    return int(review_id)


async def run(ctx: FingentContext) -> dict[str, Any]:
    """Pipeline-node entry point. Reads draft from `draft-post-mortem`."""
    draft = ctx.get("draft-post-mortem")
    if not isinstance(draft, dict):
        return {"skipped": True, "reason": "no draft-post-mortem output"}

    body_md = draft.get("body_md") or ""
    title = draft.get("title") or ""
    path = draft.get("path") or ""
    if not (body_md and title and path):
        return {"skipped": True, "reason": "draft missing body_md/title/path"}

    if draft.get("requires_human_ratification"):
        review_id = await _enqueue_rule_change(
            ctx,
            proposed_policy_path=draft.get("proposed_policy_path"),
            body_md=body_md,
            title=title,
        )
        return {
            "enqueued": True,
            "review_id": review_id,
            "page_id": None,
            "revision_id": None,
            "path": draft.get("proposed_policy_path"),
        }

    frontmatter = _coerce_frontmatter(draft.get("frontmatter") or {})
    page_id, revision_id = await upsert_page(
        ctx.store.orchestration,
        ctx.store.orchestration_lock,
        path=path,
        title=title,
        frontmatter=frontmatter,
        body_md=body_md,
        author=_AUTHOR,
    )
    return {
        "enqueued": False,
        "page_id": int(page_id),
        "revision_id": int(revision_id),
        "path": path,
    }
