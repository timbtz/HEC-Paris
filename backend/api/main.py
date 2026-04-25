"""FastAPI app entrypoint.

Source: REF-FASTAPI-BACKEND.md:31-82, 170-223 (lifespan + background tasks).
Phase 1 ships ONLY `/healthz`; webhook routes, document upload, runs API,
and SSE arrive in Phase D / E / F.

IMPORTANT: deploy with `uvicorn backend.api.main:app --workers 1`.
Multi-worker breaks the per-DB asyncio.Lock invariant
(RealMetaPRD §9.5 line 1419, REF-FASTAPI-BACKEND:767).
"""
from __future__ import annotations

import asyncio
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI

from ..orchestration.event_bus import bus_reaper_task
from ..orchestration.store.bootstrap import open_dbs


@asynccontextmanager
async def lifespan(app: FastAPI):
    data_dir = Path(os.environ.get("AGNES_DATA_DIR", "./data")).resolve()
    data_dir.mkdir(parents=True, exist_ok=True)

    app.state.store = await open_dbs(data_dir)
    app.state.bus_reaper = asyncio.create_task(bus_reaper_task())
    try:
        yield
    finally:
        app.state.bus_reaper.cancel()
        try:
            await app.state.bus_reaper
        except (asyncio.CancelledError, BaseException):  # noqa: BLE001
            pass
        await app.state.store.close()


app = FastAPI(title="Agnes (Phase 1 metalayer)", lifespan=lifespan)


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}
