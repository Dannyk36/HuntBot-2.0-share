import huntbot2.config.env_store as env_store
from huntbot2.llm.client import llm_health


def _set_env(tmp_path, **values):
    env_store._ENV_PATH = tmp_path / ".env"
    for key, val in values.items():
        env_store.set(key, val)


def test_llm_health_disabled(tmp_path):
    _set_env(tmp_path, HUNTBOT2_LLM_ENABLED="false")
    h = llm_health()
    assert h["enabled"] is False
    assert h["ready"] is False
    assert h["provider"] is None


def test_llm_health_enabled_but_no_key_is_not_ready(tmp_path):
    _set_env(tmp_path, HUNTBOT2_LLM_ENABLED="true", HUNTBOT2_LLM_PROVIDER="anthropic")
    h = llm_health()
    assert h["enabled"] is True
    assert h["key_present"] is False
    assert h["ready"] is False  # the silent-failure trap is now visible


def test_llm_health_enabled_with_key_is_ready(tmp_path):
    _set_env(
        tmp_path,
        HUNTBOT2_LLM_ENABLED="true",
        HUNTBOT2_LLM_PROVIDER="anthropic",
        ANTHROPIC_API_KEY="sk-test",
    )
    h = llm_health()
    assert h["enabled"] is True
    assert h["key_present"] is True
    assert h["ready"] is True
    assert h["provider"] == "anthropic"
