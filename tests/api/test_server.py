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

        with (
            patch.object(srv, "MESH_DB", tmp_path / "data" / "mesh.duckdb"),
            TestClient(srv.app) as client,
        ):
            assert client.app.state.db is None

    def test_lifespan_mesh_found(self, tmp_path: Path) -> None:
        """When mesh.duckdb exists, lifespan sets app.state.db to a MeshDB instance."""
        import duckdb

        import zentinull.api.server as srv

        mesh_path = tmp_path / "data" / "mesh.duckdb"
        mesh_path.parent.mkdir(parents=True, exist_ok=True)
        conn = duckdb.connect(str(mesh_path))
        conn.execute("CREATE TABLE devices (cluster_id TEXT)")
        conn.execute("CHECKPOINT")
        conn.close()

        with patch.object(srv, "MESH_DB", mesh_path), TestClient(srv.app) as client:
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


class TestMainBlock:
    """__main__ block — port parsing and uvicorn invocation."""

    def _run_as_main(self, argv: list[str]) -> object:
        """Run the server module as __main__ with mocked uvicorn and given argv."""
        import runpy
        import sys

        # uvicorn is imported inside __main__, so inject a mock into sys.modules
        # before runpy executes the module.
        import unittest.mock

        mock_uvicorn = unittest.mock.MagicMock()

        saved_modules = {}
        if "uvicorn" in sys.modules:
            saved_modules["uvicorn"] = sys.modules["uvicorn"]
        sys.modules["uvicorn"] = mock_uvicorn

        saved_argv = sys.argv
        sys.argv = argv

        try:
            runpy.run_module("zentinull.api.server", run_name="__main__", alter_sys=False)
        finally:
            sys.argv = saved_argv
            if "uvicorn" in saved_modules:
                sys.modules["uvicorn"] = saved_modules["uvicorn"]
            else:
                del sys.modules["uvicorn"]

        return mock_uvicorn

    def test_default_port(self) -> None:
        """Without --port arg, defaults to 8001 and no reload."""
        mock_uvicorn = self._run_as_main(["server.py"])
        mock_uvicorn.run.assert_called_once()
        args, kwargs = mock_uvicorn.run.call_args
        assert kwargs.get("port") == 8001
        assert kwargs.get("reload") is False

    def test_custom_port(self) -> None:
        """--port 8999 overrides the default port."""
        mock_uvicorn = self._run_as_main(["server.py", "--port", "8999"])
        mock_uvicorn.run.assert_called_once()
        args, kwargs = mock_uvicorn.run.call_args
        assert kwargs.get("port") == 8999

    def test_reload_flag(self) -> None:
        """--reload sets reload=True in uvicorn.run()."""
        mock_uvicorn = self._run_as_main(["server.py", "--port", "8001", "--reload"])
        mock_uvicorn.run.assert_called_once()
        args, kwargs = mock_uvicorn.run.call_args
        assert kwargs.get("reload") is True

    def test_no_reload(self) -> None:
        """Without --reload, reload is False."""
        mock_uvicorn = self._run_as_main(["server.py", "--port", "8001"])
        mock_uvicorn.run.assert_called_once()
        args, kwargs = mock_uvicorn.run.call_args
        assert kwargs.get("reload") is False
