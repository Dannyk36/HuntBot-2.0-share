from huntbot2.tailoring.cover import generate_cover_note


def test_generate_cover_note_is_deterministic():
    job = {"title": "Data Scientist", "company": "Beta", "location": "NYC"}
    roles = ["data scientist", "ml engineer"]

    out1 = generate_cover_note(job, roles)
    out2 = generate_cover_note(job, roles)

    assert out1 == out2
    assert "Data Scientist" in out1
    assert "Beta" in out1
    assert "NYC" in out1
    assert "data scientist, ml engineer" in out1
