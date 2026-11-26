from datetime import datetime
from pathlib import Path


def export_shortlist_markdown(project_dir: str | Path, rows: list[dict]) -> Path:
    root = Path(project_dir)
    artifacts_dir = root / "artifacts"
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    out = artifacts_dir / f"shortlist-{datetime.now().strftime('%Y%m%d-%H%M')}.md"

    lines = [
        "| Score | Title | Company | Location | URL | Reasons |",
        "|---:|---|---|---|---|---|",
    ]
    for row in rows:
        reasons = ", ".join(row.get("reasons", []))
        lines.append(
            f"| {row['score']} | {row['title']} | {row['company']} | {row['location']} | {row['url']} | {reasons} |"
        )

    out.write_text("\n".join(lines) + "\n")
    return out
