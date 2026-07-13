"""Zentinull API — FastAPI server with DuckDB-backed query layer."""

from __future__ import annotations

import sys
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware

from ..config import API_PORT, MESH_DB, _load_dotenv
from ..logging_config import get_logger, request_id_var, setup
from .db import MeshDB
from .metrics import metrics
from .router import router

_load_dotenv()

log = get_logger("api.server")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    setup(level="INFO")
    if not MESH_DB.exists():
        log.warning({"event": "mesh_not_found", "path": str(MESH_DB)})
        app.state.db = None
    else:
        app.state.db = MeshDB(MESH_DB)
        log.info({"event": "mesh_connected", "path": str(MESH_DB)})
    yield


app = FastAPI(
    title="Zentinull",
    version="3.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def add_request_id(
    request: Request,
    call_next: Callable[[Request], Awaitable[Response]],
) -> Response:
    """Attach a unique request_id to every request and track metrics."""
    start = time.time()
    request_id = request.headers.get("X-Request-ID", "") or uuid.uuid4().hex[:12]
    request_id_var.set(request_id)
    response: Response | None = None
    try:
        response = await call_next(request)
        metrics.requests_total.labels(
            method=request.method,
            endpoint=request.url.path,
            status=str(response.status_code),
        ).inc()
        return response
    except Exception:
        metrics.requests_total.labels(
            method=request.method,
            endpoint=request.url.path,
            status="500",
        ).inc()
        raise
    finally:
        elapsed = time.time() - start
        metrics.request_duration_seconds.labels(
            method=request.method,
            endpoint=request.url.path,
        ).observe(elapsed)
        if response is not None:
            response.headers["X-Request-ID"] = request_id


app.include_router(router)


if __name__ == "__main__":
    import uvicorn

    port = int(sys.argv[2]) if len(sys.argv) > 2 and sys.argv[1] == "--port" else API_PORT
    reload = "--reload" in sys.argv
    log.info({"event": "server_start", "url": f"http://0.0.0.0:{port}"})
    log.info({"event": "server_start", "url": f"http://0.0.0.0:{port}/device-view?q=ws28", "desc": "device view"})
    log.info({"event": "server_start", "url": f"http://0.0.0.0:{port}/docs", "desc": "docs"})
    uvicorn.run("zentinull.api.server:app", host="0.0.0.0", port=port, reload=reload)
