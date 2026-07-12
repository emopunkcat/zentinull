"""Tests for api.router — FastAPI TestClient."""

from __future__ import annotations

from fastapi.testclient import TestClient


def test_dashboard_returns_503_when_no_db(client: TestClient) -> None:
    resp = client.get("/dashboard")
    assert resp.status_code == 503
    assert "Mesh database not loaded" in resp.text
