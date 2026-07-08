"""sqlite access: WAL, busy_timeout, schema DDL, thin query helpers.
Single process; writes come from the API handlers and the one worker task."""
from __future__ import annotations

import json
import sqlite3
import time
import uuid
from typing import Any, Optional

from . import config

_SCHEMA = """
CREATE TABLE IF NOT EXISTS videos (
  id TEXT PRIMARY KEY,
  filename TEXT NOT NULL,
  sha256 TEXT UNIQUE NOT NULL,
  duration_s REAL, width INTEGER, height INTEGER, fps REAL, size_bytes INTEGER,
  status TEXT NOT NULL DEFAULT 'ingested',
  analysis_version INTEGER,
  brain_status TEXT NOT NULL DEFAULT 'none',
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS jobs (
  id TEXT PRIMARY KEY,
  video_id TEXT NOT NULL REFERENCES videos(id) ON DELETE CASCADE,
  type TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'queued',
  stage TEXT DEFAULT '',
  progress REAL NOT NULL DEFAULT 0,
  message TEXT DEFAULT '',
  error TEXT,
  settings_json TEXT NOT NULL,
  variants_json TEXT NOT NULL DEFAULT '["hook_a","hook_b","hook_c"]',
  created_at TEXT NOT NULL,
  started_at TEXT, finished_at TEXT
);
CREATE TABLE IF NOT EXISTS renders (
  id TEXT PRIMARY KEY,
  video_id TEXT NOT NULL REFERENCES videos(id) ON DELETE CASCADE,
  job_id TEXT REFERENCES jobs(id),
  variant TEXT NOT NULL,
  settings_json TEXT NOT NULL,
  settings_hash TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'rendering',
  qc_json TEXT,
  output_path TEXT,
  duration_s REAL, size_bytes INTEGER,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS presets (
  id TEXT PRIMARY KEY,
  name TEXT UNIQUE NOT NULL,
  settings_json TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS app_state (key TEXT PRIMARY KEY, value TEXT);
CREATE INDEX IF NOT EXISTS idx_jobs_video ON jobs(video_id);
CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);
CREATE INDEX IF NOT EXISTS idx_renders_video ON renders(video_id);
"""

import threading

_local = threading.local()
_schema_lock = threading.Lock()
_schema_done = False


def now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def new_id() -> str:
    return uuid.uuid4().hex[:12]


def get_conn() -> sqlite3.Connection:
    """One connection per thread — sqlite connections must not be used
    concurrently across threads (FastAPI sync endpoints run in a threadpool).
    WAL + busy_timeout makes concurrent readers/writer safe."""
    global _schema_done
    conn = getattr(_local, "conn", None)
    if conn is None:
        conn = sqlite3.connect(config.DB_PATH)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        conn.execute("PRAGMA foreign_keys=ON")
        with _schema_lock:
            if not _schema_done:
                conn.executescript(_SCHEMA)
                conn.commit()
                _schema_done = True
        _local.conn = conn
    return conn


def query(sql: str, args: tuple = ()) -> list[dict[str, Any]]:
    cur = get_conn().execute(sql, args)
    return [dict(r) for r in cur.fetchall()]


def query_one(sql: str, args: tuple = ()) -> Optional[dict[str, Any]]:
    rows = query(sql, args)
    return rows[0] if rows else None


def execute(sql: str, args: tuple = ()) -> None:
    conn = get_conn()
    conn.execute(sql, args)
    conn.commit()


def insert(table: str, row: dict[str, Any]) -> None:
    keys = ", ".join(row)
    ph = ", ".join("?" for _ in row)
    execute(f"INSERT INTO {table} ({keys}) VALUES ({ph})", tuple(row.values()))


def update(table: str, row_id: str, fields: dict[str, Any]) -> None:
    sets = ", ".join(f"{k}=?" for k in fields)
    execute(f"UPDATE {table} SET {sets} WHERE id=?", (*fields.values(), row_id))


def get_state(key: str, default: Any = None) -> Any:
    row = query_one("SELECT value FROM app_state WHERE key=?", (key,))
    return json.loads(row["value"]) if row else default


def set_state(key: str, value: Any) -> None:
    execute("INSERT INTO app_state (key,value) VALUES (?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, json.dumps(value)))
