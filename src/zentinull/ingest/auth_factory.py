"""Auth factory — builds auth objects from manifest Auth configs.

Maps Auth.kind to the appropriate auth class from ingestor/auth.py,
resolving env-var-backed config values from zentinull.config.
"""

from __future__ import annotations

from typing import Any

from ..config import (
    AD_PASSWORD,
    AD_SERVER,
    AD_USER,
    FG_API_KEY,
    ME_CLIENT_ID,
    ME_CLIENT_SECRET,
    ME_OAUTH_FILE,
    SDP_CLIENT_ID,
    SDP_CLIENT_SECRET,
    SDP_OAUTH_FILE,
    ZBX_TOKEN,
)
from ..ingestors.auth import APIKeyAuth, LDAPBindAuth, OAuth2RefreshAuth
from ..manifest.types import Auth


def build_auth(auth_cfg: Auth) -> Any:
    """Build an auth object from a manifest Auth config.

    Returns:
        Auth object with a ``get_headers()`` method (APIKeyAuth, OAuth2RefreshAuth)
        or a ``bind()`` method (LDAPBindAuth).
    """
    kind = auth_cfg.kind
    opts = auth_cfg.options

    if kind == "api_key":
        return APIKeyAuth(
            api_key=ZBX_TOKEN if opts.get("api_key") == "ZBX_TOKEN" else FG_API_KEY,
            header_name="Authorization",
            prefix="Bearer",
        )

    if kind == "oauth_refresh":
        # Determine which service from env-var names
        client_id_env = opts.get("client_id", "")
        if "SDP" in client_id_env:
            client_id = SDP_CLIENT_ID
            client_secret = SDP_CLIENT_SECRET
            token_file = SDP_OAUTH_FILE
        else:
            client_id = ME_CLIENT_ID
            client_secret = ME_CLIENT_SECRET
            token_file = ME_OAUTH_FILE
        return OAuth2RefreshAuth(
            "https://accounts.zoho.com/oauth/v2/token",
            client_id,
            client_secret,
            token_file=token_file,
        )

    if kind == "ldap":
        server = AD_SERVER if opts.get("server") == "AD_SERVER" else opts.get("server", "")
        user = AD_USER if opts.get("user") == "AD_USER" else opts.get("user", "")
        password = AD_PASSWORD if opts.get("password") == "AD_PASSWORD" else opts.get("password", "")
        return LDAPBindAuth(server, user, password)

    if kind == "none":
        return None

    raise ValueError(f"Unknown auth kind: {kind}")
