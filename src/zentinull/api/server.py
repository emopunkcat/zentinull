"""Zentinull API — FastAPI server with DuckDB-backed query layer."""

from __future__ import annotations

import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware

from ..config import PATHS, _load_dotenv
from ..logging_config import get_logger, request_id_var, setup
from .db import MeshDB
from .metrics import metrics
from .router import router

_load_dotenv()

log = get_logger("api.server")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    setup(level="INFO")
    if not PATHS.mesh_path.exists():
        log.warning({"event": "mesh_not_found", "path": str(PATHS.mesh_path)})
        app.state.db = None
    else:
        app.state.db = MeshDB(PATHS.mesh_path)
        log.info({"event": "mesh_connected", "path": str(PATHS.mesh_path)})
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
