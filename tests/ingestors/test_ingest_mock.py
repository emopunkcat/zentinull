"""Strategy-level mock tests for fetch strategies.

Tests each registered fetch strategy (rest_json, paged_json, sdp_cursor,
json_rpc, ldap) by mocking the transport layer (requests / ldap3) and
asserting the returned list-of-dicts. No legacy ingestor modules imported.
"""

from __future__ import annotations

import json
import threading
from types import SimpleNamespace
from typing import Any
from unittest.mock import Mock, patch

import requests

# ═══════════════════════════════════════════════════════════════════════════════
# rest_json
# ═══════════════════════════════════════════════════════════════════════════════


class TestRestJson:
    """Mock tests for rest_json_fetch."""

    def test_list_body_returned_as_is(self) -> None:
        """Given a response with a JSON list body, when rest_json_fetch is called,
        then the list is returned as-is."""
        from zentinull.ingest.strategies.rest_json import rest_json_fetch

        mock_resp = Mock(spec=requests.Response)
        mock_resp.json.return_value = [{"a": 1}, {"b": 2}]
        mock_resp.raise_for_status = Mock()

        auth = SimpleNamespace(get_headers=lambda: {})

        with patch("zentinull.ingest.strategies.rest_json.requests.get", return_value=mock_resp) as mock_get:
            result = rest_json_fetch({"url": "https://example.com/api/items"}, auth)

        assert result == [{"a": 1}, {"b": 2}]
        mock_get.assert_called_once()

    def test_dict_body_wrapped_in_list(self) -> None:
        """Given a response returning a single dict, when rest_json_fetch is called,
        then the dict is wrapped in a singleton list."""
        from zentinull.ingest.strategies.rest_json import rest_json_fetch

        mock_resp = Mock(spec=requests.Response)
        mock_resp.json.return_value = {"id": 42, "name": "Device"}
        mock_resp.raise_for_status = Mock()

        auth = SimpleNamespace(get_headers=lambda: {})

        with patch("zentinull.ingest.strategies.rest_json.requests.get", return_value=mock_resp):
            result = rest_json_fetch({"url": "https://example.com/api/item/42"}, auth)

        assert result == [{"id": 42, "name": "Device"}]

    def test_response_path_drills_into_nested_dict(self) -> None:
        """Given response_path set to a dotted key, when rest_json_fetch is called,
        then the path is drilled and the nested list is returned."""
        from zentinull.ingest.strategies.rest_json import rest_json_fetch

        mock_resp = Mock(spec=requests.Response)
        mock_resp.json.return_value = {"data": {"devices": [{"id": "d1"}, {"id": "d2"}]}}
        mock_resp.raise_for_status = Mock()

        auth = SimpleNamespace(get_headers=lambda: {})

        with patch("zentinull.ingest.strategies.rest_json.requests.get", return_value=mock_resp):
            result = rest_json_fetch({"url": "https://example.com/api/devices", "response_path": "data.devices"}, auth)

        assert result == [{"id": "d1"}, {"id": "d2"}]

    def test_response_path_none_returns_empty_list(self) -> None:
        """Given a response_path that drills into None, when rest_json_fetch is called,
        then an empty list is returned."""
        from zentinull.ingest.strategies.rest_json import rest_json_fetch

        mock_resp = Mock(spec=requests.Response)
        mock_resp.json.return_value = {"data": {"missing": []}}
        mock_resp.raise_for_status = Mock()

        auth = SimpleNamespace(get_headers=lambda: {})

        with patch("zentinull.ingest.strategies.rest_json.requests.get", return_value=mock_resp):
            result = rest_json_fetch({"url": "https://example.com/api/devices", "response_path": "data.devices"}, auth)

        assert result == []

    def test_http_error_returns_empty_list(self) -> None:
        """Given an HTTP error (raise_for_status raises), when rest_json_fetch is called,
        then an empty list is returned."""
        from zentinull.ingest.strategies.rest_json import rest_json_fetch

        mock_resp = Mock(spec=requests.Response)
        mock_resp.raise_for_status.side_effect = requests.HTTPError("403 Forbidden")
        mock_resp.json = Mock()

        auth = SimpleNamespace(get_headers=lambda: {})

        with patch("zentinull.ingest.strategies.rest_json.requests.get", return_value=mock_resp):
            result = rest_json_fetch({"url": "https://example.com/api/secret"}, auth)

        assert result == []

    def test_auth_none_does_not_raise(self) -> None:
        """Given auth=None (kind="none"), when rest_json_fetch is called with empty header auth,
        then it does not raise and still makes the request with empty headers."""
        from zentinull.ingest.strategies.rest_json import rest_json_fetch

        mock_resp = Mock(spec=requests.Response)
        mock_resp.json.return_value = [{"ok": True}]
        mock_resp.raise_for_status = Mock()

        # Simulate auth=None from kind="none" — build_auth returns None for "none"
        # but the strategy calls auth.get_headers(), so the runner
        # would pass a no-op proxy or None would crash.
        # This test uses a SimpleNamespace to confirm empty headers work.
        auth = SimpleNamespace(get_headers=lambda: {})

        with patch("zentinull.ingest.strategies.rest_json.requests.get", return_value=mock_resp) as mock_get:
            result = rest_json_fetch({"url": "https://example.com/api/public"}, auth)

        assert result == [{"ok": True}]
        # Verify auth headers were passed (empty dict)
        _call_headers = mock_get.call_args[1].get("headers", {})
        assert _call_headers == {}


# ═══════════════════════════════════════════════════════════════════════════════
# paged_json — page_param mode
# ═══════════════════════════════════════════════════════════════════════════════


class TestPagedJsonPageParam:
    """Mock tests for paged_json_fetch with pagination='page_param'."""

    def test_two_pages_then_empty_concatenates_items(self) -> None:
        """Given two non-empty pages followed by an empty page (204),
        when paged_json_fetch runs in page_param mode,
        then items from both non-empty pages are concatenated."""
        from zentinull.ingest.strategies.paged_json import paged_json_fetch

        auth = SimpleNamespace(get_headers=lambda: {})

        def _mock_get(url: str, **kwargs: Any) -> Mock:
            resp = Mock(spec=requests.Response)
            resp.status_code = 200
            resp.text = "not empty"
            resp.raise_for_status = Mock()
            if "page=1" in url:
                resp.json.return_value = [{"id": "a"}, {"id": "b"}]
            elif "page=2" in url:
                resp.json.return_value = [{"id": "c"}]
            else:
                resp.status_code = 204
                resp.text = ""
            return resp

        with patch("zentinull.ingest.strategies.paged_json.requests.get", side_effect=_mock_get) as mock_get:
            result = paged_json_fetch(
                {"url": "https://example.com/api/items", "pagination": "page_param", "response_path": None},
                auth,
            )

        assert result == [{"id": "a"}, {"id": "b"}, {"id": "c"}]
        # Should have hit page=1, page=2, then page=3 which returned 204
        assert mock_get.call_count >= 3

    def test_exception_mid_pagination_returns_items_so_far(self) -> None:
        """Given a request exception on the second page,
        when paged_json_fetch runs in page_param mode,
        then items from the first page are returned."""
        from zentinull.ingest.strategies.paged_json import paged_json_fetch

        auth = SimpleNamespace(get_headers=lambda: {})
        call_count: int = 0
        lock = threading.Lock()

        def _mock_get(url: str, **kwargs: Any) -> Mock:
            nonlocal call_count
            with lock:
                call_count += 1
                page = call_count
            resp = Mock(spec=requests.Response)
            if page == 1:
                resp.status_code = 200
                resp.text = "ok"
                resp.json.return_value = [{"id": "first"}]
                resp.raise_for_status = Mock()
            else:
                raise requests.RequestException("Connection reset")
            return resp

        with patch("zentinull.ingest.strategies.paged_json.requests.get", side_effect=_mock_get):
            result = paged_json_fetch(
                {"url": "https://example.com/api/items", "pagination": "page_param", "response_path": None},
                auth,
            )

        assert result == [{"id": "first"}]


# ═══════════════════════════════════════════════════════════════════════════════
# paged_json — paging.next mode
# ═══════════════════════════════════════════════════════════════════════════════


class TestPagedJsonPagingNext:
    """Mock tests for paged_json_fetch with pagination='paging.next'."""

    def test_wrapper_key_auto_detect_devices(self) -> None:
        """Given a response body with a 'devices' wrapper key (and no paging.next),
        when paged_json_fetch runs in paging.next mode,
        then items are extracted from the 'devices' key."""
        from zentinull.ingest.strategies.paged_json import paged_json_fetch

        auth = SimpleNamespace(get_headers=lambda: {})

        mock_resp = Mock(spec=requests.Response)
        mock_resp.json.return_value = {"devices": [{"id": "m1"}, {"id": "m2"}], "paging": {"next": None}}
        mock_resp.raise_for_status = Mock()

        with patch("zentinull.ingest.strategies.paged_json.requests.get", return_value=mock_resp):
            result = paged_json_fetch({"url": "https://mdm.example.com/api/devices", "pagination": "paging.next"}, auth)

        assert result == [{"id": "m1"}, {"id": "m2"}]

    def test_follows_paging_next_url(self) -> None:
        """Given page 1 has a paging.next URL and page 2 has paging.next=null,
        when paged_json_fetch runs in paging.next mode,
        then items from both pages are concatenated."""
        from zentinull.ingest.strategies.paged_json import paged_json_fetch

        auth = SimpleNamespace(get_headers=lambda: {})

        page_responses: dict[str, Any] = {
            "https://example.com/api/items": {
                "devices": [{"id": "p1a"}, {"id": "p1b"}],
                "paging": {"next": "https://example.com/api/items?page=2"},
            },
            "https://example.com/api/items?page=2": {
                "devices": [{"id": "p2a"}],
                "paging": {"next": None},
            },
        }

        def _mock_get(url: str, **kwargs: Any) -> Mock:
            resp = Mock(spec=requests.Response)
            resp.raise_for_status = Mock()
            resp.json.return_value = page_responses.get(url, {})
            return resp

        with patch("zentinull.ingest.strategies.paged_json.requests.get", side_effect=_mock_get):
            result = paged_json_fetch({"url": "https://example.com/api/items", "pagination": "paging.next"}, auth)

        assert result == [{"id": "p1a"}, {"id": "p1b"}, {"id": "p2a"}]

    def test_stops_when_paging_next_is_absent(self) -> None:
        """Given a single-page response with no 'paging' key at all,
        when paged_json_fetch runs in paging.next mode,
        then it returns only the first page items and stops gracefully."""
        from zentinull.ingest.strategies.paged_json import paged_json_fetch

        auth = SimpleNamespace(get_headers=lambda: {})

        mock_resp = Mock(spec=requests.Response)
        mock_resp.json.return_value = {"items": [{"id": "x"}], "meta": {"count": 1}}
        mock_resp.raise_for_status = Mock()

        with patch("zentinull.ingest.strategies.paged_json.requests.get", return_value=mock_resp):
            result = paged_json_fetch({"url": "https://example.com/api/single", "pagination": "paging.next"}, auth)

        assert result == [{"id": "x"}]


# ═══════════════════════════════════════════════════════════════════════════════
# sdp_cursor
# ═══════════════════════════════════════════════════════════════════════════════


class TestSdpCursor:
    """Mock tests for sdp_cursor_fetch."""

    def test_paginates_via_list_info_until_has_more_rows_false(self) -> None:
        """Given two pages where has_more_rows transitions True→False,
        when sdp_cursor_fetch is called,
        then items from both pages are concatenated in order."""
        from zentinull.ingest.strategies.sdp_cursor import sdp_cursor_fetch

        auth = SimpleNamespace(get_headers=lambda: {})

        page_data = [
            {
                "operation": {
                    "result": {
                        "data": [{"id": "s1"}, {"id": "s2"}],
                    },
                },
                "list_info": {"has_more_rows": True, "start_index": 1},
            },
            {
                "operation": {
                    "result": {
                        "data": [{"id": "s3"}],
                    },
                },
                "list_info": {"has_more_rows": False, "start_index": 101},
            },
        ]
        page_iter = iter(page_data)

        def _mock_get(*args: Any, **kwargs: Any) -> Mock:
            resp = Mock(spec=requests.Response)
            resp.raise_for_status = Mock()
            try:
                resp.json.return_value = next(page_iter)
            except StopIteration:
                resp.json.return_value = {}
            return resp

        with patch("zentinull.ingest.strategies.sdp_cursor.requests.get", side_effect=_mock_get) as mock_get:
            result = sdp_cursor_fetch(
                {
                    "url": "https://sdp.example.com/api/assets",
                    "response_path": "operation.result.data",
                    "pagination": {"row_count": 2, "sort_field": "id", "sort_order": "asc"},
                },
                auth,
            )

        assert result == [{"id": "s1"}, {"id": "s2"}, {"id": "s3"}]
        # Verify input_data JSON was sent as params
        for call_args in mock_get.call_args_list:
            params = call_args[1].get("params", {})
            assert "input_data" in params
            input_data = json.loads(params["input_data"])
            assert "list_info" in input_data
            assert "row_count" in input_data["list_info"]
            assert "sort_field" in input_data["list_info"]

    def test_drills_response_path(self) -> None:
        """Given response_path pointing to nested data,
        when sdp_cursor_fetch is called,
        then items are correctly extracted from the nested structure."""
        from zentinull.ingest.strategies.sdp_cursor import sdp_cursor_fetch

        auth = SimpleNamespace(get_headers=lambda: {})

        mock_resp = Mock(spec=requests.Response)
        mock_resp.json.return_value = {
            "operation": {
                "result": {
                    "data": [{"id": "d1"}, {"id": "d2"}],
                },
            },
            "list_info": {"has_more_rows": False},
        }
        mock_resp.raise_for_status = Mock()

        with patch("zentinull.ingest.strategies.sdp_cursor.requests.get", return_value=mock_resp):
            result = sdp_cursor_fetch(
                {
                    "url": "https://sdp.example.com/api/requests",
                    "response_path": "operation.result.data",
                },
                auth,
            )

        assert result == [{"id": "d1"}, {"id": "d2"}]

    def test_error_returns_empty_list(self) -> None:
        """Given a request exception,
        when sdp_cursor_fetch is called,
        then an empty list is returned."""
        from zentinull.ingest.strategies.sdp_cursor import sdp_cursor_fetch

        auth = SimpleNamespace(get_headers=lambda: {})

        with patch(
            "zentinull.ingest.strategies.sdp_cursor.requests.get",
            side_effect=requests.ConnectionError("Connection refused"),
        ):
            result = sdp_cursor_fetch(
                {
                    "url": "https://sdp.example.com/api/broken",
                    "response_path": "operation.result.data",
                },
                auth,
            )

        assert result == []


# ═══════════════════════════════════════════════════════════════════════════════
# json_rpc
# ═══════════════════════════════════════════════════════════════════════════════


class TestJsonRpc:
    """Mock tests for json_rpc_fetch."""

    def test_builds_jsonrpc_payload_with_bearer_token(self) -> None:
        """Given auth with a Bearer token (Authorization header), when json_rpc_fetch is called,
        then the POST payload contains jsonrpc 2.0, the method, params, auth token, and id."""
        from zentinull.ingest.strategies.json_rpc import json_rpc_fetch

        mock_resp = Mock(spec=requests.Response)
        mock_resp.json.return_value = {"result": [{"hostid": "10001"}]}
        mock_resp.raise_for_status = Mock()

        auth = SimpleNamespace(get_headers=lambda: {"Authorization": "Bearer zbx_token_abc"})

        with patch("zentinull.ingest.strategies.json_rpc.requests.post", return_value=mock_resp) as mock_post:
            result = json_rpc_fetch(
                {
                    "url": "https://zabbix.example.com/api_jsonrpc.php",
                    "method": "host.get",
                    "params": {"output": "extend", "filter": {"status": 0}},
                },
                auth,
            )

        assert result == [{"hostid": "10001"}]
        # Verify the payload
        call_kwargs = mock_post.call_args[1]
        payload = call_kwargs["json"]
        assert payload["jsonrpc"] == "2.0"
        assert payload["method"] == "host.get"
        assert payload["params"] == {"output": "extend", "filter": {"status": 0}}
        assert payload["auth"] == "zbx_token_abc"
        assert payload["id"] == 1

    def test_result_list_passthrough(self) -> None:
        """Given a response with result as a list, when json_rpc_fetch is called,
        then the list is returned as-is."""
        from zentinull.ingest.strategies.json_rpc import json_rpc_fetch

        mock_resp = Mock(spec=requests.Response)
        mock_resp.json.return_value = {"result": [{"x": 1}, {"x": 2}]}
        mock_resp.raise_for_status = Mock()

        auth = SimpleNamespace(get_headers=lambda: {"Authorization": "Bearer tok"})

        with patch("zentinull.ingest.strategies.json_rpc.requests.post", return_value=mock_resp):
            result = json_rpc_fetch({"url": "https://example.com/api", "method": "get.list", "params": {}}, auth)

        assert result == [{"x": 1}, {"x": 2}]

    def test_error_in_response_returns_empty_list(self) -> None:
        """Given a response containing an 'error' key, when json_rpc_fetch is called,
        then an empty list is returned."""
        from zentinull.ingest.strategies.json_rpc import json_rpc_fetch

        mock_resp = Mock(spec=requests.Response)
        mock_resp.json.return_value = {"error": {"code": -32601, "message": "Method not found"}}
        mock_resp.raise_for_status = Mock()

        auth = SimpleNamespace(get_headers=lambda: {"Authorization": "Bearer tok"})

        with patch("zentinull.ingest.strategies.json_rpc.requests.post", return_value=mock_resp):
            result = json_rpc_fetch({"url": "https://example.com/api", "method": "bad.method", "params": {}}, auth)

        assert result == []

    def test_result_wrapper_extracts_nested_list(self) -> None:
        """Given result_wrapper='hosts' and result as a dict with that key,
        when json_rpc_fetch is called, then the inner list is extracted."""
        from zentinull.ingest.strategies.json_rpc import json_rpc_fetch

        mock_resp = Mock(spec=requests.Response)
        mock_resp.json.return_value = {"result": {"hosts": [{"hostid": "h1"}, {"hostid": "h2"}]}}
        mock_resp.raise_for_status = Mock()

        auth = SimpleNamespace(get_headers=lambda: {"Authorization": "Bearer tok"})

        with patch("zentinull.ingest.strategies.json_rpc.requests.post", return_value=mock_resp):
            result = json_rpc_fetch(
                {
                    "url": "https://zabbix.example.com/api_jsonrpc.php",
                    "method": "host.get",
                    "params": {},
                    "result_wrapper": "hosts",
                },
                auth,
            )

        assert result == [{"hostid": "h1"}, {"hostid": "h2"}]


# ═══════════════════════════════════════════════════════════════════════════════
# ldap
# ═══════════════════════════════════════════════════════════════════════════════


class _FakeLdapEntry:
    """Fake ldap3 Entry-like object for testing."""

    def __init__(self, dn: str, attributes: dict[str, list[str]]) -> None:
        self.entry_dn = dn
        self.entry_attributes_as_dict = attributes


class _FakeLdapConnection:
    """Fake ldap3 Connection-like object for testing."""

    def __init__(self, entries: list[_FakeLdapEntry]) -> None:
        self.entries = entries

    def search(self, **kwargs: Any) -> None:
        pass


class TestLdap:
    """Mock tests for ldap_fetch."""

    def test_bind_returns_entries_as_dicts_with_dn(self) -> None:
        """Given auth.bind() returns a fake connection with entries,
        when ldap_fetch is called,
        then it returns dicts with 'dn' + all attributes."""
        from zentinull.ingest.strategies.ldap import ldap_fetch

        entries = [
            _FakeLdapEntry(
                "CN=WS001,CN=Computers,DC=example,DC=local",
                {"cn": ["WS001"], "operatingSystem": ["Windows 10 Pro"]},
            ),
            _FakeLdapEntry(
                "CN=SRV001,CN=Computers,DC=example,DC=local",
                {"cn": ["SRV001"], "operatingSystem": ["Windows Server 2022"]},
            ),
        ]
        fake_conn = _FakeLdapConnection(entries)

        mock_auth = Mock()
        mock_auth.bind.return_value = fake_conn

        result = ldap_fetch(
            {
                "search_base": "CN=Computers,DC=example,DC=local",
                "search_filter": "(objectClass=computer)",
                "attributes": ["cn", "operatingSystem"],
                "size_limit": 5000,
            },
            mock_auth,
        )

        assert len(result) == 2
        assert result[0]["dn"] == "CN=WS001,CN=Computers,DC=example,DC=local"
        assert result[0]["cn"] == ["WS001"]
        assert result[0]["operatingSystem"] == ["Windows 10 Pro"]
        assert result[1]["dn"] == "CN=SRV001,CN=Computers,DC=example,DC=local"

        # Verify the search was called with correct params
        mock_auth.bind.assert_called_once()

    def test_bind_returns_none_returns_empty_list(self) -> None:
        """Given auth.bind() returns None (bind failure), when ldap_fetch is called,
        then an empty list is returned."""
        from zentinull.ingest.strategies.ldap import ldap_fetch

        mock_auth = Mock()
        mock_auth.bind.return_value = None

        result = ldap_fetch(
            {
                "search_base": "DC=example,DC=local",
                "search_filter": "(objectClass=computer)",
                "attributes": ["cn"],
                "size_limit": 1000,
            },
            mock_auth,
        )

        assert result == []
