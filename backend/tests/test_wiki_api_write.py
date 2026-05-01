"""POST /wiki/pages and PUT /wiki/pages/{id} — write API + frontmatter validation.

Source: plan §STEP-BY-STEP Task 21.
"""
from __future__ import annotations

import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from backend.api.wiki import router as wiki_router


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


async def test_post_creates_revision_one(client):
    body = {
        "path": "policies/expense-thresholds.md",
        "title": "Expense thresholds",
        "body_md": "## Manager approval > 500 EUR",
        "frontmatter": {
            "applies_to": ["anomaly_detection", "period_close"],
            "jurisdictions": ["FR"],
            "revision": 1,
        },
    }
    resp = await client.post("/wiki/pages", json=body, headers={"x-fingent-author": "marie"})
    assert resp.status_code == 200, resp.text
    payload = resp.json()
    assert payload["path"] == body["path"]
    assert payload["revision_number"] == 1
    assert payload["title"] == body["title"]
    assert payload["frontmatter"]["applies_to"] == ["anomaly_detection", "period_close"]


async def test_put_bumps_revision_number(client):
    create = {
        "path": "policies/p.md",
        "title": "P v1",
        "body_md": "first body",
        "frontmatter": {"applies_to": ["x"], "revision": 1},
    }
    resp = await client.post("/wiki/pages", json=create)
    assert resp.status_code == 200
    page_id = resp.json()["page_id"]

    update = {
        "title": "P v2",
        "body_md": "updated body",
        "frontmatter": {"applies_to": ["x"], "revision": 2},
    }
    resp = await client.put(f"/wiki/pages/{page_id}", json=update)
    assert resp.status_code == 200, resp.text
    payload = resp.json()
    assert payload["revision_number"] == 2
    assert payload["body_md"] == "updated body"
    assert payload["title"] == "P v2"


async def test_post_rejects_malformed_frontmatter(client):
    body = {
        "path": "policies/bad.md",
        "title": "Bad",
        "body_md": "x",
        "frontmatter": {"applies_to": "not-a-list"},  # invalid
    }
    resp = await client.post("/wiki/pages", json=body)
    assert resp.status_code == 400
    assert "applies_to" in resp.json()["detail"]


async def test_put_404_for_unknown_page_id(client):
    resp = await client.put(
        "/wiki/pages/9999",
        json={"title": "x", "body_md": "y", "frontmatter": {"applies_to": ["x"], "revision": 1}},
    )
    assert resp.status_code == 404


async def test_post_then_get_round_trips(client):
    body = {
        "path": "policies/round-trip.md",
        "title": "Round-trip",
        "body_md": "hello",
        "frontmatter": {"applies_to": ["a", "b"], "revision": 1},
    }
    resp = await client.post("/wiki/pages", json=body)
    page_id = resp.json()["page_id"]

    resp = await client.get(f"/wiki/pages/{page_id}")
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["body_md"] == "hello"
    assert payload["frontmatter"]["applies_to"] == ["a", "b"]
