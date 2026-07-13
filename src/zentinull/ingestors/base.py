"""
Minimal base: SQLite helpers for dumb ingest.
No dedup, no identity resolution, no field_map.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from ..config import DATA_DIR


def db(source_name: str) -> sqlite3.Connection:
    db_path = DATA_DIR / f"{source_name}.sqlite"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


def create_table(conn: sqlite3.Connection, name: str, columns: list[str], raw: bool = True) -> None:
    """Create table with given columns + auto id + raw_json + ingested_at."""
    cols = ", ".join(c.split()[0] for c in columns)
    raw_col = ", raw_json TEXT" if raw else ""
    sql = f"""
        CREATE TABLE IF NOT EXISTS {name} (
            id INTEGER PRIMARY KEY,
            {cols}{raw_col},
            ingested_at TEXT DEFAULT (datetime('now'))
        )
    """
    # Atomic swap: create in _tmp table, then drop old + rename
    conn.execute(f"DROP TABLE IF EXISTS {name}_tmp")
    conn.execute(sql.replace(f"CREATE TABLE IF NOT EXISTS {name}", f"CREATE TABLE {name}_tmp"))
    conn.execute(f"DROP TABLE IF EXISTS {name}")
    conn.execute(f"ALTER TABLE {name}_tmp RENAME TO {name}")
    if not conn.in_transaction:
        conn.commit()


def insert(conn: sqlite3.Connection, table: str, records: list[dict[str, Any]]) -> int:
    """Bulk INSERT. No dedup. Returns count."""
    if not records:
        return 0
    cols = list(records[0].keys())
    placeholders = ",".join("?" for _ in cols)
    sql = f"INSERT INTO {table} ({','.join(cols)}) VALUES ({placeholders})"
    rows = []
    for r in records:
        row = tuple(json.dumps(v, default=str) if isinstance(v, dict | list) else v for v in r.values())
        rows.append(row)
    conn.executemany(sql, rows)
    if not conn.in_transaction:
        conn.commit()
    return len(rows)


def insert_raw(
    conn: sqlite3.Connection, table: str, records: list[dict[str, Any]], extra: dict[str, Any] | None = None
) -> int:
    """Insert records with raw_json. extra = additional constant columns."""
    if not records:
        return 0
    cols = list(records[0].keys())
    if extra:
        for k in extra:
            if k not in cols:
                cols.append(k)
    placeholders = ",".join("?" for _ in cols)
    sql = f"INSERT INTO {table} ({','.join(cols)}) VALUES ({placeholders})"
    rows = []
    for r in records:
        row_vals = {**r, **(extra or {})}
        row = tuple(
            json.dumps(v, default=str) if isinstance(v, dict | list) else v for v in (row_vals.get(c, "") for c in cols)
        )
        rows.append(row)
    conn.executemany(sql, rows)
    if not conn.in_transaction:
        conn.commit()
    return len(rows)
