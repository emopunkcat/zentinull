"""Backup logic: checkpoint WAL, then copy SQLite DBs, DuckDB mesh, and export CSVs to a timestamped directory."""

from __future__ import annotations

import json
import shutil
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from ..config import PATHS
from ..logging_config import get_logger

log = get_logger("cli.backup")

SQLITE_GLOB = "*.sqlite"
BACKUPS_DIR = "backups"


def _fmt_bytes(size: int) -> str:
    """Human-readable byte size, one decimal place."""
    n: float = float(size)
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def create_backup(output_dir: Path | None = None) -> Path:
    """Create a timestamped backup of all databases and export files.

    Steps:
        1. WAL checkpoint every SQLite DB in data/
        2. Copy data/*.sqlite, data/mesh.duckdb, and export/ if present
        3. Write manifest.json with file sizes and copy status

    Args:
        output_dir: Custom backup directory. Defaults to
            <ROOT>/data/backups/YYYY-MM-DDTHHMMSS/

    Returns:
        Path to the backup directory.
    """
    now = datetime.now(UTC)
    ts_dir = now.strftime("%Y-%m-%dT%H%M%S")
    ts_iso = now.isoformat().replace("+00:00", "Z")
    if output_dir is None:
        output_dir = PATHS.data_dir / BACKUPS_DIR / ts_dir

    output_dir.mkdir(parents=True, exist_ok=True)
    log.info({"event": "backup_started", "output_dir": str(output_dir)})

    manifest_files: dict[str, dict[str, object]] = {}

    # ── Phase 1: WAL checkpoint ──────────────────────────────────────────
    data_path = PATHS.data_dir
    sqlite_files = sorted(data_path.glob(SQLITE_GLOB))
    for db_path in sqlite_files:
        try:
            conn = sqlite3.connect(str(db_path))
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            conn.close()
            log.info({"event": "wal_checkpoint", "db": db_path.name, "status": "ok"})
        except Exception as exc:
            log.error({"event": "wal_checkpoint", "db": db_path.name, "status": "error", "error": str(exc)})

    # ── Phase 2: Copy SQLite databases ───────────────────────────────────
    for db_path in sqlite_files:
        size_bytes = db_path.stat().st_size
        print(f"Backing up {db_path.name} ({_fmt_bytes(size_bytes)})...")
        try:
            shutil.copy2(db_path, output_dir / db_path.name)
            manifest_files[db_path.name] = {"size_bytes": size_bytes, "copied": True}
            log.info({"event": "copied", "file": db_path.name, "size_bytes": size_bytes})
        except Exception as exc:
            log.error({"event": "copy_failed", "file": db_path.name, "error": str(exc)})
            manifest_files[db_path.name] = {"size_bytes": size_bytes, "copied": False}

    # ── Phase 3: Copy DuckDB mesh ────────────────────────────────────────
    mesh_name = PATHS.mesh_path.name
    if PATHS.mesh_path.exists():
        size_bytes = PATHS.mesh_path.stat().st_size
        print(f"Backing up {mesh_name} ({_fmt_bytes(size_bytes)})...")
        try:
            shutil.copy2(PATHS.mesh_path, output_dir / mesh_name)
            manifest_files[mesh_name] = {"size_bytes": size_bytes, "copied": True}
            log.info({"event": "copied", "file": mesh_name, "size_bytes": size_bytes})
        except Exception as exc:
            log.error({"event": "copy_failed", "file": mesh_name, "error": str(exc)})
            manifest_files[mesh_name] = {"size_bytes": size_bytes, "copied": False}
    else:
        log.info({"event": "skipped", "file": mesh_name, "reason": "not_found"})

    # ── Phase 4: Copy export directory ───────────────────────────────────
    export_path = PATHS.export_dir
    if export_path.is_dir():
        dest_export = output_dir / PATHS.export_dir.name
        dest_export.mkdir(parents=True, exist_ok=True)
        for src_file in export_path.rglob("*"):
            if src_file.is_file():
                rel = src_file.relative_to(export_path)
                dest_file = dest_export / rel
                dest_file.parent.mkdir(parents=True, exist_ok=True)
                size_bytes = src_file.stat().st_size
                key = f"{PATHS.export_dir.name}/{rel.as_posix()}"
                print(f"Backing up {key} ({_fmt_bytes(size_bytes)})...")
                try:
                    shutil.copy2(src_file, dest_file)
                    manifest_files[key] = {"size_bytes": size_bytes, "copied": True}
                    log.info({"event": "copied", "file": key, "size_bytes": size_bytes})
                except Exception as exc:
                    log.error({"event": "copy_failed", "file": key, "error": str(exc)})
                    manifest_files[key] = {"size_bytes": size_bytes, "copied": False}
    else:
        log.info({"event": "skipped", "file": PATHS.export_dir.name, "reason": "not_found"})

    # ── Phase 5: Write manifest ──────────────────────────────────────────
    manifest = {
        "created": ts_iso,
        "files": manifest_files,
    }
    manifest_path = output_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2))

    copied_count = sum(1 for f in manifest_files.values() if f.get("copied"))
    total_files = len(manifest_files)
    log.info(
        {
            "event": "backup_complete",
            "output_dir": str(output_dir),
            "files_copied": copied_count,
            "total_files": total_files,
        }
    )

    return output_dir
