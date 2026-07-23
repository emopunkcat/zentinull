"""Auth factory — builds auth objects from manifest Auth configs.

Maps Auth.kind to the appropriate auth class from ingestor/auth.py,
resolving env-var-backed config values from zentinull.config.
"""

from __future__ import annotations

from typing import Any

from ..config import get_config
from ..ingestors.auth import APIKeyAuth, LDAPBindAuth, OAuth2RefreshAuth
from ..manifest.types import Auth


def build_auth(auth_cfg: Auth) -> Any:
    """Build an auth object from a manifest Auth config.

    Returns:
        Auth object with a ``get_headers()`` method (APIKeyAuth, OAuth2RefreshAuth)
        or a ``bind()`` method (LDAPBindAuth).
    """
    cfg = get_config()
    kind = auth_cfg.kind
    opts = auth_cfg.options

    if kind == "api_key":
        return APIKeyAuth(
            api_key=cfg.zbx_token if opts.get("api_key") == "ZBX_TOKEN" else cfg.fg_api_key,
            header_name="Authorization",
            prefix="Bearer",
        )

    if kind == "oauth_refresh":
        # Determine which service from env-var names
        client_id_env = opts.get("client_id", "")
        if "SDP" in client_id_env:
            client_id = cfg.sdp_client_id
            client_secret = cfg.sdp_client_secret
            token_file = cfg.sdp_oauth_file
        else:
            client_id = cfg.me_client_id
            client_secret = cfg.me_client_secret
            token_file = cfg.me_oauth_file
        return OAuth2RefreshAuth(
            "https://accounts.zoho.com/oauth/v2/token",
            client_id,
            client_secret,
            token_file=token_file,
        )

    if kind == "ldap":
        server = cfg.ad_server if opts.get("server") == "AD_SERVER" else opts.get("server", "")
        user = cfg.ad_user if opts.get("user") == "AD_USER" else opts.get("user", "")
        password = cfg.ad_password if opts.get("password") == "AD_PASSWORD" else opts.get("password", "")
        return LDAPBindAuth(server, user, password)

    if kind == "none":
        return None

    raise ValueError(f"Unknown auth kind: {kind}")
