"""Auth classes for source API authentication. Vendored from sentinull."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import ldap3
import requests

from ..logging_config import get_logger

log = get_logger("ingest.auth")


class APIKeyAuth:
    """Bearer token / static API key auth."""

    def __init__(
        self,
        api_key: str,
        header_name: str = "Authorization",
        prefix: str = "Bearer",
    ) -> None:
        self._api_key = api_key
        self._header = header_name
        self._prefix = prefix

    def get_headers(self) -> dict[str, str]:
        return {self._header: f"{self._prefix} {self._api_key}"}


class OAuth2RefreshAuth:
    """OAuth2 client-credentials with automatic token refresh.

    Supports two flows:
    1. Client credentials (no token file) — grants client_credentials
    2. Refresh token flow (token file with refresh_token) — grants refresh_token

    Token file is auto-updated on successful refresh.
    Authorization header uses the scheme from the token_url (e.g. Zoho-oauthtoken for Zoho).
    """

    def __init__(
        self,
        token_url: str,
        client_id: str,
        client_secret: str,
        token_file: str | Path | None = None,
    ) -> None:
        self._token_url = token_url
        self._client_id = client_id
        self._client_secret = client_secret
        self._token_file = Path(token_file) if token_file else None
        self._access_token: str | None = None
        self._refresh_token: str | None = None
        self._expires_at: float = 0
        self._auth_scheme = "Bearer"
        self._load_token()

    def _load_token(self) -> None:
        """Load tokens from file if it exists."""
        if not self._token_file or not self._token_file.exists():
            return
        try:
            data = json.loads(self._token_file.read_text())
            self._access_token = data.get("access_token")
            self._refresh_token = data.get("refresh_token")
            self._auth_scheme = data.get("token_type", "Bearer")
            if "expires_at" in data:
                self._expires_at = data["expires_at"]
            elif "expires_in" in data:
                self._expires_at = time.time() + data["expires_in"] - 60
            elif data.get("access_token"):
                # No expiry info — assume token is recently provisioned and valid
                # for a default 55-minute window to avoid immediate refresh.
                self._expires_at = time.time() + 3300
        except Exception as e:
            log.warning({"event": "oauth_load_failed", "token_file": str(self._token_file), "error": str(e)})

    def _save_token(self, data: dict[str, Any]) -> None:
        """Persist token data to file atomically."""
        if not self._token_file:
            return
        try:
            tmp = self._token_file.with_suffix(".tmp")
            tmp.write_text(json.dumps(data))
            os.replace(tmp, self._token_file)
        except Exception as e:
            log.warning({"event": "oauth_save_failed", "token_file": str(self._token_file), "error": str(e)})

    def refresh(self) -> bool:
        """Refresh the access token.

        Uses refresh_token grant if a refresh_token is available,
        otherwise falls back to client_credentials grant.
        """
        try:
            if self._refresh_token:
                data = {
                    "grant_type": "refresh_token",
                    "client_id": self._client_id,
                    "client_secret": self._client_secret,
                    "refresh_token": self._refresh_token,
                }
            else:
                data = {
                    "grant_type": "client_credentials",
                    "client_id": self._client_id,
                    "client_secret": self._client_secret,
                }
            r = requests.post(self._token_url, data=data, timeout=(15, 30))
            r.raise_for_status()
            body = r.json()
            # Zoho returns HTTP 200 with {"error":"invalid_grant"} for expired/revoked tokens
            err = body.get("error")
            if err:
                error_description = body.get("error_description", err)
                log.error({"event": "oauth_refresh_failed", "token_url": self._token_url, "error": error_description})
                return False
            self._access_token = body["access_token"]
            self._expires_at = time.time() + body.get("expires_in", 3600) - 60
            # Capture token_type from server response (e.g. Zoho-oauthtoken)
            if "token_type" in body:
                self._auth_scheme = body["token_type"]
            # Update refresh_token if server returned a new one
            if "refresh_token" in body:
                self._refresh_token = body["refresh_token"]
            token_data = {
                "access_token": self._access_token,
                "refresh_token": self._refresh_token,
                "expires_at": self._expires_at,
                "token_type": self._auth_scheme,
            }
            self._save_token(token_data)
            return True
        except requests.HTTPError as e:
            detail = "http_error"
            try:
                if e.response is not None:
                    detail = str(e.response.json())
            except Exception:
                detail = str(e)
            log.error({"event": "oauth_refresh_failed", "token_url": self._token_url, "error": detail})
            return False
        except Exception as e:
            log.error({"event": "oauth_refresh_failed", "token_url": self._token_url, "error": str(e)})
            return False

    def get_headers(self) -> dict[str, str]:
        if time.time() >= self._expires_at:
            self.refresh()
        return {"Authorization": f"{self._auth_scheme} {self._access_token}"}


class LDAPBindAuth:
    """Simple LDAP bind auth."""

    def __init__(self, server: str, user: str, password: str) -> None:
        parsed = urlparse(server)
        self._server = ldap3.Server(
            parsed.hostname,
            port=parsed.port or 389,
            use_ssl=parsed.scheme == "ldaps",
            get_info=ldap3.NONE,
        )
        self._user = user
        self._password = password
        self._conn: ldap3.Connection | None = None

    def bind(self) -> ldap3.Connection | None:
        try:
            conn = ldap3.Connection(self._server, self._user, self._password, auto_bind=True)
            self._conn = conn
            return conn
        except Exception as e:
            log.error({"event": "ldap_bind_failed", "server": self._server.host, "user": self._user, "error": str(e)})
            return None
