"""Multi-user accounts: registration, login, session scoping, onboarding,
workspace isolation, and per-user stats."""
import pytest
from fastapi.testclient import TestClient

import huntbot2.config.env_store as env_store
import huntbot2.web.app as web_app
from huntbot2.db.schema import init_db
from huntbot2.web import accounts


@pytest.fixture(autouse=True)
def setup(tmp_path, monkeypatch):
    monkeypatch.setenv("HUNTBOT2_DB_PATH", str(tmp_path / "data" / "huntbot2.db"))
    monkeypatch.setenv("HUNTBOT2_DATA_ROOT", str(tmp_path))
    monkeypatch.setenv("HUNTBOT2_DISABLE_SCHEDULER", "1")
    env_store._ENV_PATH = tmp_path / ".env"
    init_db(tmp_path / "data" / "huntbot2.db")
    monkeypatch.setattr(web_app, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(web_app, "_run_pipeline_thread", lambda *a, **k: None)
    yield


def _client():
    return TestClient(web_app.app)


# ── accounts module ───────────────────────────────────────────────────────────

def test_create_user_provisions_workspace(tmp_path):
    db = tmp_path / "data" / "huntbot2.db"
    user = accounts.create_user(db, username="alice", password="hunter2secure",
                                display_name="Alice", base_dir=tmp_path)
    ws = tmp_path / "data" / "users" / f"u{user['id']}"
    assert ws.exists()
    assert (ws / "data" / "huntbot2.db").exists()
    assert (ws / "config" / ".env").exists()
    assert "browser-profiles" in (ws / "config" / ".env").read_text()


def test_duplicate_username_rejected(tmp_path):
    db = tmp_path / "data" / "huntbot2.db"
    accounts.create_user(db, username="bob", password="password123", base_dir=tmp_path)
    with pytest.raises(ValueError, match="taken"):
        accounts.create_user(db, username="bob", password="password456", base_dir=tmp_path)


def test_short_password_rejected(tmp_path):
    with pytest.raises(ValueError, match="8 characters"):
        accounts.create_user(tmp_path / "data" / "huntbot2.db",
                             username="carol", password="short", base_dir=tmp_path)


def test_authenticate_right_and_wrong_password(tmp_path):
    db = tmp_path / "data" / "huntbot2.db"
    accounts.create_user(db, username="dave", password="correcthorse", base_dir=tmp_path)
    assert accounts.authenticate(db, username="dave", password="correcthorse") is not None
    assert accounts.authenticate(db, username="dave", password="wrong") is None


def test_session_roundtrip(tmp_path):
    db = tmp_path / "data" / "huntbot2.db"
    user = accounts.create_user(db, username="erin", password="password123", base_dir=tmp_path)
    token = accounts.create_session(db, user["id"])
    resolved = accounts.resolve_session(db, token)
    assert resolved is not None and resolved["username"] == "erin"
    accounts.destroy_session(db, token)
    assert accounts.resolve_session(db, token) is None


def test_build_profile_yaml_from_answers():
    yaml_text = accounts.build_profile_yaml({
        "roles": "Operations Manager, Analyst",
        "locations": "Remote, Phoenix",
        "remote_only": True,
        "needs_sponsorship": True,
        "exclude_keywords": "senior",
        "min_salary": "70000",
    })
    assert "roles: [Operations Manager, Analyst]" in yaml_text
    assert "remote_only: true" in yaml_text
    assert "require_sponsorship: true" in yaml_text
    assert "min_salary: 70000" in yaml_text


# ── HTTP flow ─────────────────────────────────────────────────────────────────

def test_register_login_me_flow():
    c = _client()
    r = c.post("/api/auth/register", json={"username": "frank", "password": "password123"})
    assert r.status_code == 200
    me = c.get("/api/auth/me").json()
    assert me["authenticated"] is True
    assert me["user"]["username"] == "frank"
    assert me["user"]["onboarded"] is False

    c.post("/api/auth/logout")
    me2 = c.get("/api/auth/me").json()
    assert me2["authenticated"] is False

    r = c.post("/api/auth/login", json={"username": "frank", "password": "password123"})
    assert r.status_code == 200
    assert c.get("/api/auth/me").json()["authenticated"] is True


def test_login_rejects_bad_credentials():
    c = _client()
    c.post("/api/auth/register", json={"username": "gina", "password": "password123"})
    c.post("/api/auth/logout")
    assert c.post("/api/auth/login", json={"username": "gina", "password": "nope-nope"}).status_code == 401


def test_signups_can_be_disabled(monkeypatch):
    monkeypatch.setenv("HUNTBOT2_ALLOW_SIGNUPS", "false")
    c = _client()
    assert c.post("/api/auth/register", json={"username": "harry", "password": "password123"}).status_code == 403


def test_two_users_have_isolated_job_data(tmp_path):
    import sqlite3
    c1, c2 = _client(), _client()
    c1.post("/api/auth/register", json={"username": "user.one", "password": "password123"})
    c2.post("/api/auth/register", json={"username": "user.two", "password": "password123"})

    # Insert a job directly into user one's workspace DB.
    ws1 = tmp_path / "data" / "users" / "u1"
    conn = sqlite3.connect(ws1 / "data" / "huntbot2.db")
    conn.execute(
        "INSERT INTO jobs (source, title, company, url, created_at, status) VALUES (?,?,?,?,?,?)",
        ("linkedin", "Only Mine", "Acme", "https://x/1", "2026-01-01", "applied"),
    )
    conn.commit()
    conn.close()

    s1 = c1.get("/api/stats/applications").json()
    s2 = c2.get("/api/stats/applications").json()
    assert s1["applied_total"] == 1
    assert s1["applied_by_platform"] == {"linkedin": 1}
    assert s2["applied_total"] == 0


def test_onboarding_writes_profile_into_workspace(tmp_path):
    c = _client()
    c.post("/api/auth/register", json={"username": "ivy", "password": "password123"})
    q = c.get("/api/onboarding").json()
    assert q["onboarded"] is False
    assert any(item["key"] == "roles" for item in q["questions"])

    r = c.post("/api/onboarding", json={"answers": {
        "roles": "Data Analyst", "locations": "Remote", "remote_only": True,
        "platforms": "linkedin, handshake",
    }})
    assert r.status_code == 200
    ws = tmp_path / "data" / "users" / "u1"
    profile = (ws / "config" / "profile.yml").read_text()
    assert "Data Analyst" in profile
    assert c.get("/api/auth/me").json()["user"]["onboarded"] is True
    # Platform choice lands in the user's own .env, not the shared one.
    assert "handshake" in (ws / "config" / ".env").read_text()


def test_template_upload_saves_docx(tmp_path):
    import base64
    c = _client()
    c.post("/api/auth/register", json={"username": "jack", "password": "password123"})
    blob = base64.b64encode(b"PK\x03\x04 fake docx bytes").decode()
    r = c.post("/api/onboarding/template", json={
        "kind": "resume", "filename": "my_resume.docx", "content_b64": blob,
    })
    assert r.status_code == 200
    ws = tmp_path / "data" / "users" / "u1"
    assert (ws / "config" / "resume_template.docx").exists()


def test_template_upload_rejects_non_docx():
    c = _client()
    c.post("/api/auth/register", json={"username": "kate", "password": "password123"})
    r = c.post("/api/onboarding/template", json={
        "kind": "resume", "filename": "resume.pdf", "content_b64": "aGk=",
    })
    assert r.status_code == 422


# ── admin dashboard ───────────────────────────────────────────────────────────

def test_first_user_is_admin_second_is_not(tmp_path):
    db = tmp_path / "data" / "huntbot2.db"
    first = accounts.create_user(db, username="root.user", password="password123", base_dir=tmp_path)
    second = accounts.create_user(db, username="guest", password="password123", base_dir=tmp_path)
    assert first["is_admin"] is True
    assert second["is_admin"] is False


def test_admin_overview_counts_accounts_and_activity(tmp_path):
    import sqlite3
    admin, friend = _client(), _client()
    admin.post("/api/auth/register", json={"username": "owner", "password": "password123"})
    friend.post("/api/auth/register", json={"username": "friend", "password": "password123"})

    # Give the friend one applied job in their isolated workspace.
    ws2 = tmp_path / "data" / "users" / "u2"
    conn = sqlite3.connect(ws2 / "data" / "huntbot2.db")
    conn.execute(
        "INSERT INTO jobs (source, title, company, url, created_at, status, updated_at) "
        "VALUES (?,?,?,?,?,?,?)",
        ("indeed", "Friend Job", "Acme", "https://x/2", "2026-01-01", "applied", "2026-06-11T00:00:00+00:00"),
    )
    conn.commit()
    conn.close()

    d = admin.get("/api/admin/overview").json()
    assert d["accounts_total"] == 2
    assert d["accounts_new_last_7d"] == 2
    assert d["applied_total"] == 1
    assert d["applied_by_platform"] == {"indeed": 1}
    by_name = {a["username"]: a for a in d["accounts"]}
    assert by_name["owner"]["is_admin"] is True
    assert by_name["friend"]["applied_total"] == 1


def test_admin_overview_denied_for_non_admin():
    admin, friend = _client(), _client()
    admin.post("/api/auth/register", json={"username": "boss", "password": "password123"})
    friend.post("/api/auth/register", json={"username": "pal", "password": "password123"})
    assert friend.get("/api/admin/overview").status_code == 403
    assert admin.get("/api/admin/overview").status_code == 200


def test_admin_overview_allowed_for_legacy_operator():
    # No user session at all (single-user cockpit on loopback) still sees it.
    c = _client()
    assert c.get("/api/admin/overview").status_code == 200
