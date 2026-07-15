"""Centralized configuration for Zentinull.

All env-var-backed settings and path constants are defined here.
This is the single source of truth for runtime configuration.

Usage:
    from zentinull.config import DATA_DIR, MESH_DB, API_HOST, API_PORT  # absolute
    from .config import DATA_DIR             # relative (from sibling module)
"""

import os
from dataclasses import dataclass
from pathlib import Path
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


_load_dotenv()
PATHS: ProjectPaths = resolve_paths()

# ── Data directories (aliases of PATHS for backward compatibility) ───────────

DATA_DIR: Final[Path] = PATHS.data_dir
EXPORT_DIR: Final[Path] = PATHS.export_dir
CSV_DIR: Final[Path] = PATHS.csv_dir
SPLINK_OUTPUT_DIR: Final[Path] = PATHS.splink_output_dir
BENCHMARKS_DIR: Final[Path] = PATHS.benchmarks_dir

# ── Database paths ────────────────────────────────────────────────────────────
MESH_DB: Final[Path] = PATHS.mesh_path
STATUS_FILE: Final[Path] = PATHS.status_file
PIPELINE_LOG: Final[Path] = PATHS.log_file

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
FG_BASE_URL: str = f"https://{FG_HOST}:{FG_PORT}"

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
SPLINK_PREDICT_THRESHOLD: float = float(os.environ.get("SPLINK_PREDICT_THRESHOLD", "-10"))
SPLINK_U_MAX_PAIRS: int = int(os.environ.get("SPLINK_U_MAX_PAIRS", "2000000"))
SPLINK_LAMBDA_RECALL: float = float(os.environ.get("SPLINK_LAMBDA_RECALL", "0.5"))
# Comma-separated list of match-weight thresholds to sweep during clustering
SPLINK_SWEEP_THRESHOLDS: list[int] = [
    int(x.strip()) for x in os.environ.get("SPLINK_SWEEP_THRESHOLDS", "10,5,0,-2,-5,-10").split(",")
]


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
