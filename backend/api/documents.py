"""Document upload endpoint with SHA256 idempotency.

Source: RealMetaPRD §7.3, §10.

Two safety properties:

  1. Content-addressed storage. The PDF bytes are SHA256-hashed; the blob
     is written to `data/blobs/<sha256>` exactly once, and re-uploads of
     the same content are idempotent at both the filesystem and DB layer.
  2. Idempotent pipeline dispatch. A re-upload returns the most recent
     `pipeline_runs.id` for that document instead of starting a fresh
     extraction (RealMetaPRD §7.3 idempotency requirement).
"""
from __future__ import annotations

import hashlib
from typing import Any

from fastapi import APIRouter, File, Form, Request, UploadFile

from ..orchestration.executor import execute_pipeline
from ..orchestration.store.writes import write_tx


router = APIRouter(prefix="/documents")


@router.post("/upload")
async def upload_document(
    request: Request,
    file: UploadFile = File(...),
    employee_id: int | None = Form(default=None),
) -> dict[str, Any]:
    """Accept a multipart-uploaded PDF; trigger document_ingested on first sight."""
    data = await file.read()
    sha256 = hashlib.sha256(data).hexdigest()

    store = request.app.state.store
    data_dir = store.data_dir
    blob_dir = data_dir / "blobs"
    blob_dir.mkdir(parents=True, exist_ok=True)

    blob_path = blob_dir / sha256
    if not blob_path.exists():
        # Disk write is idempotent because filename == content hash.
        blob_path.write_bytes(data)

    employee_for_db: Any = employee_id

    inserted = False
    document_id: int
    async with write_tx(store.accounting, store.accounting_lock) as conn:
        cur = await conn.execute(
            "INSERT OR IGNORE INTO documents "
            "(sha256, kind, direction, counterparty_id, amount_cents, vat_cents, "
            " issue_date, due_date, employee_id, extraction, blob_path) "
            "VALUES (?, 'invoice_in', 'inbound', NULL, NULL, NULL, "
            "        NULL, NULL, ?, NULL, ?)",
            (sha256, employee_for_db, str(blob_path)),
        )
        inserted = (cur.rowcount or 0) > 0

        cur2 = await conn.execute(
            "SELECT id FROM documents WHERE sha256 = ?", (sha256,)
        )
        row = await cur2.fetchone()
        await cur2.close()
        if row is None:  # pragma: no cover — INSERT OR IGNORE just ran
            raise RuntimeError("documents row vanished after INSERT OR IGNORE")
        document_id = int(row[0])

    if inserted:
        run_id = await execute_pipeline(
            "document_ingested",
            trigger_source="document.uploaded",
            trigger_payload={
                "document_id": document_id,
                "sha256": sha256,
                "employee_id": employee_id,
            },
            store=store,
            employee_id=employee_id,
        )
    else:
        # Idempotent re-upload: surface the most recent run_id for this doc.
        cur3 = await store.orchestration.execute(
            "SELECT MAX(id) FROM pipeline_runs "
            "WHERE pipeline_name = 'document_ingested' "
            "  AND json_extract(trigger_payload, '$.document_id') = ?",
            (document_id,),
        )
        row3 = await cur3.fetchone()
        await cur3.close()
        run_id = int(row3[0]) if row3 and row3[0] is not None else 0

    return {
        "document_id": document_id,
        "sha256": sha256,
        "run_id": run_id,
        "stream_url": f"/runs/{run_id}/stream",
    }
