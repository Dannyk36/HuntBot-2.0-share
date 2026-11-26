import os
import pytest
from fastapi.testclient import TestClient
import huntbot2.config.env_store as env_store
from huntbot2.web.app import app

client = TestClient(app)


_TRACKED_PREFIXES = ("HUNTBOT2_", "ANTHROPIC_API_KEY", "OPENAI_API_KEY", "OPENROUTER_API_KEY")


@pytest.fixture(autouse=True)
def tmp_env(tmp_path):
    original_path = env_store._ENV_PATH
    env_store._ENV_PATH = tmp_path / ".env"
    # Snapshot and clear all vars that env_store may write to os.environ so
    # process-env from a prior test doesn't bleed in between tests.
    saved = {k: v for k, v in os.environ.items()
             if any(k.startswith(p) or k == p for p in _TRACKED_PREFIXES)}
    for k in saved:
        del os.environ[k]
    yield
    env_store._ENV_PATH = original_path
    for k in saved:
        os.environ[k] = saved[k]
    for k in list(os.environ):
        if any(k.startswith(p) or k == p for p in _TRACKED_PREFIXES) and k not in saved:
            del os.environ[k]


def test_get_settings_returns_defaults():
    r = client.get("/api/settings")
    assert r.status_code == 200
    d = r.json()
    assert d["llm_enabled"] is False
    assert d["theme"] == "dark"
    assert d["submit_policy"] == "stop_before_submit"
    assert d["min_score"] == 60
    assert d["api_keys"]["anthropic"]["set"] is False


def test_post_settings_saves_theme():
    r = client.post("/api/settings", json={"theme": "obsidian"})
    assert r.status_code == 200
    assert r.json()["ok"] is True
    assert client.get("/api/settings").json()["theme"] == "obsidian"


def test_post_settings_saves_llm_enabled():
    client.post("/api/settings", json={"llm_enabled": True})
    assert client.get("/api/settings").json()["llm_enabled"] is True


def test_post_settings_saves_discovery_urls():
    urls = ["https://linkedin.com/jobs/search?keywords=manager"]
    client.post("/api/settings", json={"discovery_urls": urls})
    assert client.get("/api/settings").json()["discovery_urls"] == urls


def test_post_settings_api_key_masked_in_get():
    client.post("/api/settings", json={"anthropic_api_key": "sk-ant-test123456"})
    d = client.get("/api/settings").json()
    assert d["api_keys"]["anthropic"]["set"] is True
    preview = d["api_keys"]["anthropic"]["preview"]
    assert preview.startswith("sk-ant-")
    assert "test123456" not in preview  # full key never returned


def test_post_settings_partial_update_leaves_others():
    client.post("/api/settings", json={"theme": "light", "min_score": 70})
    client.post("/api/settings", json={"theme": "obsidian"})
    d = client.get("/api/settings").json()
    assert d["theme"] == "obsidian"
    assert d["min_score"] == 70  # untouched


def test_get_settings_handles_corrupted_min_score(tmp_path):
    import huntbot2.config.env_store as _es
    _es._ENV_PATH = tmp_path / ".env"
    _es._ENV_PATH.write_text("HUNTBOT2_MIN_SCORE=not_a_number\n")
    r = client.get("/api/settings")
    assert r.status_code == 200
    assert r.json()["min_score"] == 60  # falls back to default


def test_test_key_invalid_provider():
    r = client.post("/api/settings/test-key", json={"provider": "unknown", "key": "x"})
    assert r.status_code == 200
    assert r.json()["ok"] is False


def test_test_key_bad_anthropic_key():
    r = client.post("/api/settings/test-key", json={"provider": "anthropic", "key": "sk-bad"})
    assert r.status_code == 200
    assert r.json()["ok"] is False


def test_master_resume_empty_by_default(tmp_path, monkeypatch):
    monkeypatch.setenv("HUNTBOT2_DATA_ROOT", str(tmp_path))
    r = client.get("/api/settings/master-resume")
    assert r.status_code == 200
    assert r.json()["content"] == ""


def test_master_resume_save_and_retrieve(tmp_path, monkeypatch):
    monkeypatch.setenv("HUNTBOT2_DATA_ROOT", str(tmp_path))
    content = "# My Resume\n\n## Experience\n- Built things."
    r = client.post("/api/settings/master-resume", json={"content": content})
    assert r.status_code == 200
    assert r.json()["ok"] is True
    r2 = client.get("/api/settings/master-resume")
    assert r2.json()["content"] == content
