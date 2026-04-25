"""Tests for `backend.api.documents` — multipart upload + idempotency.

Source: RealMetaPRD §7.3 (content-addressed PDF storage; idempotent re-upload
returns the most recent run_id, never creates a duplicate run).
"""
from __future__ import annotations

import hashlib
import io
from pathlib import Path

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

import backend.orchestration  # noqa: F401 — registers production tools/agents/conditions
from backend.api.dashboard import router as dashboard_router
from backend.api.documents import router as documents_router
from backend.api.runs import router as runs_router
from backend.orchestration.executor import wait_for_run


# Tiny but valid-ish PDF byte string.
_FAKE_PDF = b"%PDF-1.4\n%fake-agnes-test\n%%EOF\n"


@pytest_asyncio.fixture
async def app(store, fake_anthropic, fake_anthropic_message):
    """A minimal FastAPI app wired to the shared `store` fixture (no lifespan)."""
    # Make the document_extractor agent see a deterministic AnthropicResponse so
    # the document_ingested pipeline never tries the real network.
    _, fake = fake_anthropic
    fake.messages._response = fake_anthropic_message(
        tool_input={
            "supplier_name": "Test Supplier",
            "invoice_number": "INV-1",
            "date": "2026-04-01",
            "due_date": None,
            "items": [
                {"description": "Widget", "amount_cents": 1000, "vat_rate_bp": 2000},
            ],
            "subtotal_cents": 1000,
            "vat_percent": 20.0,
            "vat_cents": 200,
            "total_cents": 1200,
            "currency": "EUR",
            "confidence": 0.9,
        },
        tool_name="submit_invoice",
    )

    a = FastAPI()
    a.state.store = store
    a.include_router(documents_router)
    a.include_router(runs_router)
    a.include_router(dashboard_router)
    return a


@pytest_asyncio.fixture
async def client(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac


async def test_upload_returns_document_id_sha_runid_streamurl(client, store):
    files = {"file": ("invoice.pdf", io.BytesIO(_FAKE_PDF), "application/pdf")}
    resp = await client.post("/documents/upload", files=files, data={"employee_id": "1"})
    assert resp.status_code == 200, resp.text

    body = resp.json()
    sha = hashlib.sha256(_FAKE_PDF).hexdigest()
    assert body["sha256"] == sha
    assert body["document_id"] >= 1
    assert body["run_id"] >= 1
    assert body["stream_url"] == f"/runs/{body['run_id']}/stream"

    # Drain the dispatched pipeline so we can assert on its terminal state.
    await wait_for_run(body["run_id"])

    # Blob file is on disk (data_dir/blobs/<sha>).
    blob_path = Path(store.data_dir) / "blobs" / sha
    assert blob_path.exists()
    assert blob_path.read_bytes() == _FAKE_PDF


async def test_upload_idempotent_returns_existing_doc_and_latest_run(client, store):
    files = {"file": ("invoice.pdf", io.BytesIO(_FAKE_PDF), "application/pdf")}
    resp1 = await client.post("/documents/upload", files=files, data={"employee_id": "1"})
    assert resp1.status_code == 200
    first = resp1.json()
    await wait_for_run(first["run_id"])

    files2 = {"file": ("invoice-again.pdf", io.BytesIO(_FAKE_PDF), "application/pdf")}
    resp2 = await client.post("/documents/upload", files=files2, data={"employee_id": "1"})
    assert resp2.status_code == 200
    second = resp2.json()

    assert second["document_id"] == first["document_id"]
    assert second["sha256"] == first["sha256"]

    # The second response must surface the most recent run_id, which on a
    # second upload (no new dispatch) is the run_id from the first upload.
    assert second["run_id"] == first["run_id"]

    # documents table only contains one row for this content.
    cur = await store.accounting.execute(
        "SELECT COUNT(*) FROM documents WHERE sha256 = ?", (first["sha256"],)
    )
    row = await cur.fetchone()
    await cur.close()
    assert int(row[0]) == 1


async def test_upload_writes_blob_only_once(client, store):
    """Idempotent disk write: re-uploading the same content does not error."""
    files = {"file": ("a.pdf", io.BytesIO(_FAKE_PDF), "application/pdf")}
    resp1 = await client.post("/documents/upload", files=files)
    assert resp1.status_code == 200
    body = resp1.json()
    await wait_for_run(body["run_id"])

    blob_path = Path(store.data_dir) / "blobs" / body["sha256"]
    mtime_before = blob_path.stat().st_mtime_ns

    # Second upload should not raise; should not rewrite the blob file.
    files2 = {"file": ("a.pdf", io.BytesIO(_FAKE_PDF), "application/pdf")}
    resp2 = await client.post("/documents/upload", files=files2)
    assert resp2.status_code == 200

    # mtime is unchanged because the blob path already existed.
    assert blob_path.stat().st_mtime_ns == mtime_before
