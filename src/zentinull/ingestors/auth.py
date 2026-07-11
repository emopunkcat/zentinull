"""Auth classes for source API authentication. Vendored from sentinull."""

from __future__ import annotations

import json
import time
from pathlib import Path
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
    """OAuth2 client-credentials with automatic token refresh."""

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

    def refresh(self) -> bool:
        try:
            data = {
                "grant_type": "client_credentials",
                "client_id": self._client_id,
                "client_secret": self._client_secret,
            }
            r = requests.post(self._token_url, data=data, timeout=30)
            r.raise_for_status()
            body = r.json()
            self._access_token = body["access_token"]
            self._expires_at = time.time() + body.get("expires_in", 3600) - 60
            if self._token_file:
                self._token_file.write_text(json.dumps(body))
            return True
        except Exception as e:
            log.error({"event": "oauth_refresh_failed", "token_url": self._token_url, "error": str(e)})
            return False

    def get_headers(self) -> dict[str, str]:
        if time.time() >= self._expires_at:
            self.refresh()
        return {"Authorization": f"Bearer {self._access_token}"}


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
