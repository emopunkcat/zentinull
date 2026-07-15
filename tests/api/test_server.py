"""Tests for api.server — lifespan, app setup, and entry point."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient


class TestLifespan:
    """Server lifespan — mesh DB found / not found."""

    def test_lifespan_mesh_not_found(self, tmp_path: Path) -> None:
        """When mesh.duckdb does not exist, lifespan sets app.state.db = None."""
        import zentinull.api.server as srv
        from zentinull.config import ProjectPaths

        _paths = ProjectPaths(
            project="test",
            data_dir=tmp_path / "data",
            export_dir=tmp_path / "export",
            mesh_path=tmp_path / "data" / "mesh.duckdb",
            status_file=tmp_path / "data" / "status.json",
            log_file=tmp_path / "data" / "pipeline.log",
            csv_dir=tmp_path / "export" / "csv",
            splink_output_dir=tmp_path / "export" / "splink_output",
            benchmarks_dir=tmp_path / ".benchmarks",
        )
        with (
            patch.object(srv, "PATHS", _paths),
            TestClient(srv.app) as client,
        ):
            assert client.app.state.db is None

    def test_lifespan_mesh_found(self, tmp_path: Path) -> None:
        """When mesh.duckdb exists, lifespan sets app.state.db to a MeshDB instance."""
        import duckdb

        import zentinull.api.server as srv
        from zentinull.config import ProjectPaths

        mesh_path = tmp_path / "data" / "mesh.duckdb"
        mesh_path.parent.mkdir(parents=True, exist_ok=True)
        conn = duckdb.connect(str(mesh_path))
        conn.execute("CREATE TABLE devices (cluster_id TEXT)")
        conn.execute("CHECKPOINT")
        conn.close()

        _paths = ProjectPaths(
            project="test",
            data_dir=tmp_path / "data",
            export_dir=tmp_path / "export",
            mesh_path=mesh_path,
            status_file=tmp_path / "data" / "status.json",
            log_file=tmp_path / "data" / "pipeline.log",
            csv_dir=tmp_path / "export" / "csv",
            splink_output_dir=tmp_path / "export" / "splink_output",
            benchmarks_dir=tmp_path / ".benchmarks",
        )
        with patch.object(srv, "PATHS", _paths), TestClient(srv.app) as client:
            assert client.app.state.db is not None
            assert hasattr(client.app.state.db, "lookup")


class TestAppConfig:
    """App-level configuration."""

    def test_cors_wildcard(self) -> None:
        """CORS middleware allows all origins."""
        from zentinull.api.server import app

        middleware = [m for m in app.user_middleware if m.cls.__name__ == "CORSMiddleware"]
        assert len(middleware) == 1
        opts = middleware[0].kwargs
        assert opts.get("allow_origins") == ["*"]

    def test_api_version(self) -> None:
        """FastAPI app has version string."""
        from zentinull.api.server import app

        assert app.version == "3.0"

    def test_router_attached(self) -> None:
        """Router endpoints are visible via the app routes."""
        from zentinull.api.server import app

        # The included router is wrapped, so we check total route count
        assert len(app.routes) >= 5, "Expected at least 5 routes (docs, openapi, + router endpoints)"
