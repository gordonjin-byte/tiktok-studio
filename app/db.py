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
  caption_qc_json TEXT, audio_qc_json TEXT, hook_qc_json TEXT, pacing_qc_json TEXT,
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
CREATE TABLE IF NOT EXISTS scripts (
  id TEXT PRIMARY KEY,
  video_id TEXT NOT NULL REFERENCES videos(id) ON DELETE CASCADE,
  raw_text TEXT NOT NULL,
  parsed_json TEXT,
  episode_title TEXT, episode_category TEXT, episode_difficulty TEXT,
  duration_estimate_s REAL, builds_text TEXT, new_piece_text TEXT,
  alt_hooks_json TEXT NOT NULL DEFAULT '[]',
  status TEXT NOT NULL DEFAULT 'parsed',
  checksum TEXT NOT NULL,
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS script_cues (
  id TEXT PRIMARY KEY,
  script_id TEXT NOT NULL REFERENCES scripts(id) ON DELETE CASCADE,
  video_id TEXT NOT NULL REFERENCES videos(id) ON DELETE CASCADE,
  cue_index INTEGER NOT NULL,
  cue_type TEXT NOT NULL,
  source_text TEXT NOT NULL,
  script_time_s REAL,
  anchor_src_t REAL,
  manual_anchor_line_index INTEGER, resolved_anchor_line_index INTEGER,
  line_src_t0 REAL, line_src_t1 REAL,
  resolved_out_t0_s REAL, resolved_out_t1_s REAL,
  match_confidence REAL,
  decision_status TEXT NOT NULL DEFAULT 'pending',
  decision_kind TEXT,
  template_id TEXT, template_props_json TEXT,
  bespoke_brief TEXT, bespoke_module_path TEXT, bespoke_error TEXT,
  duration_s REAL,
  advisor_checksum TEXT, advisor_status TEXT NOT NULL DEFAULT 'none',
  decision_reason TEXT, advisor_confidence REAL,
  visual_qc_status TEXT NOT NULL DEFAULT 'none',
  visual_qc_report TEXT, visual_qc_spec_hash TEXT,
  anchor_word_index INTEGER, timing_status TEXT NOT NULL DEFAULT 'none',
  timing_checksum TEXT, timing_reason TEXT, overlay_skip INTEGER NOT NULL DEFAULT 0,
  available_duration_s REAL,
  user_overridden INTEGER NOT NULL DEFAULT 0,
  error TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS script_renders (
  id TEXT PRIMARY KEY,
  script_id TEXT NOT NULL REFERENCES scripts(id) ON DELETE CASCADE,
  render_id TEXT NOT NULL REFERENCES renders(id) ON DELETE CASCADE,
  job_id TEXT REFERENCES jobs(id), created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_jobs_video ON jobs(video_id);
CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);
CREATE INDEX IF NOT EXISTS idx_renders_video ON renders(video_id);
CREATE INDEX IF NOT EXISTS idx_scripts_video ON scripts(video_id);
CREATE INDEX IF NOT EXISTS idx_script_cues_script ON script_cues(script_id);
CREATE INDEX IF NOT EXISTS idx_script_cues_video ON script_cues(video_id);
CREATE INDEX IF NOT EXISTS idx_script_renders_script ON script_renders(script_id);
CREATE INDEX IF NOT EXISTS idx_script_renders_render ON script_renders(render_id);
"""

import threading

_local = threading.local()
_schema_lock = threading.Lock()
_schema_done = False


def _ensure_column(conn: sqlite3.Connection, table: str, col: str, decl: str) -> None:
    """Add a column to an existing table if missing. CREATE TABLE IF NOT EXISTS
    is a no-op against a table that already exists, so new columns on tables
    present in an already-provisioned db.sqlite3 need this instead."""
    cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
    if col not in cols:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")


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
                _ensure_column(conn, "jobs", "script_id", "TEXT REFERENCES scripts(id)")
                _ensure_column(conn, "renders", "script_fingerprint", "TEXT NOT NULL DEFAULT ''")
                _ensure_column(conn, "script_cues", "line_src_t0", "REAL")
                _ensure_column(conn, "script_cues", "line_src_t1", "REAL")
                _ensure_column(conn, "script_cues", "decision_reason", "TEXT")
                _ensure_column(conn, "script_cues", "advisor_confidence", "REAL")
                _ensure_column(conn, "script_cues", "visual_qc_status", "TEXT NOT NULL DEFAULT 'none'")
                _ensure_column(conn, "script_cues", "visual_qc_report", "TEXT")
                _ensure_column(conn, "script_cues", "visual_qc_spec_hash", "TEXT")
                _ensure_column(conn, "script_cues", "manual_anchor_line_index", "INTEGER")
                _ensure_column(conn, "script_cues", "resolved_anchor_line_index", "INTEGER")
                _ensure_column(conn, "script_cues", "anchor_word_index", "INTEGER")
                _ensure_column(conn, "script_cues", "timing_status", "TEXT NOT NULL DEFAULT 'none'")
                _ensure_column(conn, "script_cues", "timing_checksum", "TEXT")
                _ensure_column(conn, "script_cues", "timing_reason", "TEXT")
                _ensure_column(conn, "script_cues", "overlay_skip", "INTEGER NOT NULL DEFAULT 0")
                _ensure_column(conn, "script_cues", "available_duration_s", "REAL")
                _ensure_column(conn, "renders", "caption_qc_json", "TEXT")
                _ensure_column(conn, "renders", "audio_qc_json", "TEXT")
                _ensure_column(conn, "renders", "hook_qc_json", "TEXT")
                _ensure_column(conn, "renders", "pacing_qc_json", "TEXT")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_jobs_script ON jobs(script_id)")
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
