from huntbot2.tailoring.resume import generate_resume_variant


def test_generate_resume_variant_concise_contains_job_fields():
    out = generate_resume_variant(
        "# Master Resume\n\n- Did things",
        {"title": "Data Analyst", "company": "Acme", "location": "Remote"},
        style="concise",
    )

    assert "# Master Resume" in out
    assert "Role: Data Analyst" in out
    assert "Company: Acme" in out
    assert "Location: Remote" in out
    assert "Style: concise" in out
    assert "Prioritize high-impact bullets" in out
