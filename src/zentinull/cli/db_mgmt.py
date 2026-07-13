"""SQLite database management utilities — list, vacuum, and integrity check.

Usage:
    from zentinull.cli.db_mgmt import list_dbs, vacuum_dbs, check_dbs
    list_dbs()       # print table of all DBs, tables, row counts, sizes
    vacuum_dbs()     # VACUUM all DBs, report size before/after
    check_dbs()      # PRAGMA integrity_check on all DBs, report results
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from ..config import DATA_DIR
from ..logging_config import get_logger

log = get_logger("cli.db_mgmt")


def _fmt_size(bytes_: int) -> str:
    """Format a byte count as a human-readable string (e.g. '2.3 MB')."""
    if bytes_ < 1024:
        return f"{bytes_} B"
    kb = bytes_ / 1024.0
    if kb < 1024:
        return f"{kb:.1f} KB"
    mb = kb / 1024.0
    if mb < 1024:
        return f"{mb:.1f} MB"
    gb = mb / 1024.0
    return f"{gb:.2f} GB"


def _get_db_files() -> list[Path]:
    """Return sorted list of .sqlite file paths in the data directory."""
    if not DATA_DIR.is_dir():
        return []
    return sorted(DATA_DIR.glob("*.sqlite"), key=lambda p: p.name)


def _table_rows(conn: sqlite3.Connection, table: str) -> int:
    """Return the row count for *table*."""
    try:
        row = conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()
        return row[0] if row else 0
    except sqlite3.OperationalError:
        return 0


# ── list_dbs ──────────────────────────────────────────────────────────────────


def list_dbs() -> None:
    """Print a table of all SQLite databases with their tables, row counts, and file sizes."""
    db_files = _get_db_files()

    if not db_files:
        log.warning({"event": "no_db_files", "dir": str(DATA_DIR)})
        print(f"No .sqlite files found in {DATA_DIR}")
        return

    total_size = 0
    rows: list[tuple[str, str, int, str]] = []  # (db_name, table, row_count, size_str)

    for db_path in db_files:
        db_name = db_path.name
        file_size = db_path.stat().st_size
        total_size += file_size
        size_str = _fmt_size(file_size)

        conn: sqlite3.Connection | None = None
        try:
            conn = sqlite3.connect(str(db_path))
            conn.row_factory = sqlite3.Row
            cursor = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
            )
            tables = [r["name"] for r in cursor.fetchall()]

            if not tables:
                rows.append((db_name, "(no tables)", 0, size_str))
            else:
                for i, table in enumerate(tables):
                    count = _table_rows(conn, table)
                    label = db_name if i == 0 else ""
                    rows.append((label, table, count, size_str if i == 0 else ""))
        except sqlite3.Error as exc:
            log.error({"event": "db_open_failed", "file": db_name, "error": str(exc)})
            rows.append((db_name, "(error)", 0, _fmt_size(file_size)))
        finally:
            if conn:
                conn.close()

    # ── Print table ───────────────────────────────────────────────────────────
    col_db = max(max(len(r[0]) for r in rows), len("Database"))
    col_tbl = max(max(len(r[1]) for r in rows), len("Tables"))
    col_rows = max(max(len(str(r[2])) for r in rows), len("Total Rows"))
    col_size = max(max(len(r[3]) for r in rows), len("Size"))

    header = f"{'Database':<{col_db}}  {'Tables':<{col_tbl}}  {'Total Rows':>{col_rows}}  {'Size':>{col_size}}"
    sep = "─" * len(header)

    print(header)
    print(sep)

    for db_label, table, count, size in rows:
        print(f"{db_label:<{col_db}}  {table:<{col_tbl}}  {count:>{col_rows}}  {size:>{col_size}}")

    print(sep)
    print(f"{'TOTAL':<{col_db}}  {'':<{col_tbl}}  {'':>{col_rows}}  {_fmt_size(total_size):>{col_size}}")

    log.info({"event": "list_dbs", "files": len(db_files), "total_size": total_size})


# ── vacuum_dbs ────────────────────────────────────────────────────────────────


def vacuum_dbs() -> None:
    """Run VACUUM on all SQLite databases, reporting size before and after."""
    db_files = _get_db_files()

    if not db_files:
        log.warning({"event": "no_db_files", "dir": str(DATA_DIR)})
        print(f"No .sqlite files found in {DATA_DIR}")
        return

    total_saved = 0
    total_before = 0

    col_file = max(len(p.name) for p in db_files)
    col_before = len("Before")
    col_after = len("After")
    col_saved = len("Saved")

    header = f"{'File':<{col_file}}  {'Before':>{col_before}}  {'After':>{col_after}}  {'Saved':>{col_saved}}"
    sep = "─" * len(header)

    print(header)
    print(sep)

    for db_path in db_files:
        db_name = db_path.name
        size_before = db_path.stat().st_size
        total_before += size_before

        conn: sqlite3.Connection | None = None
        size_after = size_before
        try:
            conn = sqlite3.connect(str(db_path))
            conn.execute("VACUUM")
            conn.close()
            conn = None
            size_after = db_path.stat().st_size
        except sqlite3.Error as exc:
            log.error({"event": "vacuum_failed", "file": db_name, "error": str(exc)})
        finally:
            if conn:
                conn.close()

        saved = size_before - size_after
        total_saved += saved

        before_str = _fmt_size(size_before)
        after_str = _fmt_size(size_after)
        saved_str = _fmt_size(saved)

        print(f"{db_name:<{col_file}}  {before_str:>{col_before}}  {after_str:>{col_after}}  {saved_str:>{col_saved}}")

        log.info(
            {
                "event": "vacuumed",
                "file": db_name,
                "before": size_before,
                "after": size_after,
                "saved": saved,
            }
        )

    print(sep)
    print(
        f"{'TOTAL':<{col_file}}  {_fmt_size(total_before):>{col_before}}  {_fmt_size(total_before - total_saved):>{col_after}}  {_fmt_size(total_saved):>{col_saved}}"
    )

    log.info(
        {
            "event": "vacuum_dbs_done",
            "files": len(db_files),
            "total_saved": total_saved,
        }
    )


# ── check_dbs ─────────────────────────────────────────────────────────────────


def check_dbs() -> None:
    """Run PRAGMA integrity_check on all SQLite databases, reporting results."""
    db_files = _get_db_files()

    if not db_files:
        log.warning({"event": "no_db_files", "dir": str(DATA_DIR)})
        print(f"No .sqlite files found in {DATA_DIR}")
        return

    col_file = max(len(p.name) for p in db_files)
    col_status = len("Status")
    col_detail = max(len("Details"), 32)

    header = f"{'File':<{col_file}}  {'Status':^{col_status}}  Details"
    sep = "─" * (col_file + col_status + col_detail + 4)

    print(header)
    print(sep)

    passed = 0
    failed = 0

    for db_path in db_files:
        db_name = db_path.name

        conn: sqlite3.Connection | None = None
        status = "FAIL"
        detail = ""
        try:
            conn = sqlite3.connect(str(db_path))
            conn.row_factory = sqlite3.Row
            rows = conn.execute("PRAGMA integrity_check").fetchall()
            results = [r[0] for r in rows]
            if results == ["ok"]:
                status = "PASS"
                detail = "ok"
                passed += 1
            else:
                detail = "; ".join(results[:3])
                if len(results) > 3:
                    detail += f" (+{len(results) - 3} more)"
                failed += 1
                log.warning({"event": "integrity_check_failed", "file": db_name, "details": results})
        except sqlite3.Error as exc:
            detail = str(exc)
            failed += 1
            log.error({"event": "integrity_check_error", "file": db_name, "error": str(exc)})
        finally:
            if conn:
                conn.close()

        print(f"{db_name:<{col_file}}  {status:^{col_status}}  {detail}")

    print(sep)
    print(f"Passed: {passed}  Failed: {failed}")

    log.info(
        {
            "event": "check_dbs_done",
            "files": len(db_files),
            "passed": passed,
            "failed": failed,
        }
    )
