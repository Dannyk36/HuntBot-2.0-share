"""Tests for MCP server tool handlers — direct function calls, no network."""
import json
import sqlite3
import pytest
from datetime import datetime, UTC
from pathlib import Path
import huntbot2.config.env_store as env_store


@pytest.fixture(autouse=True)
def tmp_project(tmp_path, monkeypatch):
    env_store._ENV_PATH = tmp_path / ".env"
    monkeypatch.setenv("HUNTBOT2_DB_PATH", str(tmp_path / "data" / "huntbot2.db"))
    monkeypatch.setenv("HUNTBOT2_DATA_ROOT", str(tmp_path))
    from huntbot2.db.schema import init_db
    (tmp_path / "data").mkdir(parents=True)
    (tmp_path / "config").mkdir()
    (tmp_path / "fixtures").mkdir()
    (tmp_path / "artifacts").mkdir()
    init_db(tmp_path / "data" / "huntbot2.db")
    (tmp_path / "config" / "profile.yml").write_text(
        "profile:\n  roles: [Ops Manager]\n  locations: [Phoenix]\n  remote_only: false\n"
        "filters:\n  exclude_keywords: [intern]\n"
        "apply:\n  target_jobs_per_run: 5\n"
    )
    (tmp_path / "fixtures" / "sample_linkedin_search.html").write_text("<html></html>")
    yield tmp_path


def _insert_job(db_path, title="Ops Manager", company="Acme", score=None, status="discovered"):
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO jobs (source, title, company, location, url, created_at, dedup_key, score, status) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        ("test", title, company, "Phoenix",
         f"https://acme.example/{title.lower().replace(' ', '-')}",
         datetime.now(UTC).isoformat(),
         f"acme-{title.lower().replace(' ', '-')}",
         score, status),
    )
    conn.commit()
    conn.close()


@pytest.mark.asyncio
async def test_huntbot_status_empty_db(tmp_project):
    from huntbot2.mcp.server import _tool_huntbot_status
    result = await _tool_huntbot_status({})
    assert "discovered" in result
    assert "scored" in result
    assert "tailored" in result
    assert "applied" in result
    assert "llm_enabled" in result
    assert result["discovered"] == 0


@pytest.mark.asyncio
async def test_huntbot_status_with_jobs(tmp_project):
    db_path = tmp_project / "data" / "huntbot2.db"
    _insert_job(db_path, title="Job A", status="discovered")
    _insert_job(db_path, title="Job B", status="scored", score=70)
    from huntbot2.mcp.server import _tool_huntbot_status
    result = await _tool_huntbot_status({})
    assert result["discovered"] >= 1
    assert result["scored"] >= 1


@pytest.mark.asyncio
async def test_huntbot_get_jobs_returns_list(tmp_project):
    db_path = tmp_project / "data" / "huntbot2.db"
    _insert_job(db_path, score=85, status="scored")
    from huntbot2.mcp.server import _tool_huntbot_get_jobs
    result = await _tool_huntbot_get_jobs({"limit": 10})
    assert isinstance(result, list)
    assert len(result) >= 1
    assert "title" in result[0]
    assert "job_id" in result[0]


@pytest.mark.asyncio
async def test_huntbot_get_jobs_filters_by_status(tmp_project):
    db_path = tmp_project / "data" / "huntbot2.db"
    _insert_job(db_path, title="Job A", status="discovered")
    _insert_job(db_path, title="Job B", status="scored", score=70)
    from huntbot2.mcp.server import _tool_huntbot_get_jobs
    result = await _tool_huntbot_get_jobs({"status": "scored"})
    assert all(j["status"] == "scored" for j in result)


@pytest.mark.asyncio
async def test_huntbot_get_jobs_empty_db(tmp_project):
    from huntbot2.mcp.server import _tool_huntbot_get_jobs
    result = await _tool_huntbot_get_jobs({})
    assert result == []


@pytest.mark.asyncio
async def test_huntbot_add_url_invalid(tmp_project):
    from huntbot2.mcp.server import _tool_huntbot_add_url
    result = await _tool_huntbot_add_url({"url": "not-a-url"})
    assert "error" in result


@pytest.mark.asyncio
async def test_huntbot_add_url_valid(tmp_project):
    from huntbot2.mcp.server import _tool_huntbot_add_url
    result = await _tool_huntbot_add_url({"url": "https://example.com/job/1"})
    assert "job_id" in result
    assert result.get("error") is None


@pytest.mark.asyncio
async def test_huntbot_add_url_duplicate(tmp_project):
    from huntbot2.mcp.server import _tool_huntbot_add_url
    r1 = await _tool_huntbot_add_url({"url": "https://example.com/job/99"})
    r2 = await _tool_huntbot_add_url({"url": "https://example.com/job/99"})
    assert r1.get("error") is None
    assert r2.get("duplicate") is True


@pytest.mark.asyncio
async def test_huntbot_discover_returns_counts(tmp_project):
    from huntbot2.mcp.server import _tool_huntbot_discover
    result = await _tool_huntbot_discover({})
    assert "jobs_found" in result
    assert "new" in result
    assert "source" in result


@pytest.mark.asyncio
async def test_huntbot_score_returns_shortlist(tmp_project):
    db_path = tmp_project / "data" / "huntbot2.db"
    _insert_job(db_path, title="Ops Manager", status="discovered")
    from huntbot2.mcp.server import _tool_huntbot_score
    result = await _tool_huntbot_score({})
    assert "scored" in result
    assert "shortlist" in result
    assert isinstance(result["shortlist"], list)


@pytest.mark.asyncio
async def test_huntbot_health_reports_deps(tmp_project):
    from huntbot2.mcp.server import _tool_huntbot_health
    result = await _tool_huntbot_health({})
    assert "db_exists" in result
    assert "playwright_installed" in result
    assert "chromium_ready" in result
    assert "llm_enabled" in result
    assert "submit_policy" in result
    assert "ready_to_apply" in result
    assert result["db_exists"] is True
    assert result["submit_policy"] in ("never_submit", "allow_submit_with_token")


@pytest.mark.asyncio
async def test_huntbot_health_stale_url_count(tmp_project):
    db_path = tmp_project / "data" / "huntbot2.db"
    _insert_job(db_path, title="Gone Job", status="stale_url")
    from huntbot2.mcp.server import _tool_huntbot_health
    result = await _tool_huntbot_health({})
    assert result["stale_url_jobs"] == 1


@pytest.mark.asyncio
async def test_huntbot_simulate_apply_no_browser(tmp_project, monkeypatch):
    """simulate_apply returns structured no_browser when playwright missing."""
    import huntbot2.mcp.server as srv
    monkeypatch.setattr(srv, "_playwright_available", lambda: False)
    result = await srv._tool_huntbot_simulate_apply({})
    assert result["status"] == "no_browser"
    assert result["gate_decision"] == "blocked"


@pytest.mark.asyncio
async def test_huntbot_apply_no_browser(tmp_project, monkeypatch):
    """apply returns structured no_browser when playwright missing."""
    import huntbot2.mcp.server as srv
    monkeypatch.setattr(srv, "_playwright_available", lambda: False)
    result = await srv._tool_huntbot_apply({})
    assert result["status"] == "no_browser"
    assert result["gate_decision"] == "blocked"


@pytest.mark.asyncio
async def test_tool_schemas_are_valid(tmp_project):
    from huntbot2.mcp.server import _TOOL_SCHEMAS, _TOOL_HANDLERS
    assert len(_TOOL_SCHEMAS) == 10
    for schema in _TOOL_SCHEMAS:
        assert "name" in schema
        assert "description" in schema
        assert "inputSchema" in schema
        assert schema["name"] in _TOOL_HANDLERS


@pytest.mark.asyncio
async def test_submit_policy_enum_is_canonical(tmp_project):
    """huntbot_apply and huntbot_run schemas only expose canonical policy values."""
    from huntbot2.mcp.server import _TOOL_SCHEMAS
    canonical = {"never_submit", "allow_submit_with_token"}
    for schema in _TOOL_SCHEMAS:
        props = schema.get("inputSchema", {}).get("properties", {})
        if "policy" in props:
            enum_vals = set(props["policy"].get("enum", []))
            assert enum_vals == canonical, f"{schema['name']} exposes non-canonical policy values: {enum_vals - canonical}"
