import json
import pytest
from unittest.mock import patch, MagicMock
from huntbot2.llm.client import (
    LLMClient, LLMError, build_client, client_from_env,
)
import huntbot2.config.env_store as env_store


@pytest.fixture(autouse=True)
def tmp_env(tmp_path):
    original = env_store._ENV_PATH
    env_store._ENV_PATH = tmp_path / ".env"
    yield
    env_store._ENV_PATH = original


def _mock_anthropic_response(text: str) -> MagicMock:
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {
        "content": [{"type": "text", "text": text}],
        "model": "claude-haiku-4-5-20251001",
    }
    resp.raise_for_status = MagicMock()
    return resp


def _mock_openai_response(text: str) -> MagicMock:
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {
        "choices": [{"message": {"content": text}}],
    }
    resp.raise_for_status = MagicMock()
    return resp


def test_anthropic_ask():
    client = build_client("anthropic", "claude-haiku-4-5-20251001", "sk-ant-test")
    with patch("httpx.post", return_value=_mock_anthropic_response("hello")) as mock_post:
        result = client.ask("say hello")
    assert result == "hello"
    call_kwargs = mock_post.call_args
    assert "api.anthropic.com" in mock_post.call_args.args[0]
    headers = mock_post.call_args.kwargs["headers"]
    assert headers["x-api-key"] == "sk-ant-test"
    assert headers["anthropic-version"] == "2023-06-01"


def test_openai_ask():
    client = build_client("openai", "gpt-4o-mini", "sk-test")
    with patch("httpx.post", return_value=_mock_openai_response("hi there")):
        result = client.ask("say hi")
    assert result == "hi there"


def test_openrouter_ask():
    client = build_client("openrouter", "openai/gpt-4o-mini", "sk-or-test")
    with patch("httpx.post", return_value=_mock_openai_response("hi")) as mock_post:
        result = client.ask("say hi")
    assert result == "hi"
    assert "openrouter.ai" in mock_post.call_args[0][0]


def test_ask_passes_system_prompt():
    client = build_client("anthropic", "claude-haiku-4-5-20251001", "sk-ant-test")
    with patch("httpx.post", return_value=_mock_anthropic_response("ok")) as mock_post:
        client.ask("question", system="You are a helper")
    body = mock_post.call_args[1]["json"]
    assert body.get("system") == "You are a helper"


def test_http_error_raises_llm_error():
    client = build_client("anthropic", "claude-haiku-4-5-20251001", "sk-bad")
    mock_resp = MagicMock()
    mock_resp.is_success = False
    mock_resp.status_code = 401
    mock_resp.json.return_value = {"error": {"message": "Invalid API key"}}
    mock_resp.text = "Unauthorized"
    with patch("httpx.post", return_value=mock_resp):
        with pytest.raises(LLMError, match="401"):
            client.ask("test")


def test_client_from_env_returns_none_when_disabled():
    env_store.set("HUNTBOT2_LLM_ENABLED", "false")
    assert client_from_env() is None


def test_client_from_env_returns_none_when_key_missing():
    env_store.set("HUNTBOT2_LLM_ENABLED", "true")
    env_store.set("HUNTBOT2_LLM_PROVIDER", "anthropic")
    assert client_from_env() is None


def test_client_from_env_returns_client_when_configured():
    env_store.set("HUNTBOT2_LLM_ENABLED", "true")
    env_store.set("HUNTBOT2_LLM_PROVIDER", "anthropic")
    env_store.set("ANTHROPIC_API_KEY", "sk-ant-abc")
    env_store.set("HUNTBOT2_SCORING_MODEL", "claude-haiku-4-5-20251001")
    client = client_from_env()
    assert client is not None
    assert client.provider == "anthropic"
    assert client.model == "claude-haiku-4-5-20251001"
    assert client.api_key == "sk-ant-abc"


def test_ask_records_call_in_calls_list():
    client = build_client("anthropic", "claude-haiku-4-5-20251001", "sk-ant-test")
    with patch("httpx.post", return_value=_mock_anthropic_response("pong")):
        client.ask("ping")
    assert len(client.calls) == 1
    record = client.calls[0]
    assert record["provider"] == "anthropic"
    assert record["model"] == "claude-haiku-4-5-20251001"
    assert record["prompt_chars"] == len("ping")
    assert record["response_chars"] == len("pong")
    assert record["duration_ms"] >= 0
    assert record["error"] is None


def test_ask_records_error_in_calls_list():
    client = build_client("anthropic", "claude-haiku-4-5-20251001", "sk-bad")
    mock_resp = MagicMock()
    mock_resp.is_success = False
    mock_resp.status_code = 401
    mock_resp.json.return_value = {"error": {"message": "Invalid API key"}}
    mock_resp.text = "Unauthorized"
    with patch("httpx.post", return_value=mock_resp):
        with pytest.raises(LLMError):
            client.ask("test")
    assert len(client.calls) == 1
    assert client.calls[0]["error"] is not None


def test_write_call_log_creates_jsonl(tmp_path):
    client = build_client("anthropic", "claude-haiku-4-5-20251001", "sk-ant-test")
    with patch("httpx.post", return_value=_mock_anthropic_response("result")):
        client.ask("first")
    with patch("httpx.post", return_value=_mock_anthropic_response("result2")):
        client.ask("second")
    log_path = tmp_path / "artifacts" / "llm-calls.jsonl"
    client.write_call_log(log_path)
    lines = log_path.read_text().strip().splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["prompt_chars"] == len("first")
    assert json.loads(lines[1])["prompt_chars"] == len("second")
