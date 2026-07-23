from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from ..config import get_config, get_paths, validate_config
from ..logging_config import get_logger, request_id_var, setup
from .db import MeshDB
from .metrics import metrics
from .router import router

log = get_logger("api.server")


async def _scheduler_loop() -> None:
    """Background data-refresh loop — runs inside the server process.

    Mirrors the pattern from sentinull: the server is the single 24/7 process,
    and the scheduler kicks off incremental syncs and full Splink runs on
    their configured intervals.  No separate worker process needed.
    """
    from ..worker import loop as worker_loop

    await worker_loop(register_signals=False)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    setup(level="INFO")
    paths = get_paths()
    if not paths.mesh_path.exists():
        log.warning({"event": "mesh_not_found", "path": str(paths.mesh_path)})
        app.state.db = None
    else:
        app.state.db = MeshDB(paths.mesh_path)
        log.info({"event": "mesh_connected", "path": str(paths.mesh_path)})

    # ── Validate configuration ──────────────────────────────────────────
    cfg = get_config()
    for w in validate_config(cfg):
        log.warning({"event": "config_warning", "message": w})

    # Start background scheduler (same pattern as sentinull)
    scheduler_task = asyncio.create_task(_scheduler_loop())
    log.info({"event": "scheduler_started"})

    yield

    # Clean shutdown — cancel the scheduler
    scheduler_task.cancel()
    with suppress(asyncio.CancelledError):
        await scheduler_task
    log.info({"event": "scheduler_stopped"})


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


# ── F2: Timeout middleware ────────────────────────────────────────────────
@app.middleware("http")
async def timeout_middleware(
    request: Request,
    call_next: Callable[[Request], Awaitable[Response]],
) -> Response:
    """Enforce 30s request deadline. Log WARNING at 5s."""
    deadline = 30.0
    try:
        start = time.time()
        response = await asyncio.wait_for(call_next(request), timeout=deadline)
        elapsed = time.time() - start
        if elapsed > 5.0:
            log.warning(
                {
                    "event": "slow_request",
                    "path": request.url.path,
                    "elapsed": round(elapsed, 2),
                }
            )
        return response
    except TimeoutError:
        log.error(
            {
                "event": "request_timeout",
                "path": request.url.path,
                "deadline": deadline,
            }
        )
        return JSONResponse(
            {"error": "Request timed out", "deadline": deadline},
            status_code=504,
        )


app.include_router(router)
