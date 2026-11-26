import json
import pytest
from unittest.mock import MagicMock, patch
from huntbot2.discovery.models import JobRecord
from huntbot2.scoring.engine import score_job, score_job_llm
from huntbot2.llm.client import LLMClient


class FakeConfig:
    class profile:
        roles = ["Operations Manager"]
        locations = ["Phoenix"]
        remote_only = False
    class filters:
        exclude_keywords = ["intern"]


def _make_job(title="Operations Manager", company="Acme", location="Phoenix AZ") -> JobRecord:
    return JobRecord(source="test", title=title, company=company, location=location, url="https://example.com")


def _mock_llm(response_text: str) -> LLMClient:
    client = MagicMock(spec=LLMClient)
    client.ask.return_value = response_text
    return client


def test_score_job_llm_parses_json():
    llm = _mock_llm('{"score": 85, "reasons": ["role match"], "reject_reason": null}')
    result = score_job_llm(_make_job(), FakeConfig, llm)
    assert result["score"] == 85
    assert "role match" in result["reasons"]
    assert result["reject_reason"] is None


def test_score_job_llm_handles_markdown_fence():
    llm = _mock_llm('```json\n{"score": 70, "reasons": [], "reject_reason": null}\n```')
    result = score_job_llm(_make_job(), FakeConfig, llm)
    assert result["score"] == 70


def test_score_job_llm_falls_back_on_bad_json():
    llm = _mock_llm("not valid json at all")
    result = score_job_llm(_make_job(), FakeConfig, llm)
    assert isinstance(result["score"], int)
    assert "reject_reason" in result


def test_score_job_llm_falls_back_on_llm_error():
    from huntbot2.llm.client import LLMError
    llm = MagicMock(spec=LLMClient)
    llm.ask.side_effect = LLMError("quota exceeded")
    result = score_job_llm(_make_job(), FakeConfig, llm)
    assert isinstance(result["score"], int)


from huntbot2.tailoring.resume import generate_resume_variant_llm
from huntbot2.tailoring.cover import generate_cover_note_llm


def test_generate_resume_variant_llm_returns_string():
    llm = _mock_llm("# Tailored Resume\n\n## Experience\n- Managed ops team.")
    result = generate_resume_variant_llm(
        "# Resume\n\n## Experience\n- Did things.",
        {"title": "Ops Manager", "company": "Acme", "location": "Phoenix"},
        llm,
    )
    assert "Tailored Resume" in result or "Resume" in result
    assert isinstance(result, str)
    assert len(result) > 10


def test_generate_resume_variant_llm_calls_ask_once():
    llm = _mock_llm("# Resume content")
    generate_resume_variant_llm("# Base", {"title": "X"}, llm)
    assert llm.ask.call_count == 1


def test_generate_cover_note_llm_returns_string():
    llm = _mock_llm("Dear Acme hiring team, I am excited about this role...")
    result = generate_cover_note_llm(
        {"title": "Ops Manager", "company": "Acme", "location": "Phoenix"},
        ["Operations Manager", "Project Manager"],
        llm,
    )
    assert isinstance(result, str)
    assert len(result) > 10


def test_generate_cover_note_llm_calls_ask_once():
    llm = _mock_llm("Cover letter content")
    generate_cover_note_llm({"title": "X"}, [], llm)
    assert llm.ask.call_count == 1


from huntbot2.apply.form_filler import fill_form_with_llm


def _make_mock_page(fields: list[dict]):
    page = MagicMock()
    page.evaluate.return_value = fields
    return page


def test_fill_form_no_fields_returns_zero():
    llm = _mock_llm("{}")
    page = _make_mock_page([])
    result = fill_form_with_llm(page, {"title": "Ops"}, {"name": "Test"}, llm)
    assert result["fields_filled"] == 0
    assert result["fields_skipped"] == 0


def test_fill_form_fills_text_field():
    llm = _mock_llm('{"Full Name": "John Doe"}')
    field = {"label": "Full Name", "type": "text", "name": "full_name", "id": "fn", "options": None}
    page = _make_mock_page([field])
    result = fill_form_with_llm(page, {"title": "Ops"}, {"name": "John Doe"}, llm)
    page.fill.assert_called_once_with('[name="full_name"]', "John Doe")
    assert result["fields_filled"] == 1
    assert result["answers"]["Full Name"] == "John Doe"


def test_fill_form_falls_back_on_bad_llm_json():
    llm = _mock_llm("not json")
    page = _make_mock_page([{"label": "X", "type": "text", "name": "x", "id": "x", "options": None}])
    result = fill_form_with_llm(page, {}, {}, llm)
    assert result["fields_filled"] == 0
    assert result["fields_skipped"] == 1


import os
from pathlib import Path
from huntbot2.automation.pipeline import _stage_score, _stage_tailor, run_pipeline_now
from huntbot2.db.schema import init_db as _init_db


@pytest.fixture
def project_dir(tmp_path):
    (tmp_path / "data").mkdir()
    (tmp_path / "config").mkdir()
    (tmp_path / "fixtures").mkdir()
    (tmp_path / "artifacts").mkdir()
    _init_db(tmp_path / "data" / "huntbot2.db")
    (tmp_path / "config" / "profile.yml").write_text(
        "profile:\n  roles: [Operations Manager]\n  locations: [Phoenix]\n  remote_only: false\n"
        "filters:\n  exclude_keywords: [intern]\n"
        "apply:\n  target_jobs_per_run: 5\n"
    )
    (tmp_path / "fixtures" / "sample_linkedin_search.html").write_text("<html></html>")
    return tmp_path


def test_stage_score_uses_llm_when_client_provided(project_dir):
    import sqlite3
    from datetime import datetime, UTC
    conn = sqlite3.connect(project_dir / "data" / "huntbot2.db")
    conn.execute(
        "INSERT INTO jobs (source, title, company, location, url, created_at, dedup_key) VALUES (?,?,?,?,?,?,?)",
        ("test", "Ops Manager", "Acme", "Phoenix", "https://acme.example/job/1",
         datetime.now(UTC).isoformat(), "acme-ops-1"),
    )
    conn.commit()
    conn.close()

    llm = _mock_llm('{"score": 90, "reasons": ["llm matched"], "reject_reason": null}')
    _stage_score(project_dir, "headless", False, llm_client=llm)
    llm.ask.assert_called()


def test_stage_score_uses_rule_based_without_llm(project_dir):
    import sqlite3
    from datetime import datetime, UTC
    conn = sqlite3.connect(project_dir / "data" / "huntbot2.db")
    conn.execute(
        "INSERT INTO jobs (source, title, company, location, url, created_at, dedup_key) VALUES (?,?,?,?,?,?,?)",
        ("test", "Ops Manager", "Acme", "Phoenix", "https://acme.example/job/2",
         datetime.now(UTC).isoformat(), "acme-ops-2"),
    )
    conn.commit()
    conn.close()

    result = _stage_score(project_dir, "headless", False)
    assert isinstance(result, dict)


def test_run_pipeline_now_passes_use_llm(project_dir, monkeypatch):
    import huntbot2.config.env_store as env_store
    env_store._ENV_PATH = project_dir / ".env"
    result = run_pipeline_now(str(project_dir), mode="headless", dry_run=True, use_llm=False)
    assert "status" in result
