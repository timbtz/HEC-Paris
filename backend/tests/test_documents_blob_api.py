"""Tests for `GET /documents/{id}` and `GET /documents/{id}/blob`.

Source: backend-gap-plan §10. Validates the read paths added alongside
the existing `POST /documents/upload`. No pipeline dispatch is required
for the read tests, so the fixture skips the `fake_anthropic` wiring
that the upload tests need.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from backend.api.documents import router as documents_router
from backend.orchestration.store.writes import write_tx


_FAKE_PDF = b"%PDF-1.4\n%fake-agnes-test\n%%EOF\n"
_FAKE_PNG = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDRfake"
_FAKE_TXT = b"plain text body\n"


@pytest_asyncio.fixture
async def app(store):
    a = FastAPI()
    a.state.store = store
    a.include_router(documents_router)
    return a


@pytest_asyncio.fixture
async def client(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac


async def _seed_doc(store, content: bytes, kind: str = "invoice_in") -> tuple[int, str]:
    sha = hashlib.sha256(content).hexdigest()
    blob_dir = Path(store.data_dir) / "blobs"
    blob_dir.mkdir(parents=True, exist_ok=True)
    (blob_dir / sha).write_bytes(content)
    async with write_tx(store.accounting, store.accounting_lock) as conn:
        cur = await conn.execute(
            "INSERT INTO documents (sha256, kind, direction, blob_path) "
            "VALUES (?, ?, 'inbound', ?)",
            (sha, kind, str(blob_dir / sha)),
        )
        return int(cur.lastrowid), sha


async def test_get_document_returns_row_and_line_items(store, client):
    doc_id, sha = await _seed_doc(store, _FAKE_PDF)
    async with write_tx(store.accounting, store.accounting_lock) as conn:
        await conn.execute(
            "INSERT INTO document_line_items "
            "(document_id, description, amount_cents, vat_rate_bp) "
            "VALUES (?, 'Widget', 1000, 2000)",
            (doc_id,),
        )
    resp = await client.get(f"/documents/{doc_id}")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["document"]["id"] == doc_id
    assert body["document"]["sha256"] == sha
    assert len(body["line_items"]) == 1
    assert body["line_items"][0]["description"] == "Widget"


async def test_get_document_404(client):
    resp = await client.get("/documents/9999")
    assert resp.status_code == 404


async def test_blob_serves_pdf_inline(store, client):
    doc_id, sha = await _seed_doc(store, _FAKE_PDF)
    resp = await client.get(f"/documents/{doc_id}/blob")
    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"] == "application/pdf"
    assert resp.headers["content-disposition"].startswith("inline")
    assert sha in resp.headers["content-disposition"]
    assert resp.content == _FAKE_PDF


async def test_blob_serves_png(store, client):
    doc_id, _ = await _seed_doc(store, _FAKE_PNG)
    resp = await client.get(f"/documents/{doc_id}/blob")
    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"] == "image/png"


async def test_blob_rejects_unsupported_mime(store, client):
    doc_id, _ = await _seed_doc(store, _FAKE_TXT)
    resp = await client.get(f"/documents/{doc_id}/blob")
    assert resp.status_code == 415


async def test_blob_404_when_missing_on_disk(store, client):
    doc_id, sha = await _seed_doc(store, _FAKE_PDF)
    (Path(store.data_dir) / "blobs" / sha).unlink()
    resp = await client.get(f"/documents/{doc_id}/blob")
    assert resp.status_code == 404


async def test_blob_500_on_invalid_sha_in_row(store, client):
    """A tampered blob_path must not enable a path-traversal escape."""
    async with write_tx(store.accounting, store.accounting_lock) as conn:
        cur = await conn.execute(
            "INSERT INTO documents (sha256, kind, direction, blob_path) "
            "VALUES (?, 'invoice_in', 'inbound', '/etc/passwd')",
            ("../../etc/passwd",),
        )
        doc_id = int(cur.lastrowid)
    resp = await client.get(f"/documents/{doc_id}/blob")
    # Either 500 (bad sha) or 404 (blob root miss) is acceptable; what
    # matters is that the response is NOT 200 with /etc/passwd content.
    assert resp.status_code in (403, 404, 500)
    assert b"root:" not in resp.content
