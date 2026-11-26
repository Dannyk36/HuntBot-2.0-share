from pathlib import Path

from huntbot2.scoring.export import export_shortlist_markdown


def test_export_shortlist_markdown(tmp_path: Path):
    out = export_shortlist_markdown(
        project_dir=tmp_path,
        rows=[
            {
                "score": 100,
                "title": "Data Analyst",
                "company": "Acme",
                "location": "Remote",
                "url": "https://example.com/job/1",
                "reasons": ["role keyword match", "location match"],
            }
        ],
    )

    assert out.exists()
    assert out.name.startswith("shortlist-")
    text = out.read_text()
    assert "| Score | Title | Company |" in text
    assert "Data Analyst" in text
