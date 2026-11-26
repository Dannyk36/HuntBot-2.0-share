"""Safety-gate hardening: configurable submit token, checkpoint-gated
auto-submit, and secure-by-default cockpit auth."""
from pathlib import Path
from types import SimpleNamespace

import pytest

from huntbot2.apply.runner import expected_submit_token, run_apply_demo
from huntbot2.db.schema import init_db


_AUTH_ENV_KEYS = (
    "HUNTBOT2_COCKPIT_AUTH_TOKEN",
    "HUNTBOT2_COCKPIT_AUTH_USER",
    "HUNTBOT2_COCKPIT_AUTH_PASSWORD",
    "HUNTBOT2_COCKPIT_AUTH_BYPASS",
)


# ── Configurable submit token ─────────────────────────────────────────────────

def test_expected_submit_token_defaults_to_legacy(monkeypatch):
    monkeypatch.delenv("HUNTBOT2_SUBMIT_TOKEN", raising=False)
    assert expected_submit_token() == "I_UNDERSTAND_SUBMIT"


def test_expected_submit_token_env_override(monkeypatch):
    monkeypatch.setenv("HUNTBOT2_SUBMIT_TOKEN", "op-secret-123")
    assert expected_submit_token() == "op-secret-123"


def test_apply_gate_honors_custom_token(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("HUNTBOT2_SUBMIT_TOKEN", "op-secret-123")

    # The legacy literal no longer authorizes once a custom token is set.
    blocked = run_apply_demo(
        project_dir=tmp_path, mode="headed", dry_run=False,
        submit_policy="allow_submit_with_token", submit_token="I_UNDERSTAND_SUBMIT",
    )
    assert blocked.gate_decision == "policy_blocked"

    allowed = run_apply_demo(
        project_dir=tmp_path, mode="headed", dry_run=False,
        submit_policy="allow_submit_with_token", submit_token="op-secret-123",
    )
    assert allowed.gate_decision == "would_submit"


# ── Checkpoint-gated auto-submit (cockpit pipeline thread) ────────────────────

def _seed_db(tmp_path: Path) -> Path:
    db = tmp_path / "data" / "huntbot2.db"
    init_db(db)
    return db


def test_resolve_submit_token_never_submit_returns_none(tmp_path: Path):
    from huntbot2.web.app import _resolve_apply_submit_token
    _seed_db(tmp_path)
    assert _resolve_apply_submit_token("never_submit", tmp_path) is None


def test_resolve_submit_token_blocked_without_checkpoint(tmp_path: Path):
    from huntbot2.web.app import _resolve_apply_submit_token
    _seed_db(tmp_path)
    # auto_submit configured but no human checkpoint approved → no token.
    assert _resolve_apply_submit_token("auto_submit", tmp_path) is None


def test_resolve_submit_token_allowed_with_valid_checkpoint(tmp_path: Path, monkeypatch):
    from huntbot2.web.app import _resolve_apply_submit_token
    from huntbot2.web.repository import CockpitRepository
    monkeypatch.delenv("HUNTBOT2_SUBMIT_TOKEN", raising=False)
    db = _seed_db(tmp_path)
    CockpitRepository(db).record_policy_event("checkpoint_approved", meta={"actor": "human"})
    assert _resolve_apply_submit_token("auto_submit", tmp_path) == "I_UNDERSTAND_SUBMIT"


# ── Secure-by-default cockpit auth ────────────────────────────────────────────

def _fake_request(host: str | None):
    client = SimpleNamespace(host=host) if host is not None else None
    return SimpleNamespace(client=client, headers={})


def test_unconfigured_cockpit_denies_remote(monkeypatch):
    import huntbot2.web.app as web_app
    for key in _AUTH_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    assert web_app._verify_auth(_fake_request("203.0.113.7")) is False


def test_unconfigured_cockpit_allows_loopback(monkeypatch):
    import huntbot2.web.app as web_app
    for key in _AUTH_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    assert web_app._verify_auth(_fake_request("127.0.0.1")) is True
    assert web_app._verify_auth(_fake_request("testclient")) is True


def test_bypass_flag_still_allows_remote(monkeypatch):
    import huntbot2.web.app as web_app
    for key in _AUTH_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("HUNTBOT2_COCKPIT_AUTH_BYPASS", "1")
    assert web_app._verify_auth(_fake_request("203.0.113.7")) is True
