"""Centralized configuration for Zentinull.

All env-var-backed settings and path constants are defined here.
This is the single source of truth for runtime configuration.

Usage:
    from zentinull.config import DATA_DIR, MESH_DB, API_HOST, API_PORT  # absolute
    from .config import DATA_DIR             # relative (from sibling module)
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Final

from .logging_config import get_logger

log = get_logger("config")

# ── Project root ──────────────────────────────────────────────────────────────
ROOT: Final[Path] = Path(__file__).resolve().parent.parent.parent


def _load_dotenv() -> None:
    """Load environment variables from ROOT/.env, if present."""
    dotenv_path = ROOT / ".env"
    if dotenv_path.exists():
        try:
            from dotenv import load_dotenv as _ld

            _ld(dotenv_path)
        except ImportError:
            pass


_load_dotenv()

# ── Data directories ──────────────────────────────────────────────────────────

DATA_DIR: Final[Path] = ROOT / "data"
EXPORT_DIR: Final[Path] = ROOT / "export"
CSV_DIR: Final[Path] = EXPORT_DIR / "csv"
SPLINK_OUTPUT_DIR: Final[Path] = EXPORT_DIR / "splink_output"
BENCHMARKS_DIR: Final[Path] = ROOT / ".benchmarks"

# ── Database paths ────────────────────────────────────────────────────────────
MESH_DB: Final[Path] = DATA_DIR / "mesh.duckdb"
STATUS_FILE: Final[Path] = DATA_DIR / "status.json"
PIPELINE_LOG: Final[Path] = DATA_DIR / "pipeline.log"

# ── Server configuration ──────────────────────────────────────────────────────
API_HOST: str = os.environ.get("ZENTINULL_HOST", "0.0.0.0")
API_PORT: int = int(os.environ.get("ZENTINULL_PORT", os.environ.get("PORT", "8001")))

# ── Logging configuration ─────────────────────────────────────────────────────
LOG_LEVEL: str = os.environ.get("ZENTINULL_LOG_LEVEL", "INFO")
LOG_JSON: bool = os.environ.get("ZENTINULL_LOG_JSON", "").lower() in ("1", "true", "yes")
LOG_PRETTY: str = os.environ.get("ZENTINULL_LOG_PRETTY", "auto")
LOG_STYLE: str = os.environ.get("ZENTINULL_LOG_STYLE", "pretty")
LOG_RULES: str = os.environ.get("ZENTINULL_LOG_RULES", "")
LOG_SHOW: str = os.environ.get("ZENTINULL_LOG_SHOW", "all")
LOG_FORMATS: str = os.environ.get("ZENTINULL_LOG_FORMATS", "")
LOG_COMPACT_WIDTH: str = os.environ.get("ZENTINULL_LOG_COMPACT_WIDTH", "48")
LOG_COLUMN_MAP: str = os.environ.get("ZENTINULL_LOG_COLUMN_MAP", "")
LOG_COMPACT_FORMATS: str = os.environ.get("ZENTINULL_LOG_COMPACT_FORMATS", "")

# ── Ingestor auth: Active Directory ───────────────────────────────────────────
AD_SERVER: str = os.environ.get("AD_SERVER", "ldap://dc.example.com:389")
AD_USER: str = os.environ.get("AD_USER", "")
AD_PASSWORD: str = os.environ.get("AD_PASSWORD", "")
AD_SEARCH_BASE: str = os.environ.get("AD_SEARCH_BASE", "DC=example,DC=local")

# ── Ingestor auth: FortiGate ──────────────────────────────────────────────────
FG_HOST: str = os.environ.get("FG_HOST", "")
FG_PORT: int = int(os.environ.get("FG_PORT", "8443"))
FG_API_KEY: str = os.environ.get("FG_API_KEY", "")

# ── Ingestor auth: ManageEngine ───────────────────────────────────────────────
ME_CLOUD_BASE_URL: str = os.environ.get("ME_CLOUD_BASE_URL", "https://endpointcentral.manageengine.com/api/1.4")
ME_MDM_BASE_URL: str = os.environ.get("ME_MDM_BASE_URL", "https://mdm.manageengine.com/api/v1/mdm")
ME_CLIENT_ID: str = os.environ.get("ME_CLIENT_ID", "")
ME_CLIENT_SECRET: str = os.environ.get("ME_CLIENT_SECRET", "")
ME_OAUTH_FILE: str = str(ROOT / os.environ.get("ME_OAUTH_FILE", "data/me_oauth.json"))

# ── Ingestor auth: ServiceDesk Plus ───────────────────────────────────────────
SDP_BASE_URL: str = os.environ.get("SDP_BASE_URL", "https://sdpondemand.manageengine.com")
SDP_CLIENT_ID: str = os.environ.get("SDP_CLIENT_ID", "")
SDP_CLIENT_SECRET: str = os.environ.get("SDP_CLIENT_SECRET", "")
SDP_OAUTH_FILE: str = str(ROOT / os.environ.get("SDP_OAUTH_FILE", "data/sdp_oauth.json"))

# ── Ingestor auth: SharePoint / n8n ───────────────────────────────────────────
N8N_BASE_URL: str = os.environ.get("N8N_BASE_URL", "http://192.168.20.56:5678/webhook")
SHAREPOINT_BASE_URL: str = os.environ.get("SHAREPOINT_BASE_URL", N8N_BASE_URL)

# ── Ingestor auth: Zabbix ─────────────────────────────────────────────────────
ZBX_URL: str = os.environ.get("ZBX_URL", "https://zabbix.example.com/api_jsonrpc.php")
ZBX_TOKEN: str = os.environ.get("ZBX_TOKEN", "")

# ── Splink configuration ──────────────────────────────────────────────────────
SPLINK_THRESHOLD: int = int(os.environ.get("SPLINK_THRESHOLD", "-5"))


def validate_config() -> list[str]:
    """Validate critical configuration at startup.

    Returns a list of warning/error messages (empty = all good).
    """
    warnings: list[str] = []
    if not DATA_DIR.exists():
        warnings.append(f"DATA_DIR does not exist: {DATA_DIR}")
    if not MESH_DB.parent.exists():
        warnings.append(f"MESH_DB parent does not exist: {MESH_DB.parent}")
    # OAuth token files are optional — only warn if explicitly set and missing
    for name, path in [("ME_OAUTH_FILE", ME_OAUTH_FILE), ("SDP_OAUTH_FILE", SDP_OAUTH_FILE)]:
        if os.environ.get(name.split("_")[0] + "_OAUTH_FILE"):
            p = Path(path)
            if not p.exists():
                warnings.append(f"{name} configured but file not found: {p}")
    return warnings
