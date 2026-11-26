from __future__ import annotations

from pathlib import Path
import sqlite3

from huntbot2.apply.runner import normalize_submit_policy
from huntbot2.config.loader import load_config


def run_preflight(project_dir: str | Path) -> dict:
    root = Path(project_dir).resolve()
    config_path = root / "config" / "profile.yml"
    db_path = root / "data" / "huntbot2.db"

    checks: list[dict] = []

    # Config
    try:
        load_config(config_path)
        checks.append({"name": "config", "ok": True, "detail": str(config_path)})
    except Exception as exc:  # noqa: BLE001
        checks.append({"name": "config", "ok": False, "detail": f"{config_path} ({exc})"})

    # DB + required tables
    required = {"runs", "jobs", "artifacts", "queue_items"}
    if not db_path.exists():
        checks.append({"name": "db", "ok": False, "detail": f"{db_path} (missing)"})
    else:
        conn = sqlite3.connect(db_path)
        try:
            tables = {
                row[0]
                for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
            }
        finally:
            conn.close()
        missing = sorted(required - tables)
        if missing:
            checks.append({"name": "db", "ok": False, "detail": f"missing tables: {', '.join(missing)}"})
        else:
            checks.append({"name": "db", "ok": True, "detail": str(db_path)})

    # Submit policy compatibility
    env_file = root / ".env"
    raw_policy = "stop_before_submit"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            if line.startswith("HUNTBOT2_SUBMIT_POLICY="):
                raw_policy = line.split("=", 1)[1].strip()
                break
    normalized = normalize_submit_policy(raw_policy)
    policy_ok = normalized in {"never_submit", "allow_submit_with_token"}
    checks.append(
        {
            "name": "submit_policy",
            "ok": policy_ok,
            "detail": f"raw={raw_policy} normalized={normalized}",
        }
    )

    # Optional browser dependencies
    try:
        import playwright.sync_api  # noqa: F401

        playwright_ok = True
        playwright_detail = "installed"
    except Exception as exc:  # noqa: BLE001
        playwright_ok = False
        playwright_detail = f"not installed ({exc})"
    checks.append({"name": "playwright", "ok": playwright_ok, "detail": playwright_detail, "optional": True})

    hard_failures = [c for c in checks if not c["ok"] and not c.get("optional")]
    optional_warnings = [c for c in checks if not c["ok"] and c.get("optional")]
    return {
        "ok": len(hard_failures) == 0,
        "checks": checks,
        "hard_failures": hard_failures,
        "optional_warnings": optional_warnings,
    }
