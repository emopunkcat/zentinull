"""Mock-based tests for ingestor ingest() functions.

Tests the full I/O path: HTTP/LDAP calls, SQLite creation, and data insertion.
The _transform* functions are already tested in test_transform.py.

NOTE: ingestors use ``from .base import db`` (local reference), so we
monkeypatch ``zentinull.ingestors.<source>.db``, not ``base.db``.
"""

from __future__ import annotations

import contextlib
import sqlite3
from collections.abc import Iterator
from pathlib import Path
from typing import TYPE_CHECKING, Any
from unittest.mock import Mock, patch

if TYPE_CHECKING:
    import pytest


@contextlib.contextmanager
def _db_pair(name: str) -> Iterator[Path]:
    """Yield (db_path) backed by a temp file.

    Each call to ``ingest()`` opens its own connection and closes it.
    We re-open from *db_path* for verification while the context is
    still alive (the file is deleted on exit).
    """
    import uuid

    tmp_path = Path(f"/tmp/_zentinull_test_{name}_{uuid.uuid4().hex[:8]}.sqlite")
    try:
        yield tmp_path
    finally:
        tmp_path.unlink(missing_ok=True)


def _db_from(path: Path) -> sqlite3.Connection:
    """Open a fresh connection to *path* with Row factory."""
    c = sqlite3.connect(str(path))
    c.row_factory = sqlite3.Row
    return c


# ═══════════════════════════════════════════════════════════════════════════════
# Zabbix ingest
# ═══════════════════════════════════════════════════════════════════════════════


class TestZabbixIngest:
    """Mock-based tests for zentinull.ingestors.zabbix.ingest()."""

    def test_ingest_inserts_hosts(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """When _zbx_call returns host data, records are inserted into SQLite."""
        with _db_pair("zbx") as db_path:
            monkeypatch.setattr("zentinull.ingestors.zabbix.db", lambda _n: _db_from(db_path))
            monkeypatch.setattr("zentinull.ingestors.zabbix.ZBX_URL", "http://fake/api")
            monkeypatch.setattr("zentinull.ingestors.zabbix.ZBX_TOKEN", "fake-token")

            fake_hosts: list[dict[str, Any]] = [
                {
                    "hostid": "10001",
                    "host": "srv-web01",
                    "name": "Web Server 01",
                    "status": "0",
                    "groups": [{"name": "Linux servers"}],
                    "inventory": {
                        "os": "Ubuntu 22.04",
                        "type": "server",
                        "serial_no_a": "SN001",
                        "macaddress_a": "00:1a:2b:3c:4d:5e",
                        "location": "DC1",
                    },
                    "interfaces": [{"ip": "10.0.1.10", "dns": "srv-web01.lan", "port": "10050", "type": "1"}],
                    "tags": [{"tag": "role", "value": "web"}],
                }
            ]

            with patch("zentinull.ingestors.zabbix._zbx_call", return_value=fake_hosts):
                from zentinull.ingestors.zabbix import ingest

                count: int = ingest()

            assert count == 1
            verify = _db_from(db_path)
            try:
                row = verify.execute("SELECT hostid, hostname, name FROM hosts").fetchone()
                assert row is not None
                assert row["hostid"] == "10001"
            finally:
                verify.close()

    def test_ingest_no_hosts_returns_zero(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """When _zbx_call returns None, no records are inserted."""
        with _db_pair("zbx") as db_path:
            monkeypatch.setattr("zentinull.ingestors.zabbix.db", lambda _n: _db_from(db_path))
            monkeypatch.setattr("zentinull.ingestors.zabbix.ZBX_URL", "http://fake/api")
            monkeypatch.setattr("zentinull.ingestors.zabbix.ZBX_TOKEN", "fake-token")

            with patch("zentinull.ingestors.zabbix._zbx_call", return_value=None):
                from zentinull.ingestors.zabbix import ingest

                count: int = ingest()

            assert count == 0
            verify = _db_from(db_path)
            try:
                tables = verify.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
                assert len(tables) == 0
            finally:
                verify.close()


# ═══════════════════════════════════════════════════════════════════════════════
# SharePoint ingest
# ═══════════════════════════════════════════════════════════════════════════════


class TestSharePointIngest:
    """Mock-based tests for zentinull.ingestors.sharepoint.ingest()."""

    def test_ingest_inserts_from_all_endpoints(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """All 6 SharePoint endpoints get populated with data."""
        with _db_pair("sp") as db_path:
            monkeypatch.setattr("zentinull.ingestors.sharepoint.db", lambda _n: _db_from(db_path))
            monkeypatch.setattr("zentinull.ingestors.sharepoint.N8N_BASE", "http://fake-n8n:5678/webhook")

            mock_resp = Mock(status_code=200)
            mock_resp.json.return_value = [{"id": "101", "fields": {"Title": "Laptop ABC"}}]

            with patch("zentinull.ingestors.sharepoint.requests.get", return_value=mock_resp):
                from zentinull.ingestors.sharepoint import ingest

                count: int = ingest()

            assert count == 6
            verify = _db_from(db_path)
            try:
                tables = verify.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
                assert len(tables) == 6
            finally:
                verify.close()

    def test_ingest_handles_http_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """When requests.get raises, the exception is caught and logged."""
        with _db_pair("sp") as db_path:
            monkeypatch.setattr("zentinull.ingestors.sharepoint.db", lambda _n: _db_from(db_path))
            monkeypatch.setattr("zentinull.ingestors.sharepoint.N8N_BASE", "http://fake-n8n:5678/webhook")

            with patch("zentinull.ingestors.sharepoint.requests.get", side_effect=ConnectionError("n8n unreachable")):
                from zentinull.ingestors.sharepoint import ingest

                count: int = ingest()

            assert count == 0

    def test_ingest_handles_empty_responses(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """When all endpoints return empty, count is 0."""
        with _db_pair("sp") as db_path:
            monkeypatch.setattr("zentinull.ingestors.sharepoint.db", lambda _n: _db_from(db_path))
            monkeypatch.setattr("zentinull.ingestors.sharepoint.N8N_BASE", "http://fake-n8n:5678/webhook")

            mock_resp = Mock(status_code=200)
            mock_resp.json.return_value = []

            with patch("zentinull.ingestors.sharepoint.requests.get", return_value=mock_resp):
                from zentinull.ingestors.sharepoint import ingest

                count: int = ingest()

            assert count == 0


# ═══════════════════════════════════════════════════════════════════════════════
# FortiGate ingest
# ═══════════════════════════════════════════════════════════════════════════════


class TestFortiGateIngest:
    """Mock-based tests for zentinull.ingestors.fortigate.ingest()."""

    def test_ingest_processes_all_endpoints(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Each endpoint with data gets inserted into its own table."""
        with _db_pair("fg") as db_path:
            monkeypatch.setattr("zentinull.ingestors.fortigate.db", lambda _n: _db_from(db_path))

            with patch(
                "zentinull.ingestors.fortigate._fg_get", return_value=[{"mac": "aa:bb:cc:dd:ee:01", "ip": "10.0.0.1"}]
            ):
                from zentinull.ingestors.fortigate import ingest

                count: int = ingest()

            assert count == 8
            verify = _db_from(db_path)
            try:
                tables = verify.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
                assert len(tables) == 8
            finally:
                verify.close()

    def test_ingest_handles_empty_endpoints(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """When endpoints return empty, no rows are inserted."""
        with _db_pair("fg") as db_path:
            monkeypatch.setattr("zentinull.ingestors.fortigate.db", lambda _n: _db_from(db_path))

            with patch("zentinull.ingestors.fortigate._fg_get", return_value=[]):
                from zentinull.ingestors.fortigate import ingest

                count: int = ingest()

            assert count == 0


class TestFortiGateHelper:
    """Direct tests for fortigate._fg_get helper function."""

    def test_fg_get_success_list(self) -> None:
        """When API returns a list, it is returned directly."""
        from zentinull.ingestors.auth import APIKeyAuth
        from zentinull.ingestors.fortigate import _fg_get

        auth = APIKeyAuth("fake_key", header_name="Authorization", prefix="Bearer")
        mock_resp = Mock()
        mock_resp.json.return_value = {"results": [{"mac": "aa:bb:cc:dd:ee:ff"}]}
        mock_resp.raise_for_status.return_value = None

        with patch("zentinull.ingestors.fortigate.requests.get", return_value=mock_resp):
            result = _fg_get("/api/v2/monitor/system/interface", auth)

        assert isinstance(result, list)
        assert len(result) == 1
        assert result[0]["mac"] == "aa:bb:cc:dd:ee:ff"

    def test_fg_get_success_dict(self) -> None:
        """When API returns a dict (wrapped in 'results'), it is wrapped in a list."""
        from zentinull.ingestors.auth import APIKeyAuth
        from zentinull.ingestors.fortigate import _fg_get

        auth = APIKeyAuth("fake_key", header_name="Authorization", prefix="Bearer")
        mock_resp = Mock()
        mock_resp.json.return_value = {"results": {"single": "item"}}
        mock_resp.raise_for_status.return_value = None

        with patch("zentinull.ingestors.fortigate.requests.get", return_value=mock_resp):
            result = _fg_get("/api/v2/monitor/system/interface", auth)

        assert isinstance(result, list)
        assert len(result) == 1
        assert result[0]["single"] == "item"

    def test_fg_get_returns_empty_on_error(self) -> None:
        """When API raises, _fg_get returns [] and logs error."""
        from zentinull.ingestors.auth import APIKeyAuth
        from zentinull.ingestors.fortigate import _fg_get

        auth = APIKeyAuth("fake_key", header_name="Authorization", prefix="Bearer")

        with patch("zentinull.ingestors.fortigate.requests.get", side_effect=Exception("connection error")):
            result = _fg_get("/api/v2/monitor/system/interface", auth)

        assert result == []

    def test_fg_get_non_list_results(self) -> None:
        """When results are neither dict nor list, return []."""
        from zentinull.ingestors.auth import APIKeyAuth
        from zentinull.ingestors.fortigate import _fg_get

        auth = APIKeyAuth("fake_key", header_name="Authorization", prefix="Bearer")
        mock_resp = Mock()
        mock_resp.json.return_value = {"results": "string_value"}
        mock_resp.raise_for_status.return_value = None

        with patch("zentinull.ingestors.fortigate.requests.get", return_value=mock_resp):
            result = _fg_get("/api/v2/monitor/system/interface", auth)

        assert result == []

    def test_fg_get_empty_results(self) -> None:
        """When results is an empty list, return []."""
        from zentinull.ingestors.auth import APIKeyAuth
        from zentinull.ingestors.fortigate import _fg_get

        auth = APIKeyAuth("fake_key", header_name="Authorization", prefix="Bearer")
        mock_resp = Mock()
        mock_resp.json.return_value = {"results": []}
        mock_resp.raise_for_status.return_value = None

        with patch("zentinull.ingestors.fortigate.requests.get", return_value=mock_resp):
            result = _fg_get("/api/v2/monitor/system/interface", auth)

        assert result == []


# ═══════════════════════════════════════════════════════════════════════════════
# ManageEngine ingest
# ═══════════════════════════════════════════════════════════════════════════════


class TestManageEngineIngest:
    """Mock-based tests for zentinull.ingestors.manageengine.ingest()."""

    def test_ingest_inserts_ec_and_mdm(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """When both EC and MDM return data, both tables get populated."""
        with _db_pair("me") as db_path:
            monkeypatch.setattr("zentinull.ingestors.manageengine.db", lambda _n: _db_from(db_path))

            mock_auth = Mock()
            mock_auth.refresh.return_value = True
            mock_auth.get_headers.return_value = {"Authorization": "Bearer fake"}

            fake_ec: list[dict[str, Any]] = [
                {
                    "resource_id": 123,
                    "serial_number": "SN-EC-001",
                    "mac_address": "00:11:22:33:44:55",
                    "name": "DESKTOP-ABC",
                    "manufacturer": "Dell",
                    "model": "OptiPlex 7090",
                    "os_name": "Windows 10",
                    "os_version": "10.0.19045",
                }
            ]
            fake_mdm: list[dict[str, Any]] = [
                {
                    "device_id": 456,
                    "serial_number": "SN-MDM-002",
                    "name": "iPhone 15",
                    "model": "Apple iPhone 15 Pro",
                }
            ]

            with (
                patch("zentinull.ingestors.manageengine._me_auth", return_value=mock_auth),
                patch("zentinull.ingestors.manageengine._me_fetch", return_value=fake_ec),
                patch("zentinull.ingestors.manageengine._mdm_fetch", return_value=fake_mdm),
            ):
                from zentinull.ingestors.manageengine import ingest

                count: int = ingest()

            assert count == 2
            verify = _db_from(db_path)
            try:
                assert verify.execute("SELECT name FROM computers").fetchone() is not None
                assert verify.execute("SELECT name FROM mdm_devices").fetchone() is not None
            finally:
                verify.close()

    def test_ingest_auth_failure_returns_zero(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """When OAuth refresh fails, ingest returns 0."""
        with _db_pair("me") as db_path:
            monkeypatch.setattr("zentinull.ingestors.manageengine.db", lambda _n: _db_from(db_path))

            mock_auth = Mock()
            mock_auth.refresh.return_value = False

            with patch("zentinull.ingestors.manageengine._me_auth", return_value=mock_auth):
                from zentinull.ingestors.manageengine import ingest

                count: int = ingest()

            assert count == 0


class TestManageEngineHelper:
    """Direct tests for manageengine helper functions."""

    def test_me_auth_creates_oauth(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """_me_auth() creates an OAuth2RefreshAuth with correct params."""
        monkeypatch.setenv("ME_CLIENT_ID", "test-client")
        monkeypatch.setenv("ME_CLIENT_SECRET", "test-secret")
        monkeypatch.setenv("ME_OAUTH_FILE", "/tmp/test_oauth.json")

        import importlib

        import zentinull.ingestors.manageengine as me_mod

        importlib.reload(me_mod)

        auth = me_mod._me_auth()
        assert auth._token_url == "https://accounts.zoho.com/oauth/v2/token"
        assert auth._client_id == "test-client"
        assert auth._client_secret == "test-secret"

    def test_me_fetch_paginates(self) -> None:
        """_me_fetch paginates through multiple pages."""
        from zentinull.ingestors.manageengine import _me_fetch

        auth = Mock()
        auth.get_headers.return_value = {"Authorization": "Bearer fake"}

        # First page returns items, second page returns empty (204)
        mock_resp_p1 = Mock()
        mock_resp_p1.status_code = 200
        mock_resp_p1.text = '{"data": [{"id": 1}, {"id": 2}]}'
        mock_resp_p1.json.return_value = {"data": [{"id": 1}, {"id": 2}]}

        mock_resp_p2 = Mock()
        mock_resp_p2.status_code = 204
        mock_resp_p2.text = ""

        with patch("zentinull.ingestors.manageengine.requests.get", side_effect=[mock_resp_p1, mock_resp_p2]):
            result = _me_fetch("https://example.com/api/computers", auth, response_path="data")

        assert len(result) == 2
        assert result[0]["id"] == 1
        assert result[1]["id"] == 2

    def test_me_fetch_204_breaks(self) -> None:
        """_me_fetch returns empty when first response is 204."""
        from zentinull.ingestors.manageengine import _me_fetch

        auth = Mock()
        auth.get_headers.return_value = {"Authorization": "Bearer fake"}

        mock_resp = Mock()
        mock_resp.status_code = 204
        mock_resp.text = ""

        with patch("zentinull.ingestors.manageengine.requests.get", return_value=mock_resp):
            result = _me_fetch("https://example.com/api/computers", auth)

        assert result == []

    def test_me_fetch_empty_items_breaks(self) -> None:
        """_me_fetch stops when items list is empty."""
        from zentinull.ingestors.manageengine import _me_fetch

        auth = Mock()
        auth.get_headers.return_value = {"Authorization": "Bearer fake"}

        mock_resp = Mock()
        mock_resp.status_code = 200
        mock_resp.text = '{"data": []}'
        mock_resp.json.return_value = {"data": []}

        with patch("zentinull.ingestors.manageengine.requests.get", return_value=mock_resp):
            result = _me_fetch("https://example.com/api/computers", auth, response_path="data")

        assert result == []

    def test_me_fetch_without_response_path(self) -> None:
        """_me_fetch returns full response when no response_path given."""
        from zentinull.ingestors.manageengine import _me_fetch

        auth = Mock()
        auth.get_headers.return_value = {"Authorization": "Bearer fake"}

        mock_resp = Mock()
        mock_resp.status_code = 200
        mock_resp.text = '[{"id": 1}]'
        mock_resp.json.return_value = [{"id": 1}]

        # Second page to break pagination
        mock_resp2 = Mock()
        mock_resp2.status_code = 204
        mock_resp2.text = ""

        with patch("zentinull.ingestors.manageengine.requests.get", side_effect=[mock_resp, mock_resp2]):
            result = _me_fetch("https://example.com/api/computers", auth)

        assert len(result) == 1

    def test_mdm_fetch_success(self) -> None:
        """_mdm_fetch returns JSON from MDM API."""
        from zentinull.ingestors.manageengine import _mdm_fetch

        auth = Mock()
        auth.get_headers.return_value = {"Authorization": "Bearer fake"}

        mock_resp = Mock()
        mock_resp.json.return_value = [{"device_id": 1, "name": "iPhone"}]
        mock_resp.raise_for_status.return_value = None

        with patch("zentinull.ingestors.manageengine.requests.get", return_value=mock_resp):
            result = _mdm_fetch(auth)

        assert len(result) == 1
        assert result[0]["device_id"] == 1


# ═══════════════════════════════════════════════════════════════════════════════
# ServiceDesk Plus ingest
# ═══════════════════════════════════════════════════════════════════════════════


class TestServiceDeskPlusIngest:
    """Mock-based tests for zentinull.ingestors.servicedeskplus.ingest()."""

    def test_ingest_inserts_all_tables(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Successful auth + fetch populates all SDP tables."""
        with _db_pair("sdp") as db_path:
            monkeypatch.setattr("zentinull.ingestors.servicedeskplus.db", lambda _n: _db_from(db_path))

            mock_auth = Mock()
            mock_auth.refresh.return_value = True
            mock_auth.get_headers.return_value = {"Authorization": "Bearer fake", "Accept": "application/json"}

            fake_items: list[dict[str, Any]] = [{"id": "1", "name": "Asset 1", "status": {"name": "In Production"}}]

            with (
                patch("zentinull.ingestors.servicedeskplus.OAuth2RefreshAuth", return_value=mock_auth),
                patch("zentinull.ingestors.servicedeskplus._sdp_fetch", return_value=fake_items),
            ):
                from zentinull.ingestors.servicedeskplus import ingest

                count: int = ingest()

            assert count == 4
            verify = _db_from(db_path)
            try:
                tables = verify.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
                assert len(tables) >= 3
            finally:
                verify.close()

    def test_ingest_auth_failure_returns_zero(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """When SDP OAuth refresh fails, ingest returns 0."""
        with _db_pair("sdp") as db_path:
            monkeypatch.setattr("zentinull.ingestors.servicedeskplus.db", lambda _n: _db_from(db_path))

            mock_auth = Mock()
            mock_auth.refresh.return_value = False

            with patch("zentinull.ingestors.servicedeskplus.OAuth2RefreshAuth", return_value=mock_auth):
                from zentinull.ingestors.servicedeskplus import ingest

                count: int = ingest()

            assert count == 0


# ═══════════════════════════════════════════════════════════════════════════════
# AD ingest
# ═══════════════════════════════════════════════════════════════════════════════


class TestADIngest:
    """Mock-based tests for zentinull.ingestors.ad.ingest()."""

    def test_ingest_inserts_computers(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """When LDAP returns entries, computers table is populated."""
        with _db_pair("ad") as db_path:
            monkeypatch.setattr("zentinull.ingestors.ad.db", lambda _n: _db_from(db_path))

            mock_entry = Mock()
            mock_entry.entry_attributes_as_dict = {
                "sAMAccountName": ["PC-001"],
                "dNSHostName": ["pc-001.moonlite.local"],
                "operatingSystem": ["Windows 10 Pro"],
                "operatingSystemVersion": ["10.0.19045"],
                "distinguishedName": ["CN=PC-001,CN=Computers,DC=moonlite,DC=local"],
                "lastLogonTimestamp": ["133500000000000000"],
                "whenCreated": ["2024-01-15 10:00:00"],
                "whenChanged": ["2026-07-11 08:00:00"],
            }
            mock_entry.entry_dn = "CN=PC-001,CN=Computers,DC=moonlite,DC=local"

            mock_ldap_conn = Mock()
            mock_ldap_conn.entries = [mock_entry]

            mock_auth = Mock()
            mock_auth.bind.return_value = mock_ldap_conn

            with patch("zentinull.ingestors.ad.LDAPBindAuth", return_value=mock_auth):
                from zentinull.ingestors.ad import ingest

                count: int = ingest()

            assert count == 1
            verify = _db_from(db_path)
            try:
                row = verify.execute("SELECT sam_account_name, dns_host_name FROM computers").fetchone()
                assert row is not None
                assert row["sam_account_name"] == "PC-001"
            finally:
                verify.close()

    def test_ingest_auth_failure_returns_zero(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """When LDAP bind fails, ingest returns 0."""
        with _db_pair("ad") as db_path:
            monkeypatch.setattr("zentinull.ingestors.ad.db", lambda _n: _db_from(db_path))

            mock_auth = Mock()
            mock_auth.bind.return_value = None

            with patch("zentinull.ingestors.ad.LDAPBindAuth", return_value=mock_auth):
                from zentinull.ingestors.ad import ingest

                count: int = ingest()

            assert count == 0

    def test_ingest_no_entries_returns_zero(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """When LDAP returns no entries, ingest returns 0."""
        with _db_pair("ad") as db_path:
            monkeypatch.setattr("zentinull.ingestors.ad.db", lambda _n: _db_from(db_path))

            mock_ldap_conn = Mock()
            mock_ldap_conn.entries = []

            mock_auth = Mock()
            mock_auth.bind.return_value = mock_ldap_conn

            with patch("zentinull.ingestors.ad.LDAPBindAuth", return_value=mock_auth):
                from zentinull.ingestors.ad import ingest

                count: int = ingest()

            assert count == 0
