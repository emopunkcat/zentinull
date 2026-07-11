"""Zentinull API — FastAPI server with DuckDB-backed query layer."""

from __future__ import annotations

import sys
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from ..logging_config import get_logger, setup
from .db import MeshDB
from .router import router

log = get_logger("api.server")
ROOT = Path(__file__).resolve().parent.parent.parent.parent
MESH_DB = ROOT / "data" / "mesh.duckdb"


@asynccontextmanager
async def lifespan(app: FastAPI):  # type: ignore[no-untyped-def]
    """Wire up the DuckDB mesh database on startup."""
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

app.include_router(router)


if __name__ == "__main__":
    import uvicorn

    port = int(sys.argv[2]) if len(sys.argv) > 2 and sys.argv[1] == "--port" else 8001
    reload = "--reload" in sys.argv
    log.info({"event": "server_start", "url": f"http://0.0.0.0:{port}"})
    log.info({"event": "server_start", "url": f"http://0.0.0.0:{port}/device-view?q=ws28", "desc": "device view"})
    log.info({"event": "server_start", "url": f"http://0.0.0.0:{port}/docs", "desc": "docs"})
    uvicorn.run("zentinull.api.server:app", host="0.0.0.0", port=port, reload=reload)
