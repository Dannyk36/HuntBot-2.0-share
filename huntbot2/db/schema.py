from pathlib import Path
import sqlite3

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_type TEXT NOT NULL,
  status TEXT NOT NULL,
  started_at TEXT NOT NULL,
  finished_at TEXT,
  failure_code TEXT,
  notes TEXT
);

CREATE TABLE IF NOT EXISTS jobs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  source TEXT NOT NULL,
  title TEXT NOT NULL,
  company TEXT,
  location TEXT,
  url TEXT,
  posted_at TEXT,
  dedup_key TEXT UNIQUE,
  raw_snapshot TEXT,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS artifacts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id INTEGER,
  kind TEXT NOT NULL,
  path TEXT NOT NULL,
  meta_json TEXT,
  created_at TEXT NOT NULL,
  FOREIGN KEY(run_id) REFERENCES runs(id)
);

CREATE TABLE IF NOT EXISTS queue_items (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id TEXT,
  queue_name TEXT NOT NULL,
  queue_code TEXT NOT NULL,
  next_action TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  status TEXT NOT NULL,
  attempts INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  last_error TEXT
);

CREATE TABLE IF NOT EXISTS cockpit_takeover_flags (
  session_key TEXT PRIMARY KEY,
  takeover_requested INTEGER NOT NULL DEFAULT 0,
  checkpoint_required INTEGER NOT NULL DEFAULT 1,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS cockpit_session_workflows (
  platform TEXT NOT NULL,
  session_key TEXT NOT NULL,
  state TEXT NOT NULL DEFAULT 'paused',
  reason_code TEXT,
  note TEXT,
  updated_at TEXT NOT NULL,
  PRIMARY KEY(platform, session_key)
);

CREATE TABLE IF NOT EXISTS cockpit_browser_profiles (
  platform TEXT PRIMARY KEY,
  profile_path TEXT NOT NULL,
  active INTEGER NOT NULL DEFAULT 0,
  status TEXT NOT NULL DEFAULT 'idle',
  last_used_at TEXT,
  note TEXT,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS cockpit_policy_settings (
  policy_key TEXT PRIMARY KEY,
  daily_apply_cap INTEGER NOT NULL DEFAULT 20,
  min_seconds_between_actions INTEGER NOT NULL DEFAULT 20,
  require_human_checkpoint INTEGER NOT NULL DEFAULT 1,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS cockpit_policy_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  policy_key TEXT NOT NULL,
  event_type TEXT NOT NULL,
  created_at TEXT NOT NULL,
  meta_json TEXT
);

CREATE TABLE IF NOT EXISTS cockpit_resume_steps (
  platform TEXT NOT NULL,
  session_key TEXT NOT NULL,
  resume_step TEXT NOT NULL DEFAULT 'open_job',
  updated_by TEXT,
  note TEXT,
  updated_at TEXT NOT NULL,
  PRIMARY KEY(platform, session_key)
);

CREATE TABLE IF NOT EXISTS cockpit_scheduler_state (
  platform TEXT PRIMARY KEY,
  paused INTEGER NOT NULL DEFAULT 0,
  window_start TEXT NOT NULL DEFAULT '08:00',
  window_end TEXT NOT NULL DEFAULT '20:00',
  timezone TEXT NOT NULL DEFAULT 'UTC',
  note TEXT,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS cockpit_operator_timeline (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  event_type TEXT NOT NULL,
  actor TEXT NOT NULL,
  platform TEXT,
  session_key TEXT,
  details_json TEXT,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS cockpit_platform_pacing (
  platform TEXT PRIMARY KEY,
  preset TEXT NOT NULL DEFAULT 'balanced',
  updated_by TEXT,
  note TEXT,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS cockpit_alerts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  severity TEXT NOT NULL,
  alert_type TEXT NOT NULL,
  dedupe_key TEXT NOT NULL,
  message TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'new',
  meta_json TEXT,
  occurrence_count INTEGER NOT NULL DEFAULT 1,
  first_seen_at TEXT NOT NULL,
  last_seen_at TEXT NOT NULL,
  acked_at TEXT,
  resolved_at TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS app_logs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  level TEXT NOT NULL DEFAULT 'info',
  source TEXT NOT NULL,
  message TEXT NOT NULL,
  details TEXT,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS cockpit_remediation_policies (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  alert_type TEXT NOT NULL,
  platform_scope TEXT NOT NULL,
  enabled INTEGER NOT NULL DEFAULT 0,
  cooldown_minutes INTEGER NOT NULL DEFAULT 60,
  max_actions_per_day INTEGER NOT NULL DEFAULT 3,
  updated_by TEXT,
  updated_at TEXT NOT NULL,
  UNIQUE(alert_type, platform_scope)
);

CREATE TABLE IF NOT EXISTS users (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  username TEXT NOT NULL UNIQUE,
  display_name TEXT,
  password_hash TEXT NOT NULL,
  password_salt TEXT NOT NULL,
  workspace_dir TEXT NOT NULL,
  onboarded INTEGER NOT NULL DEFAULT 0,
  is_admin INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS user_sessions (
  token TEXT PRIMARY KEY,
  user_id INTEGER NOT NULL,
  created_at TEXT NOT NULL,
  expires_at TEXT NOT NULL,
  FOREIGN KEY(user_id) REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS onboarding_answers (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL,
  question_key TEXT NOT NULL,
  answer TEXT,
  created_at TEXT NOT NULL,
  FOREIGN KEY(user_id) REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS auto_apply_entries (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  day_of_week INTEGER NOT NULL,
  time_hhmm TEXT NOT NULL,
  platform TEXT NOT NULL,
  jobs_per_run INTEGER NOT NULL DEFAULT 5,
  mode TEXT NOT NULL DEFAULT 'supervised',
  enabled INTEGER NOT NULL DEFAULT 1,
  last_run_at TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS cockpit_remediation_runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  alert_id INTEGER,
  alert_type TEXT NOT NULL,
  platform_scope TEXT NOT NULL,
  trigger_mode TEXT NOT NULL,
  dry_run INTEGER NOT NULL DEFAULT 0,
  status TEXT NOT NULL,
  notes TEXT,
  actions_json TEXT,
  created_at TEXT NOT NULL,
  FOREIGN KEY(alert_id) REFERENCES cockpit_alerts(id)
);
"""


def _ensure_runs_columns(conn: sqlite3.Connection) -> None:
    columns = {row[1] for row in conn.execute("PRAGMA table_info(runs)").fetchall()}
    if "failure_code" not in columns:
        conn.execute("ALTER TABLE runs ADD COLUMN failure_code TEXT")


def _ensure_jobs_columns(conn: sqlite3.Connection) -> None:
    columns = {row[1] for row in conn.execute("PRAGMA table_info(jobs)").fetchall()}
    if "posted_at" not in columns:
        conn.execute("ALTER TABLE jobs ADD COLUMN posted_at TEXT")


def _ensure_queue_items_columns(conn: sqlite3.Connection) -> None:
    columns = {row[1] for row in conn.execute("PRAGMA table_info(queue_items)").fetchall()}
    migrations: list[tuple[str, str]] = [
        ("run_id", "TEXT"),
        ("queue_name", "TEXT"),
        ("queue_code", "TEXT"),
        ("next_action", "TEXT"),
        ("payload_json", "TEXT"),
        ("status", "TEXT"),
        ("attempts", "INTEGER NOT NULL DEFAULT 0"),
        ("created_at", "TEXT"),
        ("updated_at", "TEXT"),
        ("last_error", "TEXT"),
    ]
    for name, dtype in migrations:
        if name not in columns:
            conn.execute(f"ALTER TABLE queue_items ADD COLUMN {name} {dtype}")


def _ensure_browser_profile_columns(conn: sqlite3.Connection) -> None:
    columns = {row[1] for row in conn.execute("PRAGMA table_info(cockpit_browser_profiles)").fetchall()}
    migrations: list[tuple[str, str]] = [
        ("platform", "TEXT"),
        ("profile_path", "TEXT"),
        ("active", "INTEGER NOT NULL DEFAULT 0"),
        ("status", "TEXT NOT NULL DEFAULT 'idle'"),
        ("last_used_at", "TEXT"),
        ("note", "TEXT"),
        ("updated_at", "TEXT"),
    ]
    for name, dtype in migrations:
        if name not in columns:
            conn.execute(f"ALTER TABLE cockpit_browser_profiles ADD COLUMN {name} {dtype}")


def _ensure_policy_settings_columns(conn: sqlite3.Connection) -> None:
    columns = {row[1] for row in conn.execute("PRAGMA table_info(cockpit_policy_settings)").fetchall()}
    migrations: list[tuple[str, str]] = [
        ("policy_key", "TEXT"),
        ("daily_apply_cap", "INTEGER NOT NULL DEFAULT 20"),
        ("min_seconds_between_actions", "INTEGER NOT NULL DEFAULT 20"),
        ("require_human_checkpoint", "INTEGER NOT NULL DEFAULT 1"),
        ("updated_at", "TEXT"),
    ]
    for name, dtype in migrations:
        if name not in columns:
            conn.execute(f"ALTER TABLE cockpit_policy_settings ADD COLUMN {name} {dtype}")


def _ensure_session_workflow_columns(conn: sqlite3.Connection) -> None:
    columns = {row[1] for row in conn.execute("PRAGMA table_info(cockpit_session_workflows)").fetchall()}
    migrations: list[tuple[str, str]] = [
        ("platform", "TEXT"),
        ("session_key", "TEXT"),
        ("state", "TEXT NOT NULL DEFAULT 'paused'"),
        ("reason_code", "TEXT"),
        ("note", "TEXT"),
        ("updated_at", "TEXT"),
    ]
    for name, dtype in migrations:
        if name not in columns:
            conn.execute(f"ALTER TABLE cockpit_session_workflows ADD COLUMN {name} {dtype}")


def _ensure_platform_pacing_columns(conn: sqlite3.Connection) -> None:
    columns = {row[1] for row in conn.execute("PRAGMA table_info(cockpit_platform_pacing)").fetchall()}
    migrations: list[tuple[str, str]] = [
        ("platform", "TEXT"),
        ("preset", "TEXT NOT NULL DEFAULT 'balanced'"),
        ("updated_by", "TEXT"),
        ("note", "TEXT"),
        ("updated_at", "TEXT"),
    ]
    for name, dtype in migrations:
        if name not in columns:
            conn.execute(f"ALTER TABLE cockpit_platform_pacing ADD COLUMN {name} {dtype}")


def _ensure_alert_columns(conn: sqlite3.Connection) -> None:
    columns = {row[1] for row in conn.execute("PRAGMA table_info(cockpit_alerts)").fetchall()}
    migrations: list[tuple[str, str]] = [
        ("severity", "TEXT NOT NULL DEFAULT 'warning'"),
        ("alert_type", "TEXT NOT NULL DEFAULT 'unknown'"),
        ("dedupe_key", "TEXT NOT NULL DEFAULT ''"),
        ("message", "TEXT NOT NULL DEFAULT ''"),
        ("status", "TEXT NOT NULL DEFAULT 'new'"),
        ("meta_json", "TEXT"),
        ("occurrence_count", "INTEGER NOT NULL DEFAULT 1"),
        ("first_seen_at", "TEXT"),
        ("last_seen_at", "TEXT"),
        ("acked_at", "TEXT"),
        ("resolved_at", "TEXT"),
        ("created_at", "TEXT"),
        ("updated_at", "TEXT"),
    ]
    for name, dtype in migrations:
        if name not in columns:
            conn.execute(f"ALTER TABLE cockpit_alerts ADD COLUMN {name} {dtype}")


def _ensure_remediation_policy_columns(conn: sqlite3.Connection) -> None:
    columns = {row[1] for row in conn.execute("PRAGMA table_info(cockpit_remediation_policies)").fetchall()}
    migrations: list[tuple[str, str]] = [
        ("alert_type", "TEXT"),
        ("platform_scope", "TEXT"),
        ("enabled", "INTEGER NOT NULL DEFAULT 0"),
        ("cooldown_minutes", "INTEGER NOT NULL DEFAULT 60"),
        ("max_actions_per_day", "INTEGER NOT NULL DEFAULT 3"),
        ("updated_by", "TEXT"),
        ("updated_at", "TEXT"),
    ]
    for name, dtype in migrations:
        if name not in columns:
            conn.execute(f"ALTER TABLE cockpit_remediation_policies ADD COLUMN {name} {dtype}")


def _ensure_remediation_run_columns(conn: sqlite3.Connection) -> None:
    columns = {row[1] for row in conn.execute("PRAGMA table_info(cockpit_remediation_runs)").fetchall()}
    migrations: list[tuple[str, str]] = [
        ("alert_id", "INTEGER"),
        ("alert_type", "TEXT"),
        ("platform_scope", "TEXT"),
        ("trigger_mode", "TEXT"),
        ("dry_run", "INTEGER NOT NULL DEFAULT 0"),
        ("status", "TEXT"),
        ("notes", "TEXT"),
        ("actions_json", "TEXT"),
        ("created_at", "TEXT"),
    ]
    for name, dtype in migrations:
        if name not in columns:
            conn.execute(f"ALTER TABLE cockpit_remediation_runs ADD COLUMN {name} {dtype}")


def _ensure_jobs_pipeline_columns(conn: sqlite3.Connection) -> None:
    columns = {row[1] for row in conn.execute("PRAGMA table_info(jobs)").fetchall()}
    migrations: list[tuple[str, str]] = [
        ("score", "REAL"),
        ("score_reasons_json", "TEXT"),
        ("status", "TEXT DEFAULT 'discovered'"),
        ("sponsorship_confidence", "TEXT"),
        ("updated_at", "TEXT"),
        ("description", "TEXT"),
    ]
    for name, dtype in migrations:
        if name not in columns:
            conn.execute(f"ALTER TABLE jobs ADD COLUMN {name} {dtype}")


def _ensure_artifacts_job_id_column(conn: sqlite3.Connection) -> None:
    columns = {row[1] for row in conn.execute("PRAGMA table_info(artifacts)").fetchall()}
    if "job_id" not in columns:
        conn.execute("ALTER TABLE artifacts ADD COLUMN job_id INTEGER")


def _ensure_users_columns(conn: sqlite3.Connection) -> None:
    columns = {row[1] for row in conn.execute("PRAGMA table_info(users)").fetchall()}
    if "is_admin" not in columns:
        conn.execute("ALTER TABLE users ADD COLUMN is_admin INTEGER NOT NULL DEFAULT 0")


def init_db(db_path: str | Path) -> None:
    p = Path(db_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(p)
    try:
        conn.executescript(SCHEMA_SQL)
        _ensure_runs_columns(conn)
        _ensure_jobs_columns(conn)
        _ensure_queue_items_columns(conn)
        _ensure_browser_profile_columns(conn)
        _ensure_policy_settings_columns(conn)
        _ensure_session_workflow_columns(conn)
        _ensure_platform_pacing_columns(conn)
        _ensure_alert_columns(conn)
        _ensure_remediation_policy_columns(conn)
        _ensure_remediation_run_columns(conn)
        _ensure_jobs_pipeline_columns(conn)
        _ensure_artifacts_job_id_column(conn)
        _ensure_users_columns(conn)
        conn.commit()
    finally:
        conn.close()
