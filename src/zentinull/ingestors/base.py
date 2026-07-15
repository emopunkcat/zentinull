"""
Minimal base: SQLite helpers for dumb ingest.
No dedup, no identity resolution, no field_map.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from typing import Any

from ..config import PATHS


def db(source_name: str) -> sqlite3.Connection:
    db_path = PATHS.data_dir / f"{source_name}.sqlite"
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


def raw_hash(data: str) -> str:
    """SHA-256 of a canonical (sorted-keys) JSON string.

    Used for change detection during incremental sync — same content
    always produces the same hash, byte-for-byte.
    """
    return hashlib.sha256(data.encode()).hexdigest()


def create_raw_store(conn: sqlite3.Connection, name: str) -> None:
    """Create raw-store table per §4: (id, source_id, raw_json, raw_hash, remote_updated_at, fetched_at).

    Uses the same atomic tmp-swap pattern as create_table().
    """
    conn.execute(f"DROP TABLE IF EXISTS {name}_tmp")
    conn.execute(f"""
        CREATE TABLE {name}_tmp (
            id INTEGER PRIMARY KEY,
            source_id TEXT NOT NULL,
            raw_json TEXT NOT NULL,
            raw_hash TEXT NOT NULL,
            remote_updated_at TEXT,
            fetched_at TEXT DEFAULT (datetime('now'))
        )
    """)
    conn.execute(f"DROP TABLE IF EXISTS {name}")
    conn.execute(f"ALTER TABLE {name}_tmp RENAME TO {name}")
    conn.execute(f"CREATE UNIQUE INDEX IF NOT EXISTS {name}_sid ON {name}(source_id)")
    if not conn.in_transaction:
        conn.commit()


def ensure_raw_store(conn: sqlite3.Connection, name: str) -> None:
    """Ensure a raw-store table + unique index exist WITHOUT dropping data.

    Used by incremental sync: the table must persist across runs so upsert can
    compare raw_hash against prior rows.
    """
    conn.execute(f"""
        CREATE TABLE IF NOT EXISTS {name} (
            id INTEGER PRIMARY KEY,
            source_id TEXT NOT NULL,
            raw_json TEXT NOT NULL,
            raw_hash TEXT NOT NULL,
            remote_updated_at TEXT,
            fetched_at TEXT DEFAULT (datetime('now'))
        )
    """)
    conn.execute(f"CREATE UNIQUE INDEX IF NOT EXISTS {name}_sid ON {name}(source_id)")
    if not conn.in_transaction:
        conn.commit()


def _resolve_dotted(obj: dict[str, Any], path: str) -> Any:
    """Resolve a dotted path like ``user.email`` against a dict."""
    current: Any = obj
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return current


def _stringify_id(val: Any) -> str:
    """Stringify a resolved id value, comma-joining list/tuple members.

    LDAP attribute values arrive as lists (e.g. ``["PC0$"]``); a naive
    ``str()`` yields the Python repr ``"['PC0$']"``. Comma-join elements so the
    id is the clean scalar/joined string.
    """
    if isinstance(val, (list, tuple)):
        return ",".join(str(x).strip() for x in val if x is not None and str(x).strip())
    return str(val).strip() if val is not None else ""


def insert_raw_rows(
    conn: sqlite3.Connection,
    table: str,
    rows: list[dict[str, Any]],
    id_path: str,
    updated_path: str | None = None,
) -> int:
    """Insert raw rows into a raw-store table.

    Each row dict is serialized as canonical sorted-keys JSON. source_id is
    extracted via *id_path* dotted notation; remote_updated_at via *updated_path*.
    Empty source_id falls back to raw_hash. Duplicates by source_id keep the LAST
    occurrence (last-wins dedup).
    """
    if not rows:
        return 0
    sql = f"INSERT INTO {table} (source_id, raw_json, raw_hash, remote_updated_at) VALUES (?, ?, ?, ?)"
    params: list[tuple[str, str, str, str]] = []
    for row in rows:
        raw = json.dumps(row, sort_keys=True, default=str)
        source_id = _stringify_id(_resolve_dotted(row, id_path))
        updated = ""
        if updated_path:
            val = _resolve_dotted(row, updated_path)
            if val is not None:
                updated = str(val)
        if not source_id:
            source_id = raw_hash(raw)
        params.append((source_id, raw, raw_hash(raw), updated))
    # Dedupe by source_id keeping the LAST value but preserving first-seen order
    seen: dict[str, tuple[str, str, str, str]] = {}
    for p in params:
        seen[p[0]] = p
    deduped = list(seen.values())
    was_in_txn = conn.in_transaction
    conn.executemany(sql, deduped)
    if not was_in_txn:
        conn.commit()
    return len(deduped)


def upsert_raw_rows(
    conn: sqlite3.Connection,
    table: str,
    rows: list[dict[str, Any]],
    id_path: str,
    updated_path: str | None = None,
) -> int:
    """Upsert raw rows — insert new, update changed, skip unchanged.

    Returns count of rows actually written (inserted + updated).
    Rows where raw_hash matches the existing value are skipped.
    Empty source_id falls back to raw_hash. Duplicates by source_id keep the
    LAST occurrence (last-wins dedup).
    """
    if not rows:
        return 0
    # Dedupe by source_id keeping the LAST value but preserving first-seen order
    seen_rows: dict[str, dict[str, Any]] = {}
    for row in rows:
        raw = json.dumps(row, sort_keys=True, default=str)
        source_id = _stringify_id(_resolve_dotted(row, id_path))
        if not source_id:
            source_id = raw_hash(raw)
        seen_rows[source_id] = row
    rows = list(seen_rows.values())
    was_in_txn = conn.in_transaction
    written = 0
    for row in rows:
        raw = json.dumps(row, sort_keys=True, default=str)
        source_id = _stringify_id(_resolve_dotted(row, id_path))
        new_hash = raw_hash(raw)
        updated = ""
        if updated_path:
            val = _resolve_dotted(row, updated_path)
            if val is not None:
                updated = str(val)
        if not source_id:
            source_id = new_hash

        existing = conn.execute(f"SELECT raw_hash FROM {table} WHERE source_id = ?", (source_id,)).fetchone()

        if existing and existing[0] == new_hash:
            continue  # unchanged — skip

        if existing:
            conn.execute(
                f"UPDATE {table} SET raw_json = ?, raw_hash = ?, remote_updated_at = ?, fetched_at = datetime('now') "
                f"WHERE source_id = ?",
                (raw, new_hash, updated, source_id),
            )
        else:
            conn.execute(
                f"INSERT INTO {table} (source_id, raw_json, raw_hash, remote_updated_at) VALUES (?, ?, ?, ?)",
                (source_id, raw, new_hash, updated),
            )
        written += 1
    if not was_in_txn:
        conn.commit()
    return written
