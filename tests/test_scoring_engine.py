from types import SimpleNamespace

from huntbot2.discovery.models import JobRecord
from huntbot2.scoring.engine import score_job, score_job_llm


class _FakeLLM:
    def __init__(self, text: str) -> None:
        self._text = text

    def ask(self, *args, **kwargs) -> str:
        return self._text


def _config():
    return SimpleNamespace(
        profile=SimpleNamespace(roles=["data analyst"], locations=["remote", "phoenix, az"]),
        filters=SimpleNamespace(exclude_keywords=["senior", "principal"]),
    )


def _job(title="Data Analyst", location="Remote", company="Acme", raw_snapshot=None):
    return JobRecord(
        source="linkedin",
        title=title,
        company=company,
        location=location,
        url="https://example.com/job/1",
        raw_snapshot=raw_snapshot,
    )


def test_score_job_matches_role_and_remote():
    # Role (55) + generic remote (25) — strong but not a perfect 100.
    result = score_job(_job(title="Data Analyst", location="Remote"), _config())
    assert result["reject_reason"] is None
    assert result["score"] == 80
    assert "role keyword match" in result["reasons"]


def test_score_job_rejects_excluded_keyword():
    result = score_job(_job(title="Senior Data Analyst"), _config())
    assert result["reject_reason"] == "excluded keyword"
    assert result["score"] == 0


def test_score_job_spreads_across_match_strength():
    # role only < role + remote < role + explicit location < + description echo
    role_only = score_job(_job(location="Boston, MA"), _config())["score"]
    role_remote = score_job(_job(location="Remote"), _config())["score"]
    role_location = score_job(_job(location="Phoenix, AZ"), _config())["score"]
    role_loc_desc = score_job(
        _job(location="Phoenix, AZ", raw_snapshot="We need a data analyst with SQL."),
        _config(),
    )["score"]
    assert role_only < role_remote < role_location < role_loc_desc
    assert role_loc_desc <= 100


def test_score_job_caps_non_role_matches_below_high_tier():
    # No role match — even with a location hit it cannot score as a strong lead.
    result = score_job(_job(title="Warehouse Associate", location="Phoenix, AZ"), _config())
    assert "role keyword match" not in result["reasons"]
    assert result["score"] <= 40


def test_score_job_never_exceeds_100():
    result = score_job(
        _job(location="Phoenix, AZ", raw_snapshot="data analyst data analyst data analyst"),
        _config(),
    )
    assert 0 <= result["score"] <= 100


def test_score_job_llm_clamps_out_of_range_scores():
    job = _job()
    cfg = _config()
    high = score_job_llm(job, cfg, _FakeLLM('{"score": 150, "reasons": [], "reject_reason": null}'))
    assert high["score"] == 100
    low = score_job_llm(job, cfg, _FakeLLM('{"score": -20, "reasons": [], "reject_reason": null}'))
    assert low["score"] == 0


def test_score_job_llm_parses_fenced_json():
    job = _job()
    cfg = _config()
    fenced = '```json\n{"score": 73, "reasons": ["fit"], "reject_reason": null}\n```'
    result = score_job_llm(job, cfg, _FakeLLM(fenced))
    assert result["score"] == 73
