import pytest
import huntbot2.config.env_store as env_store


@pytest.fixture(autouse=True)
def tmp_env(tmp_path):
    original = env_store._ENV_PATH
    env_store._ENV_PATH = tmp_path / ".env"
    yield
    env_store._ENV_PATH = original


def test_get_missing_returns_none():
    assert env_store.get("MISSING") is None


def test_get_missing_returns_default():
    assert env_store.get("MISSING", "fallback") == "fallback"


def test_set_and_get():
    env_store.set("FOO", "bar")
    assert env_store.get("FOO") == "bar"


def test_set_overwrites_existing():
    env_store.set("FOO", "bar")
    env_store.set("FOO", "baz")
    assert env_store.get("FOO") == "baz"
    # Only one FOO line in file
    lines = env_store._ENV_PATH.read_text().splitlines()
    assert sum(1 for l in lines if l.startswith("FOO=")) == 1


def test_delete_removes_key():
    env_store.set("FOO", "bar")
    env_store.delete("FOO")
    assert env_store.get("FOO") is None


def test_load_all_returns_all_pairs():
    env_store.set("K1", "v1")
    env_store.set("K2", "v2")
    result = env_store.load_all()
    assert result["K1"] == "v1"
    assert result["K2"] == "v2"


def test_value_with_equals_sign():
    # values like URLs contain = signs
    env_store.set("URL", "http://settings.example?x=1&y=2")
    assert env_store.get("URL") == "http://settings.example?x=1&y=2"


def test_comments_and_blanks_ignored():
    env_store._ENV_PATH.write_text("# comment\n\nFOO=bar\n")
    assert env_store.get("FOO") == "bar"
    result = env_store.load_all()
    assert "# comment" not in result
    assert "" not in result
