"""Tag routing + jurisdiction filter for the Living Rule Wiki loader.

Source: PRD-AutonomousCFO §7.3.
"""
from __future__ import annotations

from backend.orchestration.wiki.loader import load_pages_for_tags
from backend.orchestration.wiki.schema import WikiFrontmatter
from backend.orchestration.wiki.writer import upsert_page


async def _seed(store) -> dict[str, tuple[int, int]]:
    """Seed two pages with non-overlapping tag sets and return id maps."""
    fr_id, fr_rev = await upsert_page(
        store.orchestration,
        store.orchestration_lock,
        path="policies/fr-bewirtung.md",
        title="FR — Bewirtung",
        frontmatter=WikiFrontmatter(
            applies_to=["dinners", "fr", "bewirtung"],
            jurisdictions=["FR"],
            threshold_eur=250,
            revision=1,
        ),
        body_md="# FR Meals\n\nDeductible at 100% with attendees list.",
        author="test",
    )
    gl_id, gl_rev = await upsert_page(
        store.orchestration,
        store.orchestration_lock,
        path="policies/gl-classification.md",
        title="GL classification",
        frontmatter=WikiFrontmatter(
            applies_to=["gl_accounts", "classification"],
            revision=1,
        ),
        body_md="# GL classification\n\nPick the most-specific code.",
        author="test",
    )
    return {
        "fr-bewirtung": (fr_id, fr_rev),
        "gl-classification": (gl_id, gl_rev),
    }


async def test_tag_routing_returns_intersecting_pages(store):
    seeded = await _seed(store)

    # The GL classifier asks for [gl_accounts, classification] — should hit
    # only the GL page.
    pages = await load_pages_for_tags(
        store.orchestration, tags=["gl_accounts", "classification"]
    )
    assert [p.path for p in pages] == ["policies/gl-classification.md"]
    assert pages[0].page_id == seeded["gl-classification"][0]
    assert pages[0].revision_id == seeded["gl-classification"][1]

    # The dinner pipeline asks for [fr, dinners] — should hit only the FR page.
    pages = await load_pages_for_tags(store.orchestration, tags=["fr", "dinners"])
    assert [p.path for p in pages] == ["policies/fr-bewirtung.md"]


async def test_returns_latest_revision(store):
    page_id_v1, rev_id_v1 = await upsert_page(
        store.orchestration,
        store.orchestration_lock,
        path="policies/x.md",
        title="X",
        frontmatter=WikiFrontmatter(applies_to=["foo"], revision=1),
        body_md="# X v1\n\nbody one",
        author="alice",
    )
    page_id_v2, rev_id_v2 = await upsert_page(
        store.orchestration,
        store.orchestration_lock,
        path="policies/x.md",
        title="X",
        frontmatter=WikiFrontmatter(applies_to=["foo"], revision=2),
        body_md="# X v2\n\nbody two",
        author="alice",
    )
    assert page_id_v1 == page_id_v2
    assert rev_id_v2 != rev_id_v1

    pages = await load_pages_for_tags(store.orchestration, tags=["foo"])
    assert len(pages) == 1
    assert pages[0].revision_id == rev_id_v2
    assert pages[0].revision_number == 2
    assert "body two" in pages[0].body_md


async def test_jurisdiction_filter(store):
    await _seed(store)
    # Asking for fr-tagged pages with jurisdiction=DE should drop the FR page
    # (its jurisdictions=[FR] excludes DE).
    pages = await load_pages_for_tags(
        store.orchestration, tags=["fr"], jurisdiction="DE"
    )
    assert pages == []

    # Same tag, jurisdiction=FR — included.
    pages = await load_pages_for_tags(
        store.orchestration, tags=["fr"], jurisdiction="FR"
    )
    assert [p.path for p in pages] == ["policies/fr-bewirtung.md"]

    # Jurisdiction-agnostic page (no `jurisdictions` field) is included
    # regardless of caller's jurisdiction.
    pages = await load_pages_for_tags(
        store.orchestration, tags=["gl_accounts"], jurisdiction="DE"
    )
    assert [p.path for p in pages] == ["policies/gl-classification.md"]


async def test_empty_tags_returns_empty(store):
    await _seed(store)
    assert await load_pages_for_tags(store.orchestration, tags=[]) == []


async def test_deterministic_path_order(store):
    # Seed two matching pages with non-alphabetic creation order.
    await upsert_page(
        store.orchestration, store.orchestration_lock,
        path="policies/zeta.md", title="Zeta",
        frontmatter=WikiFrontmatter(applies_to=["common"], revision=1),
        body_md="z", author="t",
    )
    await upsert_page(
        store.orchestration, store.orchestration_lock,
        path="policies/alpha.md", title="Alpha",
        frontmatter=WikiFrontmatter(applies_to=["common"], revision=1),
        body_md="a", author="t",
    )
    pages = await load_pages_for_tags(store.orchestration, tags=["common"])
    assert [p.path for p in pages] == ["policies/alpha.md", "policies/zeta.md"]
