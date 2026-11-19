"""SQLite access helpers. One connection per thread; WAL mode so UI reads don't block sync writes."""

from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd

from garmin_insights import config

_SCHEMA = Path(__file__).with_name("schema.sql")
_local = threading.local()
_init_lock = threading.Lock()
_initialized: set[str] = set()


def now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def connect(path: Path | None = None) -> sqlite3.Connection:
    p = str(path or config.db_path())
    cache = getattr(_local, "conns", None)
    if cache is None:
        cache = _local.conns = {}
    conn = cache.get(p)
    if conn is None:
        conn = sqlite3.connect(p, timeout=30, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA busy_timeout = 30000")
        cache[p] = conn
        with _init_lock:
            if p not in _initialized:
                conn.executescript(_SCHEMA.read_text())
                conn.commit()
                _initialized.add(p)
    return conn


def reset_connections() -> None:
    """Used by tests when switching DB files."""
    cache = getattr(_local, "conns", None) or {}
    for c in cache.values():
        c.close()
    _local.conns = {}
    _initialized.clear()


def table_columns(conn: sqlite3.Connection, table: str) -> list[str]:
    return [r[1] for r in conn.execute(f"PRAGMA table_info({table})")]


def upsert(table: str, rows: Iterable[Mapping[str, Any]], conn: sqlite3.Connection | None = None) -> int:
    """INSERT OR REPLACE rows, silently dropping keys the table doesn't have."""
    conn = conn or connect()
    cols_avail = set(table_columns(conn, table))
    n = 0
    for row in rows:
        r = {k: v for k, v in row.items() if k in cols_avail}
        if not r:
            continue
        cols = ",".join(r)
        qs = ",".join("?" for _ in r)
        conn.execute(f"INSERT OR REPLACE INTO {table} ({cols}) VALUES ({qs})", list(r.values()))
        n += 1
    conn.commit()
    return n


def execute(sql: str, params: Iterable[Any] = (), conn: sqlite3.Connection | None = None) -> sqlite3.Cursor:
    conn = conn or connect()
    cur = conn.execute(sql, tuple(params))
    conn.commit()
    return cur


def query(sql: str, params: Iterable[Any] = (), conn: sqlite3.Connection | None = None) -> list[dict]:
    conn = conn or connect()
    return [dict(r) for r in conn.execute(sql, tuple(params)).fetchall()]


def query_df(sql: str, params: Iterable[Any] = (), conn: sqlite3.Connection | None = None,
             parse_dates: list[str] | None = None) -> pd.DataFrame:
    conn = conn or connect()
    df = pd.read_sql_query(sql, conn, params=tuple(params))
    for c in parse_dates or []:
        if c in df:
            df[c] = pd.to_datetime(df[c])
    return df


def get_state(key: str, default: Any = None) -> Any:
    rows = query("SELECT value FROM sync_state WHERE key=?", (key,))
    if not rows:
        return default
    try:
        return json.loads(rows[0]["value"])
    except (TypeError, ValueError):
        return rows[0]["value"]


def set_state(key: str, value: Any) -> None:
    execute("INSERT OR REPLACE INTO sync_state (key, value) VALUES (?, ?)", (key, json.dumps(value)))


def get_profile() -> dict[str, Any]:
    out = {}
    for r in query("SELECT key, value FROM profile"):
        try:
            out[r["key"]] = json.loads(r["value"])
        except (TypeError, ValueError):
            out[r["key"]] = r["value"]
    return out


def set_profile(**kv: Any) -> None:
    upsert("profile", [{"key": k, "value": json.dumps(v)} for k, v in kv.items()])
