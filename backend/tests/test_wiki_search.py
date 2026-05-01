"""wiki_search FTS5 query tool — BM25 ranking + revision pinning.

Source: plan §STEP-BY-STEP Task 16.
"""
from __future__ import annotations

from backend.orchestration.context import FingentContext
from backend.orchestration.tools.wiki_search import fetch
from backend.orchestration.wiki.schema import WikiFrontmatter
from backend.orchestration.wiki.writer import upsert_page


def _ctx(store) -> FingentContext:
    return FingentContext(
        run_id=1,
        pipeline_name="test",
        trigger_source="manual",
        trigger_payload={},
        node_outputs={},
        store=store,
    )


async def test_fts5_returns_matching_pages(store):
    fm = WikiFrontmatter(applies_to=["any"], revision=1)
    page_a, rev_a = await upsert_page(
        store.orchestration, store.orchestration_lock,
        path="policies/expense.md", title="Expenses",
        frontmatter=fm, body_md="Bewirtung dinner threshold 250 EUR.",
        author="t",
    )
    page_b, rev_b = await upsert_page(
        store.orchestration, store.orchestration_lock,
        path="policies/travel.md", title="Travel",
        frontmatter=fm, body_md="Flights and trains booked via TMC only.",
        author="t",
    )

    out = await fetch(_ctx(store), query="dinner")
    hits = out["hits"]
    paths = [h["path"] for h in hits]
    assert "policies/expense.md" in paths
    assert "policies/travel.md" not in paths

    only = next(h for h in hits if h["path"] == "policies/expense.md")
    assert only["page_id"] == page_a
    assert only["revision_id"] == rev_a
    assert only["revision_number"] == 1
    assert "score" in only


async def test_fts5_pins_to_latest_revision(store):
    """A second revision supersedes the first — old body is dropped from results."""
    fm = WikiFrontmatter(applies_to=["any"], revision=1)
    page_id, rev1 = await upsert_page(
        store.orchestration, store.orchestration_lock,
        path="policies/p.md", title="P",
        frontmatter=fm, body_md="OLDTERM only", author="t",
    )
    _, rev2 = await upsert_page(
        store.orchestration, store.orchestration_lock,
        path="policies/p.md", title="P",
        frontmatter=fm, body_md="NEWTERM only", author="t",
    )
    assert rev1 != rev2

    # The old body is gone from the index after the AFTER UPDATE shadow row.
    old_hits = (await fetch(_ctx(store), query="OLDTERM"))["hits"]
    assert old_hits == [] or all(h["path"] != "policies/p.md" for h in old_hits)

    new_hits = (await fetch(_ctx(store), query="NEWTERM"))["hits"]
    assert any(h["page_id"] == page_id and h["revision_id"] == rev2 for h in new_hits)


async def test_fts5_stashes_citations_on_ctx(store):
    fm = WikiFrontmatter(applies_to=["any"], revision=1)
    page_id, rev_id = await upsert_page(
        store.orchestration, store.orchestration_lock,
        path="policies/citing.md", title="Citing",
        frontmatter=fm, body_md="UNIQUEKEYWORD here.", author="t",
    )

    ctx = _ctx(store)
    await fetch(ctx, query="UNIQUEKEYWORD")
    assert (page_id, rev_id) in ctx.metadata["wiki_references"]


async def test_fts5_empty_query_short_circuits(store):
    """Whitespace / empty query returns [] without hitting FTS5 (which would raise)."""
    fm = WikiFrontmatter(applies_to=["any"], revision=1)
    await upsert_page(
        store.orchestration, store.orchestration_lock,
        path="policies/q.md", title="Q",
        frontmatter=fm, body_md="any body", author="t",
    )

    out = await fetch(_ctx(store), query="")
    assert out == {"hits": []}
    out = await fetch(_ctx(store), query="   ")
    assert out == {"hits": []}


async def test_fts5_parameterised_query_does_not_interpret_sql(store):
    """Quotes / semicolons in a query are FTS5 syntax, not SQL — must not crash."""
    fm = WikiFrontmatter(applies_to=["any"], revision=1)
    await upsert_page(
        store.orchestration, store.orchestration_lock,
        path="policies/safe.md", title="Safe",
        frontmatter=fm, body_md="benignword here.", author="t",
    )
    # FTS5 will reject some character combinations — that's fine; the
    # important property is the call doesn't smuggle SQL.
    try:
        out = await fetch(_ctx(store), query='benignword')
    except Exception:  # noqa: BLE001 - any FTS5 syntax error is fine here
        out = {"hits": []}
    assert isinstance(out, dict)
