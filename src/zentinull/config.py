"""Centralized configuration for Zentinull.

All env-var-backed settings and path constants are defined here.
This is the single source of truth for runtime configuration.

Usage:
    from zentinull.config import get_paths, get_config
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from threading import Lock
from typing import Final

from .logging_config import get_logger

log = get_logger("config")

# ── Project root ──────────────────────────────────────────────────────────────
ROOT: Final[Path] = Path(__file__).resolve().parent.parent.parent


@dataclass(frozen=True)
class ProjectPaths:
    """Project-scoped path constants."""

    project: str
    data_dir: Path
    export_dir: Path
    mesh_path: Path
    status_file: Path
    log_file: Path
    csv_dir: Path
    splink_output_dir: Path
    benchmarks_dir: Path


def resolve_paths(project: str | None = None) -> ProjectPaths:
    """Resolve project-scoped paths.

    For project='default' (or ZENTINULL_PROJECT unset): paths are
    byte-identical to the current constants (ROOT/data, ROOT/export, etc.).
    For any other project: paths are ROOT/projects/<p>/state/data,
    ROOT/projects/<p>/state/export, etc.
    """
    project_name = project or os.environ.get("ZENTINULL_PROJECT", "default")
    if project_name == "default":
        data_dir = ROOT / "data"
        export_dir = ROOT / "export"
    else:
        base = ROOT / "projects" / project_name / "state"
        data_dir = base / "data"
        export_dir = base / "export"
    return ProjectPaths(
        project=project_name,
        data_dir=data_dir,
        export_dir=export_dir,
        mesh_path=data_dir / "mesh.duckdb",
        status_file=data_dir / "status.json",
        log_file=data_dir / "pipeline.log",
        csv_dir=export_dir / "csv",
        splink_output_dir=export_dir / "splink_output",
        benchmarks_dir=ROOT / ".benchmarks",
    )


def _load_dotenv() -> None:
    """Load environment variables from ROOT/.env, if present."""
    dotenv_path = ROOT / ".env"
    if dotenv_path.exists():
        try:
            from dotenv import load_dotenv as _ld

            _ld(dotenv_path)
        except ImportError:
            pass


# ── Lazy loading ─────────────────────────────────────────────────────────────

_config_loaded: bool = False
_config_lock: Lock = Lock()


def _ensure_loaded() -> None:
    """Load .env exactly once, on first access."""
    global _config_loaded
    if not _config_loaded:
        with _config_lock:
            if not _config_loaded:
                _load_dotenv()
                _config_loaded = True


def get_paths(project: str | None = None) -> ProjectPaths:
    """Load dotenv if not yet done, then resolve project paths.

    Not cached — callers may pass different ``project`` values
    (e.g. ``--project`` CLI switches during tests).
    """
    _ensure_loaded()
    return resolve_paths(project)


@dataclass(frozen=True)
class Config:
    """Frozen configuration object — paths + all env-var settings.

    Instantiated once by ``get_config()``.  Every field has a sensible
    default so the attribute is never missing.
    """

    paths: ProjectPaths

    # ── Server ────────────────────────────────────────────────────────────
    api_host: str = "0.0.0.0"
    api_port: int = 8001

    # ── Logging ───────────────────────────────────────────────────────────
    log_level: str = "INFO"
    log_json: bool = False
    log_pretty: str = "auto"
    log_style: str = "pretty"
    log_rules: str = ""
    log_show: str = "all"
    log_formats: str = ""
    log_compact_width: str = "48"
    log_column_map: str = ""
    log_compact_formats: str = ""

    # ── Ingestor auth: Active Directory ───────────────────────────────────
    ad_server: str = "ldap://dc.example.com:389"
    ad_user: str = ""
    ad_password: str = ""
    ad_search_base: str = "DC=example,DC=local"

    # ── Ingestor auth: FortiGate ──────────────────────────────────────────
    fg_host: str = ""
    fg_port: int = 8443
    fg_api_key: str = ""

    # ── Ingestor auth: ManageEngine ───────────────────────────────────────
    me_cloud_base_url: str = "https://endpointcentral.manageengine.com/api/1.4"
    me_mdm_base_url: str = "https://mdm.manageengine.com/api/v1/mdm"
    me_client_id: str = ""
    me_client_secret: str = ""
    me_oauth_file: str = "data/me_oauth.json"

    # ── Ingestor auth: ServiceDesk Plus ───────────────────────────────────
    sdp_base_url: str = "https://sdpondemand.manageengine.com"
    sdp_client_id: str = ""
    sdp_client_secret: str = ""
    sdp_oauth_file: str = "data/sdp_oauth.json"

    # ── Ingestor auth: SharePoint / n8n ───────────────────────────────────
    n8n_base_url: str = "http://192.168.20.56:5678/webhook"
    sharepoint_base_url: str = "http://192.168.20.56:5678/webhook"

    # ── Ingestor auth: Zabbix ─────────────────────────────────────────────
    zbx_url: str = "https://zabbix.example.com/api_jsonrpc.php"
    zbx_token: str = ""

    # ── Splink ────────────────────────────────────────────────────────────
    splink_threshold: int = -5
    splink_predict_threshold: float = -10.0
    splink_u_max_pairs: int = 2000000
    splink_lambda_recall: float = 0.5
    splink_sweep_thresholds: list[int] = field(default_factory=lambda: [10, 5, 0, -2, -5, -10])

    # ── Change tracking retention ─────────────────────────────────────────
    fh_retention_days: int = 180
    zombie_stale_days: int = 90
    fg_base_url: str = "https://_fg_host_:8443"


# Module-level aliases for importers (used by manifest.py, db.py, etc.)
ZOMBIE_STALE_DAYS: int = 90
FH_RETENTION_DAYS: int = 180
SPLINK_LAMBDA_RECALL: float = 0.5
SPLINK_PREDICT_THRESHOLD: float = -10.0
SPLINK_SWEEP_THRESHOLDS: tuple[int, ...] = (10, 5, 0, -2, -5, -10)
SPLINK_THRESHOLD: int = -5
SPLINK_U_MAX_PAIRS: int = 2000000


@lru_cache(maxsize=1)
def get_config() -> Config:
    """Return a cached frozen Config with all env-var settings resolved.

    ``@lru_cache(maxsize=1)`` is safe here because the **values** are
    read from the environment at first call and then frozen — the
    ``ZENTINULL_PROJECT`` env var must be set *before* the first call.
    Tests that switch projects pass ``project`` to ``get_paths()``
    directly, bypassing this cache.
    """
    _ensure_loaded()
    project = os.environ.get("ZENTINULL_PROJECT")
    paths = resolve_paths(project)
    return Config(
        paths=paths,
        api_host=os.environ.get("ZENTINULL_HOST", "0.0.0.0"),
        api_port=int(os.environ.get("ZENTINULL_PORT", os.environ.get("PORT", "8001"))),
        log_level=os.environ.get("ZENTINULL_LOG_LEVEL", "INFO"),
        log_json=os.environ.get("ZENTINULL_LOG_JSON", "").lower() in ("1", "true", "yes"),
        log_pretty=os.environ.get("ZENTINULL_LOG_PRETTY", "auto"),
        log_style=os.environ.get("ZENTINULL_LOG_STYLE", "pretty"),
        log_rules=os.environ.get("ZENTINULL_LOG_RULES", ""),
        log_show=os.environ.get("ZENTINULL_LOG_SHOW", "all"),
        log_formats=os.environ.get("ZENTINULL_LOG_FORMATS", ""),
        log_compact_width=os.environ.get("ZENTINULL_LOG_COMPACT_WIDTH", "48"),
        log_column_map=os.environ.get("ZENTINULL_LOG_COLUMN_MAP", ""),
        log_compact_formats=os.environ.get("ZENTINULL_LOG_COMPACT_FORMATS", ""),
        ad_server=os.environ.get("AD_SERVER", "ldap://dc.example.com:389"),
        ad_user=os.environ.get("AD_USER", ""),
        ad_password=os.environ.get("AD_PASSWORD", ""),
        ad_search_base=os.environ.get("AD_SEARCH_BASE", "DC=example,DC=local"),
        fg_host=os.environ.get("FG_HOST", ""),
        fg_port=int(os.environ.get("FG_PORT", "8443")),
        fg_base_url=f"https://{os.environ.get('FG_HOST', 'fg.example.com')}:{os.environ.get('FG_PORT', '8443')}",
        me_cloud_base_url=os.environ.get("ME_CLOUD_BASE_URL", "https://endpointcentral.manageengine.com/api/1.4"),
        me_mdm_base_url=os.environ.get("ME_MDM_BASE_URL", "https://mdm.manageengine.com/api/v1/mdm"),
        me_client_id=os.environ.get("ME_CLIENT_ID", ""),
        me_client_secret=os.environ.get("ME_CLIENT_SECRET", ""),
        me_oauth_file=str(ROOT / os.environ.get("ME_OAUTH_FILE", "data/me_oauth.json")),
        sdp_base_url=os.environ.get("SDP_BASE_URL", "https://sdpondemand.manageengine.com"),
        sdp_client_id=os.environ.get("SDP_CLIENT_ID", ""),
        sdp_client_secret=os.environ.get("SDP_CLIENT_SECRET", ""),
        sdp_oauth_file=str(ROOT / os.environ.get("SDP_OAUTH_FILE", "data/sdp_oauth.json")),
        n8n_base_url=os.environ.get("N8N_BASE_URL", "http://192.168.20.56:5678/webhook"),
        sharepoint_base_url=os.environ.get(
            "SHAREPOINT_BASE_URL", os.environ.get("N8N_BASE_URL", "http://192.168.20.56:5678/webhook")
        ),
        zbx_url=os.environ.get("ZBX_URL", "https://zabbix.example.com/api_jsonrpc.php"),
        zbx_token=os.environ.get("ZBX_TOKEN", ""),
        splink_threshold=int(os.environ.get("SPLINK_THRESHOLD", "-5")),
        splink_predict_threshold=float(os.environ.get("SPLINK_PREDICT_THRESHOLD", "-10")),
        splink_u_max_pairs=int(os.environ.get("SPLINK_U_MAX_PAIRS", "2000000")),
        splink_lambda_recall=float(os.environ.get("SPLINK_LAMBDA_RECALL", "0.5")),
        splink_sweep_thresholds=[
            int(x.strip()) for x in os.environ.get("SPLINK_SWEEP_THRESHOLDS", "10,5,0,-2,-5,-10").split(",")
        ],
        fh_retention_days=int(os.environ.get("FH_RETENTION_DAYS", "180")),
        zombie_stale_days=int(os.environ.get("ZOMBIE_STALE_DAYS", "90")),
    )


def validate_config(cfg: Config | None = None) -> list[str]:
    """Validate critical configuration at startup.

    Returns a list of warning/error messages (empty = all good).
    Accepts an optional ``Config`` to avoid repeated ``get_config()`` calls.
    """
    if cfg is None:
        cfg = get_config()
    warnings: list[str] = []
    if not cfg.paths.data_dir.exists():
        warnings.append(f"DATA_DIR does not exist: {cfg.paths.data_dir}")
    if not cfg.paths.mesh_path.parent.exists():
        warnings.append(f"MESH_DB parent does not exist: {cfg.paths.mesh_path.parent}")
    # OAuth token files are optional — only warn if explicitly set and missing
    for name, path in [("ME_OAUTH_FILE", cfg.me_oauth_file), ("SDP_OAUTH_FILE", cfg.sdp_oauth_file)]:
        if os.environ.get(name.split("_")[0] + "_OAUTH_FILE"):
            p = Path(path)
            if not p.exists():
                warnings.append(f"{name} configured but file not found: {p}")
    return warnings
