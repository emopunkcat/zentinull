#!/usr/bin/env python3
"""Zentinull — unified CLI for device entity resolution pipeline.

Usage:
    python serve.py start              # Start API server
    python serve.py pipeline           # Full pipeline: ingest → export → splink → load
    python serve.py ingest             # Run all 6 ingestors
    python serve.py ingest --source fg # Single source
    python serve.py ingest --skip sp,ad
    python serve.py splink             # Run entity resolution
    python serve.py splink --skip-training --threshold -5
    python serve.py export             # Export SQLite → CSV
    python serve.py load               # Load clusters → DuckDB mesh
    python serve.py status             # Pipeline status
    python serve.py backup             # Backup all data
    python serve.py logs               # Tail pipeline log
    python serve.py db list            # List SQLite DBs
    python serve.py db vacuum          # VACUUM all DBs
    python serve.py db check           # Integrity check
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent

# ── Load .env before any zentinull imports ──────────────────────────────────
# Config module reads os.environ at import time, so .env MUST be loaded first.
_dotenv_path = _HERE / ".env"
if _dotenv_path.exists():
    try:
        from dotenv import load_dotenv

        load_dotenv(_dotenv_path)
    except ImportError:
        # python-dotenv not installed — env vars must be exported manually
        pass


def _setup_logging(json_output: bool = False) -> None:
    from zentinull.config import get_paths
    from zentinull.logging_config import setup

    paths = get_paths()
    setup(level="INFO", json_output=json_output, log_file=str(paths.log_file))


# ── Start ──────────────────────────────────────────────────────────────────────


def cmd_start(args: argparse.Namespace) -> None:
    """Start the live API server with background data refresh.

    This is the single 24/7 command: the FastAPI server serves queries while
    a background scheduler runs incremental ingest and periodic Splink re-clustering
    on their configured intervals (from manifest System.schedule, overridable via
    ZENTINULL_SCHED_* env vars).  No separate worker process needed.
    """
    import uvicorn

    from zentinull.config import get_config

    _setup_logging(json_output=args.log_json)
    from zentinull.logging_config import get_logger

    log = get_logger("serve")
    log.info({"event": "server_start", "port": args.port, "reload": args.reload})
    uvicorn.run(
        "zentinull.api.server:app",
        host=get_config().api_host,
        port=args.port,
        reload=args.reload,
    )


# ── Pipeline ───────────────────────────────────────────────────────────────────


def cmd_pipeline(args: argparse.Namespace) -> None:
    """Run the full pipeline."""
    _setup_logging()

    from zentinull.cli.pipeline import run_pipeline

    sources = [s.strip() for s in args.source.split(",")] if args.source else None
    skip_sources = [s.strip() for s in args.skip.split(",")] if args.skip else None

    run_pipeline(
        skip_ingest=args.skip_ingest,
        sources=sources,
        skip_sources=skip_sources,
    )


def cmd_ingest(args: argparse.Namespace) -> None:
    """Run ingestors."""
    _setup_logging()

    from zentinull.cli.pipeline import run_ingest

    sources: list[str] | None = None
    if args.source:
        sources = [s.strip() for s in args.source.split(",")]
    skip_sources: list[str] | None = None
    if args.skip:
        skip_sources = [s.strip() for s in args.skip.split(",")]

    results = run_ingest(sources=sources, skip_sources=skip_sources)
    total = sum(v for v in results.values() if v >= 0)
    failed = [k for k, v in results.items() if v < 0]
    print(f"\nIngest complete: {total} records from {len(results) - len(failed)} sources")
    if failed:
        print(f"Failed: {', '.join(failed)}")


def cmd_splink(args: argparse.Namespace) -> None:
    """Run Splink entity resolution."""
    _setup_logging()

    from zentinull.cli.pipeline import run_splink

    threshold = args.threshold
    skip_training = args.skip_training
    run_splink(skip_training=skip_training, threshold=threshold)


def cmd_export(args: argparse.Namespace) -> None:  # noqa: ARG001
    """Run export: SQLite → unified CSV."""
    _setup_logging()

    from zentinull.cli.pipeline import run_export

    total = run_export()
    print(f"Export complete: {total} records written to export/csv/devices.csv")


def cmd_load(args: argparse.Namespace) -> None:  # noqa: ARG001
    """Load clusters.csv into DuckDB mesh."""
    _setup_logging()

    from zentinull.cli.pipeline import run_load

    device_count = run_load()
    print(f"Load complete: {device_count} devices in data/mesh.duckdb")


# ── Seed ──────────────────────────────────────────────────────────────────────


def cmd_seed(args: argparse.Namespace) -> None:
    """Seed demo data into mesh database."""
    from scripts.seed_demo_data import seed_demo_data

    count = seed_demo_data(row_count=args.rows, force=args.force)
    print(f"Seeded {count} source records into data/mesh.duckdb")


# ── Benchmark ─────────────────────────────────────────────────────────────────


def cmd_bench(args: argparse.Namespace) -> None:  # noqa: ARG001
    """Run test suite benchmarks with historical tracking."""
    from scripts.bench import main as bench_main

    sys.exit(bench_main())


def cmd_bench_api(args: argparse.Namespace) -> None:
    """Run API endpoint benchmarks."""
    from scripts.bench_api import main as bench_api_main

    sys.exit(bench_api_main(["--ci"] if args.ci else None))


# ── Status ─────────────────────────────────────────────────────────────────────


def cmd_status(args: argparse.Namespace) -> None:  # noqa: ARG001
    """Show pipeline status."""
    from zentinull.cli.status import print_status

    print_status()


# ── Backup ─────────────────────────────────────────────────────────────────────


def cmd_backup(args: argparse.Namespace) -> None:
    """Backup SQLite + DuckDB + CSV."""
    _setup_logging()

    from zentinull.cli.backup import create_backup

    output = Path(args.output) if args.output else None
    backup_dir = create_backup(output_dir=output)
    print(f"Backup complete: {backup_dir}")


# ── Logs ───────────────────────────────────────────────────────────────────────


def cmd_logs(args: argparse.Namespace) -> None:
    from zentinull.config import get_paths

    log_path = get_paths().log_file

    if not log_path.exists():
        print("No pipeline log found at data/pipeline.log")
        return

    if args.follow:
        try:
            subprocess.run(["tail", "-f", str(log_path)])
        except KeyboardInterrupt:
            print()
    else:
        lines = log_path.read_text(encoding="utf-8").splitlines()
        n = args.lines
        for line in lines[-n:]:
            print(line)


# ── DB Management ──────────────────────────────────────────────────────────────


def cmd_db(args: argparse.Namespace) -> None:
    """SQLite database management."""
    from zentinull.cli.db_mgmt import check_dbs, list_dbs, vacuum_dbs

    action = args.db_action
    if action == "list":
        list_dbs()
    elif action == "vacuum":
        vacuum_dbs()
    elif action == "check":
        check_dbs()



# ── Audit Mapping ──────────────────────────────────────────────────────────────


def cmd_audit_mapping(args: argparse.Namespace) -> None:
    """Propose field mappings or detect drift."""
    import json
    import sqlite3
    from collections import defaultdict

    from zentinull.config import get_paths
    from zentinull.manifest import load_manifest
    from zentinull.normalizer import NULL_SENTINELS
    from zentinull.resolve.classifier import classify_value

    manifest = load_manifest()

    if args.propose:
        feed_key = args.propose
        if feed_key not in manifest.feeds:
            print(f"Error: feed '{feed_key}' not in manifest")
            sys.exit(1)
        feed = manifest.feeds[feed_key]

        db_path = get_paths().data_dir / f"{feed.system}.sqlite"
        if not db_path.exists():
            print(f"Error: database not found: {db_path}")
            sys.exit(1)

        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        try:
            tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
            if feed.store not in tables:
                print(f"Error: table '{feed.store}' not found in {db_path}")
                sys.exit(1)

            rows = conn.execute(f"SELECT raw_json FROM {feed.store} LIMIT 100").fetchall()
        finally:
            conn.close()

        # Collect all keys and sample values (first 4 non-sentinel values per key)
        key_values: dict[str, set[str]] = defaultdict(set)
        for r in rows:
            try:
                raw = json.loads(r["raw_json"])
            except (json.JSONDecodeError, TypeError):
                continue
            for k, v in raw.items():
                if isinstance(v, str) and v.strip() and v not in NULL_SENTINELS and len(key_values[k]) < 4:
                    key_values[k].add(v.strip())

        # Find mapped keys (already in spec)
        mapped_keys_lower = set()
        for spec in feed.spec.values():
            for path in spec.paths:
                mapped_keys_lower.add(path.lower().replace("fields.", ""))

        # Classify unmapped keys
        unmapped = []
        for key, vals in sorted(key_values.items()):
            kl = key.lower()
            if kl in mapped_keys_lower:
                continue
            # Check if any value matches a Tier-1 pattern
            for val in vals:
                cls = classify_value(val)
                if cls:
                    unmapped.append((key, cls, list(vals)[:2]))
                    break

        # Emit pasteable FieldSpec
        for key, target, samples in unmapped:
            print(f"# {key} → {target}  (e.g., {samples})")
            print(f'"{key}" = FieldSpec(paths=("{key}",)),  # → {target}')

        if not unmapped:
            print(f"No unmapped identity-shaped keys found in '{feed_key}'.")

        return

    if args.strict:
        # Drift mode
        exit_code = 0
        high_confidence_patterns = {"mac_address", "ip_address", "imei", "email"}

        for feed_key in manifest.feeds:
            feed = manifest.feeds[feed_key]
            db_path = get_paths().data_dir / f"{feed.system}.sqlite"
            if not db_path.exists():
                continue

            conn = sqlite3.connect(str(db_path))
            conn.row_factory = sqlite3.Row
            try:
                tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
                if feed.store not in tables:
                    continue
                rows = conn.execute(f"SELECT raw_json FROM {feed.store}").fetchall()
            finally:
                conn.close()

            # Collect all keys and their fill rates
            key_fill: dict[str, int] = defaultdict(int)
            total_rows = 0
            for r in rows:
                total_rows += 1
                try:
                    raw = json.loads(r["raw_json"])
                except (json.JSONDecodeError, TypeError):
                    continue
                for k, v in raw.items():
                    if isinstance(v, str) and v.strip() and v not in NULL_SENTINELS:
                        key_fill[k] += 1

            if total_rows == 0:
                continue

            # Find mapped keys
            mapped_keys_lower = set()
            for spec in feed.spec.values():
                for path in spec.paths:
                    mapped_keys_lower.add(path.lower().replace("fields.", ""))

            # Check unmapped high-confidence keys
            for key, fill_count in key_fill.items():
                kl = key.lower()
                if kl in mapped_keys_lower:
                    continue
                fill_pct = 100 * fill_count / total_rows
                if fill_pct < 50:
                    continue
                # Sample a value to classify
                conn = sqlite3.connect(str(db_path))
                conn.row_factory = sqlite3.Row
                try:
                    sample = conn.execute(
                        f"SELECT raw_json FROM {feed.store} WHERE raw_json LIKE ? LIMIT 1",
                        [f'%"{key}"%'],
                    ).fetchone()
                finally:
                    conn.close()

                if sample:
                    try:
                        raw = json.loads(sample["raw_json"])
                        val = raw.get(key, "")
                        if isinstance(val, str):
                            cls = classify_value(val)
                            if cls in high_confidence_patterns:
                                print(f"DRIFT: {feed_key}.{key} looks like {cls} ({fill_pct:.0f}% fill, unmapped)")
                                exit_code = 1
                    except (json.JSONDecodeError, TypeError):
                        pass

        if exit_code:
            print(f"Drift detected — exiting with code {exit_code}")
        sys.exit(exit_code)

    print("Error: specify --propose <feed> or --strict")
    sys.exit(1)

# ── Main ───────────────────────────────────────────────────────────────────────


def main() -> None:
    # Pre-parse --project before any zentinull import triggers config.PATHS resolution
    import os

    _project = None
    for i, arg in enumerate(sys.argv):
        if arg == "--project" and i + 1 < len(sys.argv):
            _project = sys.argv[i + 1]
            break
    if _project:
        os.environ["ZENTINULL_PROJECT"] = _project

    # Load manifest for dynamic source key references
    from zentinull.manifest import load_manifest
    manifest = load_manifest()
    source_keys = ",".join(sorted(manifest.systems.keys()))
    parser = argparse.ArgumentParser(
        description="Zentinull — device entity resolution pipeline",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=f"""
Examples:
  python serve.py start --port 9000 --reload
  python serve.py pipeline --skip-ingest
  python serve.py ingest --source fg
  python serve.py ingest --skip ad,sdp
  python serve.py splink --threshold -5
  python serve.py seed --rows 200 -f
  python serve.py --project demo pipeline
  python serve.py --project demo start
  # Remote ingest via SSH tunnel (no daemon needed):
  python serve.py backup --output /backups/2026-07-11/
  python serve.py logs --follow
  python serve.py db list
  # Remote ingest via SSH tunnel (no daemon needed):
  #   ./scripts/tunnel.sh user@jump-box serve.py pipeline


Available source keys: {source_keys}
""",
    )
    parser.add_argument("--project", type=str, default=None, help="Project name (default: 'default')")

    sub = parser.add_subparsers(dest="command", help="Available commands")

    # ── start ──
    p_start = sub.add_parser("start", help="Start the FastAPI API server")
    p_start.add_argument("--port", type=int, default=8001, help="Port (default: 8001)")
    p_start.add_argument("--reload", action="store_true", help="Enable auto-reload")
    p_start.add_argument("--log-json", action="store_true", help="JSON log output")
    p_start.set_defaults(func=cmd_start)

    # ── seed ──
    p_seed = sub.add_parser("seed", help="Seed demo data into mesh database")
    p_seed.add_argument("--rows", type=int, default=80, help="Number of devices to generate (default: 80)")
    p_seed.add_argument("--force", "-f", action="store_true", help="Overwrite existing mesh.duckdb")
    p_seed.set_defaults(func=cmd_seed)

    # ── bench ──
    p_bench = sub.add_parser("bench", help="Run test suite benchmarks with historical tracking")
    p_bench.set_defaults(func=cmd_bench)

    # ── bench-api ──
    p_bench_api = sub.add_parser("bench-api", help="Run API endpoint performance benchmarks")
    p_bench_api.add_argument("--ci", action="store_true", help="CI mode: strict regression check (25%% threshold)")
    p_bench_api.set_defaults(func=cmd_bench_api)

    # ── pipeline ──
    p_pipe = sub.add_parser("pipeline", help="Run full pipeline: ingest → export → splink → load")
    p_pipe.add_argument("--skip-ingest", action="store_true", help="Skip ingest stage")
    p_pipe.add_argument("--source", type=str, help="Comma-separated source keys: sp,me,fg,zbx,ad,sdp")
    p_pipe.add_argument("--skip", type=str, help="Comma-separated sources to skip")
    p_pipe.set_defaults(func=cmd_pipeline)

    # ── ingest ──
    p_ingest = sub.add_parser("ingest", help="Run data ingestors")
    p_ingest.add_argument("--source", type=str, help="Comma-separated source keys: sp,me,fg,zbx,ad,sdp")
    p_ingest.add_argument("--skip", type=str, help="Comma-separated sources to skip")
    p_ingest.set_defaults(func=cmd_ingest)

    # ── splink ──
    p_splink = sub.add_parser("splink", help="Run Splink entity resolution")
    p_splink.add_argument("--skip-training", action="store_true", help="Skip training, predict + cluster only")
    p_splink.add_argument("--threshold", type=int, help="Override match weight threshold")
    p_splink.set_defaults(func=cmd_splink)

    # ── export ──
    p_export = sub.add_parser("export", help="Export SQLite → unified CSV for Splink")
    p_export.set_defaults(func=cmd_export)

    # ── load ──
    p_load = sub.add_parser("load", help="Load Splink clusters into DuckDB mesh")
    p_load.set_defaults(func=cmd_load)

    # ── status ──
    p_status = sub.add_parser("status", help="Show pipeline status and data freshness")
    p_status.set_defaults(func=cmd_status)

    # ── backup ──
    p_backup = sub.add_parser("backup", help="Backup SQLite + DuckDB + CSV to timestamped directory")
    p_backup.add_argument("--output", type=str, help="Custom output directory")
    p_backup.set_defaults(func=cmd_backup)

    # ── logs ──
    p_logs = sub.add_parser("logs", help="View pipeline log")
    p_logs.add_argument("--follow", "-f", action="store_true", help="Follow log output (tail -f)")
    p_logs.add_argument("--lines", "-n", type=int, default=50, help="Number of lines to show (default: 50)")
    p_logs.set_defaults(func=cmd_logs)

    # ── db ──
    p_db = sub.add_parser("db", help="SQLite database management")
    p_db_sub = p_db.add_subparsers(dest="db_action", help="DB action")
    p_db_list = p_db_sub.add_parser("list", help="List all SQLite DBs with table names and row counts")
    p_db_list.set_defaults(func=cmd_db)
    p_db_vacuum = p_db_sub.add_parser("vacuum", help="VACUUM all SQLite databases")
    p_db_vacuum.set_defaults(func=cmd_db)
    p_db_check = p_db_sub.add_parser("check", help="Run integrity check on all SQLite databases")
    p_db_check.set_defaults(func=cmd_db)

    # ── audit-mapping ──
    p_audit = sub.add_parser("audit-mapping", help="Audit raw key-to-field mappings")
    p_audit.add_argument("--propose", type=str, help="Feed key to propose mapped fields for")
    p_audit.add_argument("--strict", action="store_true", help="Exit non-zero on drift")
    p_audit.set_defaults(func=cmd_audit_mapping)

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(0)

    args.func(args)


if __name__ == "__main__":
    main()
