"""
Remote ingest proxy daemon.

Runs on a machine with network access to the 6 IT sources.
Exposes a simple HTTP API over Tailscale so the main Zentinull instance
can trigger remote ingest and download the resulting SQLite databases.

Usage:
    # On the remote machine (has IT network access):
    python -m zentinull.cli.remote          # serves on 0.0.0.0:9999

    python serve.py pipeline --remote 100.x.x.x
    python serve.py ingest --remote 100.x.x.x
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import uvicorn

_HERE = Path(__file__).resolve().parent
ROOT = _HERE.parent.parent.parent
DATA_DIR = ROOT / "data"
EXPORT_CSV_DIR = ROOT / "export" / "csv"

SOURCE_MAP: dict[str, tuple[str, str]] = {
    "sp": ("sharepoint", "SharePoint"),
    "me": ("manageengine", "ManageEngine"),
    "fg": ("fortigate", "FortiGate"),
    "zbx": ("zabbix", "Zabbix"),
    "ad": ("ad", "Active Directory"),
    "sdp": ("servicedeskplus", "ServiceDesk Plus"),
}


def _build_app() -> Any:
    """Build the FastAPI app (lazy import so module is importable without FastAPI)."""
    from fastapi import FastAPI, HTTPException
    from fastapi.responses import FileResponse, Response

    app = FastAPI(title="Zentinull Remote Proxy", version="1.0.0")

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/status")
    def status() -> dict[str, Any]:
        """Return current proxy pipeline status."""
        results: dict[str, Any] = {"sources": {}}
        for key, (_mod_key, display) in SOURCE_MAP.items():
            db_path = DATA_DIR / f"{key}.sqlite"
            results["sources"][key] = {
                "name": display,
                "db_exists": db_path.exists(),
                "db_size_bytes": db_path.stat().st_size if db_path.exists() else 0,
            }
        return results

    @app.post("/ingest/all")
    def ingest_all() -> dict[str, Any]:
        """Run all 6 ingestors sequentially."""
        results: list[dict[str, Any]] = []
        for key in SOURCE_MAP:
            try:
                resp = ingest_source(key)
                results.append(resp)
            except HTTPException as e:
                results.append({"source": key, "status": "error", "detail": str(e.detail)})
        return {"results": results}

    @app.post("/ingest/{source_key}")
    def ingest_source(source_key: str) -> dict[str, Any]:
        """Run a single ingestor on the remote machine."""
        if source_key not in SOURCE_MAP:
            raise HTTPException(404, f"Unknown source: {source_key}")

        mod_key, display = SOURCE_MAP[source_key]
        try:
            mod = __import__(f"zentinull.ingestors.{mod_key}", fromlist=["ingest"])
            rows = mod.ingest()
            return {"source": source_key, "name": display, "rows": rows, "status": "ok"}
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(500, f"{mod_key} ingest failed: {e}") from e

    @app.get("/data/{filename:path}")
    def get_data(filename: str) -> Response:
        """Download a data file (SQLite DB, status.json, etc.)."""
        # Safety: only allow files from the data/ directory
        path = (DATA_DIR / filename).resolve()
        if not str(path).startswith(str(DATA_DIR.resolve())):
            raise HTTPException(403, "Access denied")
        if not path.exists():
            raise HTTPException(404, f"File not found: {filename}")
        return FileResponse(path, media_type="application/octet-stream")

    @app.get("/export/devices.csv")
    def get_export_csv() -> Response:
        """Download the unified devices.csv for Splink."""
        path = EXPORT_CSV_DIR / "devices.csv"
        if not path.exists():
            raise HTTPException(404, "devices.csv not found — run export stage first")
        return FileResponse(path, media_type="text/csv")

    return app


def main() -> None:
    """Start the remote proxy daemon."""
    import argparse

    parser = argparse.ArgumentParser(description="Zentinull Remote Proxy Daemon")
    parser.add_argument("--host", default="0.0.0.0", help="Bind address (default: 0.0.0.0)")
    parser.add_argument("--port", type=int, default=9999, help="Port (default: 9999)")
    parser.add_argument("--reload", action="store_true", help="Enable auto-reload")
    args = parser.parse_args()

    app = _build_app()
    uvicorn.run(app, host=args.host, port=args.port, reload=args.reload)


if __name__ == "__main__":
    main()
