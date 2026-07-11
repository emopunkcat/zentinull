"""Tests for ingestors.auth — APIKeyAuth, OAuth2RefreshAuth, LDAPBindAuth."""

from __future__ import annotations

import json
import tempfile
import time
from pathlib import Path
from unittest.mock import MagicMock, patch


class TestAPIKeyAuth:
    def test_apikey_auth_bearer_default(self) -> None:
        from zentinull.ingestors.auth import APIKeyAuth

        auth = APIKeyAuth("abc")
        headers = auth.get_headers()
        assert headers == {"Authorization": "Bearer abc"}

    def test_apikey_auth_custom_header_prefix(self) -> None:
        from zentinull.ingestors.auth import APIKeyAuth

        auth = APIKeyAuth("key", header_name="X-API-Key", prefix="")
        headers = auth.get_headers()
        assert headers == {"X-API-Key": " key"}


class TestOAuth2RefreshAuth:
    def test_oauth2_refresh_success(self) -> None:
        from zentinull.ingestors.auth import OAuth2RefreshAuth

        mock_response = MagicMock()
        mock_response.json.return_value = {"access_token": "tok", "expires_in": 3600}
        mock_response.raise_for_status = MagicMock()

        with patch("zentinull.ingestors.auth.requests.post", return_value=mock_response):
            auth = OAuth2RefreshAuth(
                token_url="https://example.com/token",
                client_id="cid",
                client_secret="secret",
            )
            result = auth.refresh()

        assert result is True
        assert auth._access_token == "tok"
        # _expires_at should be roughly time.time() + 3600 - 60
        now = time.time()
        expected = now + 3600 - 60
        assert abs(auth._expires_at - expected) < 5

    def test_oauth2_refresh_default_expires_in(self) -> None:
        from zentinull.ingestors.auth import OAuth2RefreshAuth

        mock_response = MagicMock()
        mock_response.json.return_value = {"access_token": "tok"}  # no expires_in
        mock_response.raise_for_status = MagicMock()

        with patch("zentinull.ingestors.auth.requests.post", return_value=mock_response):
            auth = OAuth2RefreshAuth(
                token_url="https://example.com/token",
                client_id="cid",
                client_secret="secret",
            )
            result = auth.refresh()

        assert result is True
        assert auth._access_token == "tok"
        now = time.time()
        expected = now + 3600 - 60  # defaults to 3600
        assert abs(auth._expires_at - expected) < 5

    def test_oauth2_refresh_writes_token_file(self) -> None:
        from zentinull.ingestors.auth import OAuth2RefreshAuth

        mock_response = MagicMock()
        mock_response.json.return_value = {"access_token": "tok", "expires_in": 3600}
        mock_response.raise_for_status = MagicMock()

        with tempfile.NamedTemporaryFile(delete=False, suffix=".json") as tmp:
            token_path = Path(tmp.name)

        try:
            with patch("zentinull.ingestors.auth.requests.post", return_value=mock_response):
                auth = OAuth2RefreshAuth(
                    token_url="https://example.com/token",
                    client_id="cid",
                    client_secret="secret",
                    token_file=token_path,
                )
                auth.refresh()

            written = json.loads(token_path.read_text())
            assert written["access_token"] == "tok"
            assert written["expires_in"] == 3600
        finally:
            token_path.unlink(missing_ok=True)

    def test_oauth2_refresh_failure(self) -> None:
        from zentinull.ingestors.auth import OAuth2RefreshAuth

        mock_response = MagicMock()
        mock_response.raise_for_status.side_effect = Exception("HTTP error")

        with patch("zentinull.ingestors.auth.requests.post", return_value=mock_response):
            auth = OAuth2RefreshAuth(
                token_url="https://example.com/token",
                client_id="cid",
                client_secret="secret",
            )
            result = auth.refresh()

        assert result is False

    def test_oauth2_get_headers_before_expiry(self) -> None:
        from zentinull.ingestors.auth import OAuth2RefreshAuth

        auth = OAuth2RefreshAuth(
            token_url="https://example.com/token",
            client_id="cid",
            client_secret="secret",
        )
        auth._access_token = "existing"
        auth._expires_at = time.time() + 100000  # far future

        headers = auth.get_headers()
        assert headers == {"Authorization": "Bearer existing"}
        assert auth._access_token == "existing"  # unchanged — refresh NOT called

    def test_oauth2_get_headers_after_expiry(self) -> None:
        from zentinull.ingestors.auth import OAuth2RefreshAuth

        mock_response = MagicMock()
        mock_response.json.return_value = {"access_token": "new_token", "expires_in": 3600}
        mock_response.raise_for_status = MagicMock()

        auth = OAuth2RefreshAuth(
            token_url="https://example.com/token",
            client_id="cid",
            client_secret="secret",
        )
        auth._access_token = "old"
        auth._expires_at = 0  # epoch — always expired

        with patch("zentinull.ingestors.auth.requests.post", return_value=mock_response):
            headers = auth.get_headers()

        assert headers == {"Authorization": "Bearer new_token"}
        assert auth._access_token == "new_token"


class TestLDAPBindAuth:
    def test_ldap_bind_url_parsing_ldap(self) -> None:
        from zentinull.ingestors.auth import LDAPBindAuth

        with patch("zentinull.ingestors.auth.ldap3.Server") as mock_server:
            LDAPBindAuth("ldap://server:389", "user", "pass")

        call_kwargs = mock_server.call_args.kwargs
        assert call_kwargs["port"] == 389
        assert call_kwargs["use_ssl"] is False

    def test_ldap_bind_url_parsing_ldaps(self) -> None:
        from zentinull.ingestors.auth import LDAPBindAuth

        with patch("zentinull.ingestors.auth.ldap3.Server") as mock_server:
            LDAPBindAuth("ldaps://server:636", "user", "pass")

        call_kwargs = mock_server.call_args.kwargs
        assert call_kwargs["port"] == 636
        assert call_kwargs["use_ssl"] is True

    def test_ldap_bind_default_port_non_standard(self) -> None:
        from zentinull.ingestors.auth import LDAPBindAuth

        with patch("zentinull.ingestors.auth.ldap3.Server") as mock_server:
            LDAPBindAuth("ldap://server:1234", "user", "pass")

        call_kwargs = mock_server.call_args.kwargs
        assert call_kwargs["port"] == 1234
