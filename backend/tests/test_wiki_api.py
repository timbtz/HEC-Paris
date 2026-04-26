"""Smoke tests for `backend.api.wiki`.

Source: PRD-AutonomousCFO §7.3 + §7.4. Validates the GET-revision
endpoint the DAG-viewer drilldown drawer hits when the operator clicks
a citation row.
"""
from __future__ import annotations

import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from backend.api.wiki import router as wiki_router
from backend.orchestration.wiki.schema import WikiFrontmatter
from backend.orchestration.wiki.writer import upsert_page


@pytest_asyncio.fixture
async def app(store):
    a = FastAPI()
    a.state.store = store
    a.include_router(wiki_router)
    return a


@pytest_asyncio.fixture
async def client(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac


async def test_get_wiki_revision_returns_revision_body(store, client):
    fm = WikiFrontmatter(applies_to=["dinners", "fr"], jurisdictions=["FR"], revision=1)
    page_id, rev1_id = await upsert_page(
        store.orchestration, store.orchestration_lock,
        path="policies/fr-bewirtung.md", title="FR Bewirtung",
        frontmatter=fm, body_md="# v1\n\nFirst draft.", author="alice",
    )
    page_id_2, rev2_id = await upsert_page(
        store.orchestration, store.orchestration_lock,
        path="policies/fr-bewirtung.md", title="FR Bewirtung (rev 2)",
        frontmatter=fm, body_md="# v2\n\nUpdated body.", author="bob",
    )
    assert page_id == page_id_2
    assert rev1_id != rev2_id

    # Fetch the second revision; body must match what was written then,
    # not the v1 snapshot.
    resp = await client.get(f"/wiki/pages/{page_id}/revisions/{rev2_id}")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["page_id"] == page_id
    assert body["revision_id"] == rev2_id
    assert body["revision_number"] == 2
    assert body["path"] == "policies/fr-bewirtung.md"
    assert body["title"] == "FR Bewirtung (rev 2)"
    assert body["body_md"] == "# v2\n\nUpdated body."
    assert body["frontmatter"]["applies_to"] == ["dinners", "fr"]
    assert body["created_at"]

    # And the first revision still serves its v1 body.
    resp = await client.get(f"/wiki/pages/{page_id}/revisions/{rev1_id}")
    assert resp.status_code == 200
    body1 = resp.json()
    assert body1["revision_number"] == 1
    assert body1["body_md"] == "# v1\n\nFirst draft."


async def test_get_wiki_revision_404_for_unknown_pair(store, client):
    fm = WikiFrontmatter(applies_to=["x"], revision=1)
    page_id, rev_id = await upsert_page(
        store.orchestration, store.orchestration_lock,
        path="policies/x.md", title="X",
        frontmatter=fm, body_md="body", author="t",
    )

    # Wrong revision id for that page.
    resp = await client.get(f"/wiki/pages/{page_id}/revisions/{rev_id + 999}")
    assert resp.status_code == 404

    # Wrong page id entirely.
    resp = await client.get(f"/wiki/pages/{page_id + 999}/revisions/{rev_id}")
    assert resp.status_code == 404
