"""Document upload + read endpoints with SHA256 idempotency.

Source: RealMetaPRD §7.3, §10. Document blob serving from
backend-gap-plan §10 — only PDF/PNG/JPEG MIME types are served, and the
blob path is constrained to `<data_dir>/blobs/<sha256>` so a malicious
DB row can't trigger arbitrary-file disclosure.

Routes:

  POST /documents/upload                — multipart upload, idempotent
  GET  /documents/{document_id}         — row + line items
  GET  /documents/{document_id}/blob    — original bytes (PDF / PNG / JPEG)
"""
from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import Response

from ..orchestration.executor import execute_pipeline
from ..orchestration.store.writes import write_tx
from .runs import _row_to_dict, _rows_to_dicts


router = APIRouter(prefix="/documents")


_SHA256_RE = re.compile(r"^[a-f0-9]{64}$")
_MIME_BY_MAGIC: tuple[tuple[bytes, str], ...] = (
    (b"%PDF-", "application/pdf"),
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
)
_ALLOWED_MEDIA_TYPES = frozenset({"application/pdf", "image/png", "image/jpeg"})


def _sniff_media_type(blob: Path) -> str | None:
    """Return the media type for a small allow-list, or None."""
    try:
        with blob.open("rb") as fh:
            head = fh.read(16)
    except OSError:
        return None
    for magic, mime in _MIME_BY_MAGIC:
        if head.startswith(magic):
            return mime
    return None


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


# --------------------------------------------------------------------------- #
# GET /documents/{document_id}  — row + line items
# --------------------------------------------------------------------------- #


@router.get("/{document_id}")
async def get_document(document_id: int, request: Request) -> dict[str, Any]:
    store = request.app.state.store
    cur = await store.accounting.execute(
        "SELECT id, sha256, kind, direction, counterparty_id, amount_cents, "
        "       vat_cents, issue_date, due_date, employee_id, extraction, "
        "       blob_path, created_at "
        "FROM documents WHERE id = ?",
        (document_id,),
    )
    row = await cur.fetchone()
    await cur.close()
    if row is None:
        raise HTTPException(status_code=404, detail=f"document {document_id} not found")

    cur = await store.accounting.execute(
        "SELECT id, document_id, description, amount_cents, vat_rate_bp, gl_hint "
        "FROM document_line_items WHERE document_id = ? ORDER BY id",
        (document_id,),
    )
    line_rows = list(await cur.fetchall())
    await cur.close()

    return {
        "document": _row_to_dict(row),
        "line_items": _rows_to_dicts(line_rows),
    }


# --------------------------------------------------------------------------- #
# GET /documents/{document_id}/blob  — raw PDF/PNG/JPEG bytes
# --------------------------------------------------------------------------- #


@router.get("/{document_id}/blob")
async def get_document_blob(document_id: int, request: Request) -> Response:
    """Stream the original document bytes inline.

    Only PDF / PNG / JPEG content is served (sniffed from magic bytes).
    The on-disk path is forced to `<data_dir>/blobs/<sha256>` so a
    tampered `documents.blob_path` cannot escape the blob store.
    """
    store = request.app.state.store
    cur = await store.accounting.execute(
        "SELECT sha256, kind FROM documents WHERE id = ?", (document_id,),
    )
    row = await cur.fetchone()
    await cur.close()
    if row is None:
        raise HTTPException(status_code=404, detail=f"document {document_id} not found")

    sha256 = (row["sha256"] or "").lower()
    if not _SHA256_RE.match(sha256):
        raise HTTPException(status_code=500, detail="invalid sha256 on document row")

    blob_root = (store.data_dir / "blobs").resolve()
    blob_path = (blob_root / sha256).resolve()
    # Belt-and-braces: refuse to serve a path that escapes the blob root.
    try:
        blob_path.relative_to(blob_root)
    except ValueError:
        raise HTTPException(status_code=403, detail="blob path escapes data dir")

    if not blob_path.is_file():
        raise HTTPException(status_code=404, detail="blob missing on disk")

    media_type = _sniff_media_type(blob_path)
    if media_type not in _ALLOWED_MEDIA_TYPES:
        raise HTTPException(status_code=415, detail="unsupported media type")

    body = blob_path.read_bytes()
    suffix = {
        "application/pdf": "pdf",
        "image/png": "png",
        "image/jpeg": "jpg",
    }[media_type]
    headers = {
        "Content-Disposition": f'inline; filename="{sha256}.{suffix}"',
        "X-Content-Type-Options": "nosniff",
        "Cache-Control": "private, max-age=300",
    }
    return Response(content=body, media_type=media_type, headers=headers)
