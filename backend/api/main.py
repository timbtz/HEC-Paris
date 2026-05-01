"""FastAPI app entrypoint.

Source: REF-FASTAPI-BACKEND.md:31-82, 170-223 (lifespan + background tasks).
Phase 2 mounts: Swan webhook, document upload, runs API, dashboard SSE.
External-webhook ingress (Shopify et al.) is mounted when the router
module is present.

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
from fastapi.middleware.cors import CORSMiddleware

from ..orchestration import _register_production  # noqa: F401 — import side effect
from ..orchestration.event_bus import bus_reaper_task
from ..orchestration.store.bootstrap import open_dbs
from .accounting_periods import router as accounting_periods_router
from .audit_traces import router as audit_traces_router
from .dashboard import router as dashboard_router
from .demo_webhook import router as demo_router
from .documents import router as documents_router
from .employees import router as employees_router
from .gamification import router as gamification_router
from .period_reports import router as period_reports_router
from .reports import router as reports_router
from .runs import router as runs_router
from .swan_webhook import router as swan_router
from .wiki import router as wiki_router

try:  # external_webhook is wired by a parallel agent; import is optional.
    from .external_webhook import router as external_router  # type: ignore[import-not-found]
except ImportError:  # pragma: no cover
    external_router = None  # type: ignore[assignment]


@asynccontextmanager
async def lifespan(app: FastAPI):
    data_dir = Path(os.environ.get("FINGENT_DATA_DIR", "./data")).resolve()
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "blobs").mkdir(parents=True, exist_ok=True)

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


app = FastAPI(title="Fingent (Phase 2)", lifespan=lifespan)

# CORS — Vite dev server runs at :5173. Permissive in dev; tighten origins
# for prod via env var or config.
_ALLOWED_ORIGINS = [
    "http://localhost:5173",
    "http://127.0.0.1:5173",
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_ALLOWED_ORIGINS,
    allow_credentials=False,
    allow_methods=["GET", "POST", "PATCH"],
    allow_headers=["Content-Type", "Accept", "x-fingent-author"],
)

app.include_router(swan_router)
app.include_router(demo_router)
if external_router is not None:
    app.include_router(external_router)
app.include_router(documents_router)
app.include_router(employees_router)
app.include_router(accounting_periods_router)
app.include_router(period_reports_router)
app.include_router(runs_router)
app.include_router(reports_router)
app.include_router(audit_traces_router)
app.include_router(wiki_router)
app.include_router(dashboard_router)
app.include_router(gamification_router)


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}
