"""FastAPI application entrypoint."""

from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .api import alerts as alerts_api
from .api import hosts as hosts_api
from .api import incidents as incidents_api
from .api import system as system_api
from .config import settings
from .database import configure_database, get_session_factory, init_db
from .seed import seed_all
from .workers.monitor import monitor_loop

logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s %(levelname)-8s %(name)s :: %(message)s",
)
logger = logging.getLogger("ship")

_monitor_task: asyncio.Task | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _monitor_task
    configure_database()
    init_db()
    session = get_session_factory()()
    try:
        seeded = seed_all(session, include_demo_hosts=settings.environment != "production")
        logger.info("bootstrap complete: %s", seeded)
    finally:
        session.close()

    if settings.enable_monitor_loop:
        _monitor_task = asyncio.create_task(monitor_loop())
        logger.info("background monitor loop enabled")
    else:
        logger.info("background monitor loop disabled (SHIP_ENABLE_MONITOR_LOOP=false)")

    yield

    if _monitor_task is not None:
        _monitor_task.cancel()
        try:
            await _monitor_task
        except (asyncio.CancelledError, Exception):  # noqa: BLE001
            pass


app = FastAPI(
    title=settings.app_name,
    version="1.0.0",
    description=(
        "Self-healing infrastructure monitoring: metric evaluation, incident "
        "classification, Ansible-driven recovery with loop safeguards."
    ),
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list or ["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(system_api.router, prefix=settings.api_prefix)
app.include_router(hosts_api.router, prefix=settings.api_prefix)
app.include_router(incidents_api.router, prefix=settings.api_prefix)
app.include_router(alerts_api.router, prefix=settings.api_prefix)


@app.get("/metrics", include_in_schema=False)
def prometheus_metrics():
    """Prometheus scrape target for the platform's own KPIs."""
    from fastapi import Depends

    from .database import get_db

    generator = get_db()
    db = next(generator)
    try:
        from .api.system import self_metrics

        return self_metrics(db)
    finally:
        generator.close()


@app.get("/", include_in_schema=False)
def root():
    index = Path(__file__).resolve().parents[2] / "frontend" / "dist" / "index.html"
    if index.exists():
        return FileResponse(index)
    return JSONResponse(
        {
            "service": settings.app_name,
            "version": "1.0.0",
            "docs": "/docs",
            "api": settings.api_prefix,
            "metrics": "/metrics",
            "frontend": "run the Vite dev server (npm run dev) or build it (npm run build)",
        }
    )


_dist = Path(__file__).resolve().parents[2] / "frontend" / "dist"
if _dist.exists():
    app.mount("/assets", StaticFiles(directory=str(_dist / "assets")), name="assets")


def run() -> None:  # pragma: no cover - entrypoint helper
    import uvicorn

    uvicorn.run(
        "app.main:app",
        host=os.environ.get("SHIP_HOST", "0.0.0.0"),
        port=int(os.environ.get("SHIP_PORT", "8000")),
        reload=False,
    )


if __name__ == "__main__":  # pragma: no cover
    run()
