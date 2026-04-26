"""writer.upsert_page — revision chain integrity.

Source: PRD-AutonomousCFO §7.3 ("Wiki revisions are tracked").
"""
from __future__ import annotations

from backend.orchestration.wiki.schema import WikiFrontmatter
from backend.orchestration.wiki.writer import upsert_page


async def _all_revisions(store, page_id: int) -> list[tuple[int, int, int | None, str]]:
    cur = await store.orchestration.execute(
        "SELECT id, revision_number, parent_revision_id, body_md "
        "  FROM wiki_revisions "
        " WHERE page_id = ? ORDER BY revision_number ASC",
        (page_id,),
    )
    rows = list(await cur.fetchall())
    await cur.close()
    return [(int(r[0]), int(r[1]), (int(r[2]) if r[2] is not None else None), r[3])
            for r in rows]


async def test_three_revisions_with_parent_chain(store):
    fm = WikiFrontmatter(applies_to=["foo"], revision=1)

    page_id_1, rev_id_1 = await upsert_page(
        store.orchestration, store.orchestration_lock,
        path="policies/cycle.md", title="Cycle",
        frontmatter=fm, body_md="v1 body", author="alice",
    )
    page_id_2, rev_id_2 = await upsert_page(
        store.orchestration, store.orchestration_lock,
        path="policies/cycle.md", title="Cycle",
        frontmatter=fm, body_md="v2 body", author="bob",
    )
    page_id_3, rev_id_3 = await upsert_page(
        store.orchestration, store.orchestration_lock,
        path="policies/cycle.md", title="Cycle",
        frontmatter=fm, body_md="v3 body", author="carol",
    )

    # Same page across all three upserts.
    assert page_id_1 == page_id_2 == page_id_3
    # Three distinct revisions.
    assert len({rev_id_1, rev_id_2, rev_id_3}) == 3

    rows = await _all_revisions(store, page_id_1)
    assert len(rows) == 3
    assert [r[1] for r in rows] == [1, 2, 3]
    # Parent chain: rev1 has no parent; rev2.parent == rev1.id; rev3.parent == rev2.id.
    assert rows[0][2] is None
    assert rows[1][2] == rows[0][0]
    assert rows[2][2] == rows[1][0]
    # Body persisted per revision.
    assert rows[0][3] == "v1 body"
    assert rows[1][3] == "v2 body"
    assert rows[2][3] == "v3 body"


async def test_head_row_carries_latest_body(store):
    fm = WikiFrontmatter(applies_to=["foo"], revision=1)
    page_id, _ = await upsert_page(
        store.orchestration, store.orchestration_lock,
        path="policies/head.md", title="Head",
        frontmatter=fm, body_md="initial", author="t",
    )
    await upsert_page(
        store.orchestration, store.orchestration_lock,
        path="policies/head.md", title="Head",
        frontmatter=fm, body_md="updated", author="t",
    )
    cur = await store.orchestration.execute(
        "SELECT body_md FROM wiki_pages WHERE id = ?", (page_id,),
    )
    row = await cur.fetchone()
    assert row[0] == "updated"


async def test_unique_per_page_revision_number(store):
    """The (page_id, revision_number) UNIQUE prevents accidental duplicates."""
    fm = WikiFrontmatter(applies_to=["x"], revision=1)
    pa, _ = await upsert_page(
        store.orchestration, store.orchestration_lock,
        path="policies/a.md", title="A", frontmatter=fm, body_md="a", author="t",
    )
    pb, _ = await upsert_page(
        store.orchestration, store.orchestration_lock,
        path="policies/b.md", title="B", frontmatter=fm, body_md="b", author="t",
    )
    # Both policies pages have revision_number=1; the UNIQUE is per page_id,
    # not global. (Scope to these two page_ids — the maintenance hook also
    # writes log.md / index.md, which would otherwise show up here.)
    cur = await store.orchestration.execute(
        "SELECT page_id, revision_number FROM wiki_revisions "
        "WHERE page_id IN (?, ?) ORDER BY page_id",
        (pa, pb),
    )
    rows = list(await cur.fetchall())
    assert {(int(r[0]), int(r[1])) for r in rows} == {(pa, 1), (pb, 1)}
