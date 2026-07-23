"""Unit tests for src/zentinull/ingest/auth_factory.py."""

from __future__ import annotations

import pytest

from zentinull.ingest.auth_factory import build_auth
from zentinull.ingestors.auth import APIKeyAuth, LDAPBindAuth, OAuth2RefreshAuth
from zentinull.manifest.types import Auth


def test_build_auth_none():
    auth_cfg = Auth(kind="none")
    assert build_auth(auth_cfg) is None


def test_build_auth_api_key():
    auth_cfg = Auth(kind="api_key", options={"api_key": "FG_API_KEY"})
    obj = build_auth(auth_cfg)
    assert isinstance(obj, APIKeyAuth)

    auth_cfg_zbx = Auth(kind="api_key", options={"api_key": "ZBX_TOKEN"})
    obj_zbx = build_auth(auth_cfg_zbx)
    assert isinstance(obj_zbx, APIKeyAuth)


def test_build_auth_oauth_refresh():
    auth_cfg_me = Auth(kind="oauth_refresh", options={"client_id": "ME_CLIENT_ID"})
    obj_me = build_auth(auth_cfg_me)
    assert isinstance(obj_me, OAuth2RefreshAuth)

    auth_cfg_sdp = Auth(kind="oauth_refresh", options={"client_id": "SDP_CLIENT_ID"})
    obj_sdp = build_auth(auth_cfg_sdp)
    assert isinstance(obj_sdp, OAuth2RefreshAuth)


def test_build_auth_ldap():
    auth_cfg = Auth(kind="ldap", options={"server": "AD_SERVER", "user": "AD_USER", "password": "AD_PASSWORD"})
    obj = build_auth(auth_cfg)
    assert isinstance(obj, LDAPBindAuth)


def test_build_auth_unknown():
    auth_cfg = Auth(kind="invalid_kind")
    with pytest.raises(ValueError, match="Unknown auth kind"):
        build_auth(auth_cfg)
