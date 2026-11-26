from pathlib import Path

from huntbot2.apply.linkedin_adapter import (
    analyze_linkedin_page,
    detect_apply_path,
    detect_login_state,
)


FIXTURES_DIR = Path(__file__).resolve().parents[1] / "fixtures"


def _load_fixture(name: str) -> str:
    return (FIXTURES_DIR / name).read_text(encoding="utf-8")


def test_detect_logged_out():
    html = _load_fixture("linkedin_logged_out.html")
    assert detect_login_state(html) == "logged_out"
    assert detect_apply_path(html) == "unknown"


def test_detect_logged_in_easy_apply():
    html = _load_fixture("linkedin_logged_in_easy_apply.html")
    assert detect_login_state(html) == "logged_in"
    assert detect_apply_path(html) == "easy_apply"


def test_detect_logged_in_external_apply():
    html = _load_fixture("linkedin_logged_in_external_apply.html")
    assert detect_login_state(html) == "logged_in"
    assert detect_apply_path(html) == "external_apply"


def test_detect_checkpoint_from_page_and_url():
    html = _load_fixture("linkedin_checkpoint.html")
    assert detect_login_state(html) == "checkpoint"
    assert detect_login_state("<html></html>", final_url="https://www.linkedin.com/checkpoint/challenge/") == "checkpoint"


def test_analyze_linkedin_page_combines_signals():
    html = _load_fixture("linkedin_logged_in_external_apply.html")
    result = analyze_linkedin_page(html, final_url="https://company.example/jobs/123")

    assert result["login_state"] == "logged_in"
    assert result["apply_path"] == "external_apply"
    assert "company_website_cta" in result["signals"]
    assert "url_offsite" in result["signals"]
