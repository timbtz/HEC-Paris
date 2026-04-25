"""Tests for `backend.api.period_reports` — list, fetch, artifact, approve.

Source: backend-gap-plan §2.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from backend.api.period_reports import router as period_reports_router
from backend.orchestration.store.writes import write_tx


@pytest_asyncio.fixture
async def app(store):
    a = FastAPI()
    a.state.store = store
    a.include_router(period_reports_router)
    return a


@pytest_asyncio.fixture
async def client(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac


async def _seed_report(
    store,
    *,
    period_code: str = "2026-Q1",
    report_type: str = "period_close",
    status: str = "draft",
    confidence: float = 0.91,
    write_md: bool = True,
) -> int:
    blob_dir = Path(store.data_dir) / "blobs" / "reports" / period_code
    blob_dir.mkdir(parents=True, exist_ok=True)
    json_path = blob_dir / f"{report_type}.json"
    md_path = blob_dir / f"{report_type}.md"
    payload = {
        "period_code": period_code,
        "report_type": report_type,
        "confidence": confidence,
        "headline": "demo",
    }
    json_path.write_text(json.dumps(payload), encoding="utf-8")
    if write_md:
        md_path.write_text(f"# {report_type} {period_code}\n\nok\n", encoding="utf-8")
    async with write_tx(store.accounting, store.accounting_lock) as conn:
        cur = await conn.execute(
            "INSERT INTO period_reports "
            "(period_code, report_type, status, confidence, source_run_id, "
            " blob_path, payload_json) "
            "VALUES (?, ?, ?, ?, NULL, ?, ?)",
            (period_code, report_type, status, confidence,
             str(json_path), json.dumps(payload)),
        )
        return int(cur.lastrowid)


# --------------------------------------------------------------------------- #
# GET /period_reports
# --------------------------------------------------------------------------- #


async def test_list_period_reports_returns_seeded_rows(store, client):
    await _seed_report(store, period_code="2026-Q1", report_type="period_close")
    await _seed_report(store, period_code="2026-Q1", report_type="vat_return")
    await _seed_report(store, period_code="2026-Q2", report_type="period_close")

    resp = await client.get("/period_reports")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["total"] == 3
    assert len(body["items"]) == 3
    # newest-first by id
    ids = [it["id"] for it in body["items"]]
    assert ids == sorted(ids, reverse=True)
    for it in body["items"]:
        assert isinstance(it["payload_json"], dict)


async def test_list_period_reports_filters(store, client):
    await _seed_report(store, period_code="2026-Q1", report_type="period_close")
    await _seed_report(store, period_code="2026-Q2", report_type="vat_return",
                       status="flagged")

    resp = await client.get("/period_reports?period_code=2026-Q2")
    assert resp.json()["total"] == 1
    resp = await client.get("/period_reports?type=vat_return")
    assert resp.json()["total"] == 1
    resp = await client.get("/period_reports?status=flagged")
    assert resp.json()["total"] == 1


# --------------------------------------------------------------------------- #
# GET /period_reports/{id}
# --------------------------------------------------------------------------- #


async def test_get_period_report_returns_single_row(store, client):
    rid = await _seed_report(store)
    resp = await client.get(f"/period_reports/{rid}")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["id"] == rid
    assert body["report_type"] == "period_close"
    assert body["payload_json"]["headline"] == "demo"


async def test_get_period_report_404(client):
    resp = await client.get("/period_reports/9999")
    assert resp.status_code == 404


# --------------------------------------------------------------------------- #
# GET /period_reports/{id}/artifact
# --------------------------------------------------------------------------- #


async def test_artifact_serves_markdown_by_default(store, client):
    rid = await _seed_report(store)
    resp = await client.get(f"/period_reports/{rid}/artifact")
    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"].startswith("text/markdown")
    assert "# period_close 2026-Q1" in resp.text


async def test_artifact_pdf_returns_415(store, client):
    rid = await _seed_report(store)
    resp = await client.get(f"/period_reports/{rid}/artifact?format=pdf")
    assert resp.status_code == 415


async def test_artifact_404_when_md_missing(store, client):
    rid = await _seed_report(store, write_md=False)
    resp = await client.get(f"/period_reports/{rid}/artifact")
    assert resp.status_code == 404


# --------------------------------------------------------------------------- #
# POST /period_reports/{id}/approve
# --------------------------------------------------------------------------- #


async def test_approve_flips_status_to_final(store, client):
    rid = await _seed_report(store, status="draft")
    resp = await client.post(f"/period_reports/{rid}/approve",
                             json={"approver_id": 7})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "final"
    assert body["approved_by"] == 7

    cur = await store.accounting.execute(
        "SELECT status, approved_by FROM period_reports WHERE id = ?", (rid,),
    )
    row = await cur.fetchone()
    await cur.close()
    assert row["status"] == "final"
    assert int(row["approved_by"]) == 7


async def test_approve_requires_approver_id(store, client):
    rid = await _seed_report(store)
    resp = await client.post(f"/period_reports/{rid}/approve", json={})
    assert resp.status_code == 400


async def test_approve_404_for_unknown(client):
    resp = await client.post("/period_reports/9999/approve",
                             json={"approver_id": 1})
    assert resp.status_code == 404
