"""Tests for api.router — FastAPI TestClient."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from zentinull.api.server import app


@pytest.fixture
def client():
    app.state.db = None
    return TestClient(app)


def test_dashboard_returns_503_when_no_db(client):
    resp = client.get("/dashboard")
    assert resp.status_code == 503
    assert "Mesh database not loaded" in resp.text
