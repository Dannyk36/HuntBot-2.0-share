"""User accounts for the multi-user cockpit.

Each user gets an isolated workspace directory (own jobs DB, config, templates,
artifacts, browser profiles, and .env API keys). Passwords are stored as salted
PBKDF2 hashes; sessions are random tokens with an expiry. Everything lives in
the central DB (the repository-root data/huntbot2.db), while user job data
lives in their workspace DB.
"""
from __future__ import annotations

import hashlib
import json
import re
import secrets
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

from huntbot2.config.workspace import ensure_workspace_dirs
from huntbot2.db.schema import init_db

SESSION_TTL_DAYS = 30
_PBKDF2_ITERATIONS = 200_000

ONBOARDING_QUESTIONS = [
    {"key": "roles", "label": "What roles are you looking for?",
     "hint": "Comma-separated, e.g. Operations Manager, Supply Chain Analyst", "type": "text"},
    {"key": "locations", "label": "Where do you want to work?",
     "hint": "Comma-separated cities, or 'Remote'", "type": "text"},
    {"key": "remote_only", "label": "Remote only?", "type": "bool"},
    {"key": "min_salary", "label": "Minimum salary (optional)", "type": "number"},
    {"key": "exclude_keywords", "label": "Words that should exclude a job (optional)",
     "hint": "Comma-separated, e.g. senior, intern, clearance", "type": "text"},
    {"key": "needs_sponsorship", "label": "Do you need visa sponsorship?", "type": "bool"},
    {"key": "platforms", "label": "Which job platforms do you want to use?",
     "hint": "Comma-separated: linkedin, indeed, glassdoor, handshake, ziprecruiter, dice, wellfound, monster",
     "type": "text"},
]


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _hash_password(password: str, salt: str) -> str:
    return hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), bytes.fromhex(salt), _PBKDF2_ITERATIONS
    ).hex()


def _safe_username(username: str) -> str:
    username = (username or "").strip().lower()
    if not re.fullmatch(r"[a-z0-9_.-]{3,32}", username):
        raise ValueError(
            "username must be 3-32 characters: letters, digits, dot, dash, underscore"
        )
    return username


def workspace_root(base_dir: Path) -> Path:
    return Path(base_dir) / "data" / "users"


def create_user(
    db_path: str | Path,
    *,
    username: str,
    password: str,
    display_name: str = "",
    base_dir: Path,
) -> dict:
    username = _safe_username(username)
    if len(password or "") < 8:
        raise ValueError("password must be at least 8 characters")
    init_db(db_path)
    salt = secrets.token_hex(16)
    pw_hash = _hash_password(password, salt)

    conn = sqlite3.connect(db_path)
    try:
        existing = conn.execute(
            "SELECT id FROM users WHERE username = ?", (username,)
        ).fetchone()
        if existing:
            raise ValueError("username already taken")
        # The very first account becomes the admin.
        first_user = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0
        cur = conn.execute(
            "INSERT INTO users (username, display_name, password_hash, password_salt, "
            "workspace_dir, onboarded, is_admin, created_at, updated_at) VALUES (?,?,?,?,?,0,?,?,?)",
            (username, display_name or username, pw_hash, salt, "",
             1 if first_user else 0, _now(), _now()),
        )
        user_id = int(cur.lastrowid)
        workspace = workspace_root(base_dir) / f"u{user_id}"
        conn.execute(
            "UPDATE users SET workspace_dir = ? WHERE id = ?", (str(workspace), user_id)
        )
        conn.commit()
    finally:
        conn.close()

    ensure_workspace_dirs(workspace)
    init_db(workspace / "data" / "huntbot2.db")
    # Isolate this user's browser logins from everyone else's.
    env_file = workspace / "config" / ".env"
    env_file.write_text(
        f"HUNTBOT2_BROWSER_PROFILE_BASE={workspace / 'browser-profiles'}\n"
    )
    return {"id": user_id, "username": username, "display_name": display_name or username,
            "workspace_dir": str(workspace), "onboarded": False, "is_admin": first_user}


def authenticate(db_path: str | Path, *, username: str, password: str) -> dict | None:
    init_db(db_path)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT * FROM users WHERE username = ?", ((username or "").strip().lower(),)
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        return None
    expected = row["password_hash"]
    candidate = _hash_password(password or "", row["password_salt"])
    if not secrets.compare_digest(candidate, expected):
        return None
    return {"id": int(row["id"]), "username": row["username"],
            "display_name": row["display_name"], "workspace_dir": row["workspace_dir"],
            "onboarded": bool(row["onboarded"]), "is_admin": bool(row["is_admin"])}


def create_session(db_path: str | Path, user_id: int) -> str:
    token = secrets.token_urlsafe(32)
    expires = (datetime.now(UTC) + timedelta(days=SESSION_TTL_DAYS)).isoformat()
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            "INSERT INTO user_sessions (token, user_id, created_at, expires_at) VALUES (?,?,?,?)",
            (token, user_id, _now(), expires),
        )
        conn.commit()
    finally:
        conn.close()
    return token


def resolve_session(db_path: str | Path, token: str | None) -> dict | None:
    """Return the user dict for a valid, unexpired session token, else None."""
    if not token:
        return None
    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        try:
            row = conn.execute(
                "SELECT u.*, s.expires_at FROM user_sessions s JOIN users u ON u.id = s.user_id "
                "WHERE s.token = ?",
                (token,),
            ).fetchone()
        finally:
            conn.close()
    except sqlite3.Error:
        return None
    if row is None:
        return None
    try:
        if datetime.fromisoformat(str(row["expires_at"])) < datetime.now(UTC):
            return None
    except ValueError:
        return None
    return {"id": int(row["id"]), "username": row["username"],
            "display_name": row["display_name"], "workspace_dir": row["workspace_dir"],
            "onboarded": bool(row["onboarded"]), "is_admin": bool(row["is_admin"])}


def destroy_session(db_path: str | Path, token: str | None) -> None:
    if not token:
        return
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("DELETE FROM user_sessions WHERE token = ?", (token,))
        conn.commit()
    finally:
        conn.close()


def save_onboarding(db_path: str | Path, user_id: int, answers: dict) -> None:
    """Log onboarding answers and mark the user onboarded. The caller writes the
    profile.yml derived from these answers into the user's workspace."""
    conn = sqlite3.connect(db_path)
    try:
        now = _now()
        for key, value in answers.items():
            conn.execute(
                "INSERT INTO onboarding_answers (user_id, question_key, answer, created_at) "
                "VALUES (?,?,?,?)",
                (user_id, key, json.dumps(value), now),
            )
        conn.execute(
            "UPDATE users SET onboarded = 1, updated_at = ? WHERE id = ?", (now, user_id)
        )
        conn.commit()
    finally:
        conn.close()


def _workspace_job_stats(workspace_dir: str) -> dict:
    """Best-effort per-user pipeline stats read from their workspace DB."""
    stats = {"jobs_discovered": 0, "applied_total": 0, "applied_last_7d": 0,
             "applied_by_platform": {}, "last_applied_at": None}
    db = Path(workspace_dir) / "data" / "huntbot2.db"
    if not db.exists():
        return stats
    try:
        conn = sqlite3.connect(db)
        try:
            stats["jobs_discovered"] = int(conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0])
            stats["applied_total"] = int(conn.execute(
                "SELECT COUNT(*) FROM jobs WHERE status='applied'").fetchone()[0])
            week_ago = (datetime.now(UTC) - timedelta(days=7)).isoformat()
            stats["applied_last_7d"] = int(conn.execute(
                "SELECT COUNT(*) FROM jobs WHERE status='applied' AND updated_at >= ?",
                (week_ago,)).fetchone()[0])
            stats["applied_by_platform"] = dict(conn.execute(
                "SELECT COALESCE(source,'unknown'), COUNT(*) FROM jobs WHERE status='applied' "
                "GROUP BY COALESCE(source,'unknown')").fetchall())
            row = conn.execute(
                "SELECT MAX(updated_at) FROM jobs WHERE status='applied'").fetchone()
            stats["last_applied_at"] = row[0] if row else None
        finally:
            conn.close()
    except sqlite3.Error:
        pass
    return stats


def admin_overview(db_path: str | Path) -> dict:
    """Cross-account insight for the admin dashboard: account counts, signup
    trend, per-user activity, and platform totals."""
    init_db(db_path)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        users = [dict(r) for r in conn.execute(
            "SELECT id, username, display_name, workspace_dir, onboarded, is_admin, created_at "
            "FROM users ORDER BY id").fetchall()]
        week_ago = (datetime.now(UTC) - timedelta(days=7)).isoformat()
        new_last_7d = int(conn.execute(
            "SELECT COUNT(*) FROM users WHERE created_at >= ?", (week_ago,)).fetchone()[0])
        active_sessions = int(conn.execute(
            "SELECT COUNT(*) FROM user_sessions WHERE expires_at >= ?",
            (datetime.now(UTC).isoformat(),)).fetchone()[0])
    finally:
        conn.close()

    accounts_rows = []
    platform_totals: dict[str, int] = {}
    applied_total = 0
    applied_last_7d = 0
    for user in users:
        stats = _workspace_job_stats(user["workspace_dir"])
        applied_total += stats["applied_total"]
        applied_last_7d += stats["applied_last_7d"]
        for platform, count in stats["applied_by_platform"].items():
            platform_totals[platform] = platform_totals.get(platform, 0) + int(count)
        accounts_rows.append({
            "id": user["id"],
            "username": user["username"],
            "display_name": user["display_name"],
            "onboarded": bool(user["onboarded"]),
            "is_admin": bool(user["is_admin"]),
            "created_at": user["created_at"],
            "jobs_discovered": stats["jobs_discovered"],
            "applied_total": stats["applied_total"],
            "applied_last_7d": stats["applied_last_7d"],
            "last_applied_at": stats["last_applied_at"],
        })

    return {
        "accounts_total": len(users),
        "accounts_new_last_7d": new_last_7d,
        "accounts_onboarded": sum(1 for u in users if u["onboarded"]),
        "active_sessions": active_sessions,
        "applied_total": applied_total,
        "applied_last_7d": applied_last_7d,
        "applied_by_platform": platform_totals,
        "accounts": accounts_rows,
    }


def build_profile_yaml(answers: dict) -> str:
    """Translate onboarding answers into the profile.yml the pipeline reads."""
    def _csv(key: str) -> list[str]:
        raw = answers.get(key) or ""
        if isinstance(raw, list):
            return [str(x).strip() for x in raw if str(x).strip()]
        return [part.strip() for part in str(raw).split(",") if part.strip()]

    roles = _csv("roles") or ["Operations Manager"]
    locations = _csv("locations") or ["Remote"]
    excludes = _csv("exclude_keywords")
    remote_only = bool(answers.get("remote_only"))
    needs_sponsorship = bool(answers.get("needs_sponsorship"))
    min_salary = answers.get("min_salary")

    lines = ["profile:"]
    lines.append("  roles: [" + ", ".join(roles) + "]")
    lines.append("  locations: [" + ", ".join(locations) + "]")
    lines.append(f"  remote_only: {str(remote_only).lower()}")
    if min_salary:
        try:
            lines.append(f"  min_salary: {int(min_salary)}")
        except (TypeError, ValueError):
            pass
    lines.append("filters:")
    lines.append("  exclude_keywords: [" + ", ".join(excludes) + "]")
    lines.append(f"  require_sponsorship: {str(needs_sponsorship).lower()}")
    lines.append("runtime:")
    lines.append("  default_mode: headless")
    lines.append("  max_retries: 1")
    return "\n".join(lines) + "\n"
