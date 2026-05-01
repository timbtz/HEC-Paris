"""wiki_reader — fetch routing-tag-matched wiki pages for an agent.

Source: PRD-AutonomousCFO §7.3 ("Agents call the `wiki_reader` tool with
a routing tag (e.g. `applies_to=dinners,fr`). The tool returns matching
page bodies, which are injected verbatim into the agent's system prompt.
The prompt-hash includes `(wiki_page_id, revision_id)` so cache
invalidation is correct.")

Tool contract:
- Input  : tags: list[str], jurisdiction: str | None
- Output : {pages: [{page_id, revision_id, revision_number, path, title,
                     body_md}]}

The `(page_id, revision_id)` references are also stashed on
`ctx.metadata["wiki_references"]` so the calling agent can pluck them out
without a second DB roundtrip and pass them to the runner / audit row.
"""
from __future__ import annotations

from typing import Any

from ..context import FingentContext
from ..wiki.loader import load_pages_for_tags


async def fetch(
    ctx: FingentContext,
    *,
    tags: list[str],
    jurisdiction: str | None = None,
) -> dict[str, Any]:
    """Run the wiki query and stash the citation list on `ctx.metadata`.

    Returns the public dict the agent embeds in its prompt; the audit
    seam (`wiki_references`) is populated as a side-effect on `ctx`.
    """
    pages = await load_pages_for_tags(
        ctx.store.orchestration,
        tags=tags,
        jurisdiction=jurisdiction,
    )

    out_pages = [
        {
            "page_id": p.page_id,
            "revision_id": p.revision_id,
            "revision_number": p.revision_number,
            "path": p.path,
            "title": p.title,
            "body_md": p.body_md,
        }
        for p in pages
    ]

    # Stash on ctx so the agent can thread (page_id, revision_id) into
    # AgentResult.wiki_references and the `propose_checkpoint_commit`
    # audit row without re-querying.
    refs: list[tuple[int, int]] = [(p.page_id, p.revision_id) for p in pages]
    existing = ctx.metadata.get("wiki_references")
    if isinstance(existing, list):
        existing.extend(refs)
    else:
        ctx.metadata["wiki_references"] = list(refs)

    return {"pages": out_pages}


# --- Pipeline-tool entry point --------------------------------------------- #
# The tool registry contract from `tools/noop.py` is `def run(ctx) -> dict`.
# When wired into a YAML pipeline, the routing tags must arrive via
# `ctx.trigger_payload["wiki_tags"]` (and optional `["jurisdiction"]`).
# Agents that call this in-process should use `fetch(ctx, tags=…)` directly.

async def run(ctx: FingentContext) -> dict[str, Any]:
    """Pipeline-node entry point: tags from `trigger_payload` or metadata."""
    tags = (
        ctx.trigger_payload.get("wiki_tags")
        or ctx.metadata.get("wiki_tags")
        or []
    )
    if not isinstance(tags, list):
        raise ValueError("wiki_reader: tags must be a list[str]")
    jurisdiction = (
        ctx.trigger_payload.get("jurisdiction")
        or ctx.metadata.get("jurisdiction")
    )
    return await fetch(ctx, tags=[str(t) for t in tags], jurisdiction=jurisdiction)
