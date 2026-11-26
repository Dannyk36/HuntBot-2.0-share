from __future__ import annotations

from datetime import date
from pathlib import Path


def _load_template(project_dir: Path | None = None) -> str:
    if project_dir is not None:
        path = project_dir / "config" / "cover_letter_template.md"
        if path.exists():
            return path.read_text()
    # fallback: walk up from this file to find config/
    here = Path(__file__).resolve()
    for parent in here.parents:
        candidate = parent / "config" / "cover_letter_template.md"
        if candidate.exists():
            return candidate.read_text()
    return ""


def generate_cover_note(job: dict, profile_roles: list[str]) -> str:
    title = (job.get("title") or "this role").strip()
    company = (job.get("company") or "your team").strip()
    location = (job.get("location") or "your location").strip()
    roles_line = ", ".join(profile_roles) if profile_roles else "related roles"

    return (
        f"Hello {company} hiring team,\n\n"
        f"I'm excited to apply for the {title} position in {location}. "
        f"My background aligns with {roles_line}, and I focus on delivering measurable outcomes.\n\n"
        "I would welcome the opportunity to discuss how I can contribute quickly and effectively.\n\n"
        "Best,\n[Your name]"
    )


def generate_cover_note_llm(
    job: dict,
    profile_roles: list[str],
    llm_client,
    project_dir: Path | None = None,
) -> str:
    """Fill the cover letter template placeholders using the LLM."""
    title = (job.get("title") or "this role").strip()
    company = (job.get("company") or "your team").strip()
    location = (job.get("location") or "").strip()
    description = (job.get("description") or "").strip()

    template = _load_template(project_dir)

    # Load master resume for context
    resume_text = ""
    if project_dir is not None:
        rp = project_dir / "config" / "master_resume.md"
        if rp.exists():
            resume_text = rp.read_text()

    today = date.today().strftime("%B %d, %Y")

    system = (
        "You are an expert cover letter writer. You will receive a template with "
        "[[PLACEHOLDER]] markers and must replace every placeholder with specific, "
        "genuine content tailored to the job and the candidate's background.\n"
        "Rules:\n"
        "- Replace ALL [[...]] placeholders — leave none in the output.\n"
        "- Reference the role's actual responsibilities and connect them to the "
        "candidate's real, resume-backed achievements.\n"
        "- Keep the exact letter structure and formatting.\n"
        "- Be specific and concrete; no fluff, filler, or fabricated claims.\n"
        "- Keep it to 3–4 short paragraphs.\n"
        "- Return only the completed letter text, nothing else."
    )

    parts = [
        "Fill in this cover letter template for the candidate applying to:",
        f"Role: {title}\nCompany: {company}\nLocation: {location}",
    ]
    if description:
        parts.append(f"Job description:\n{description[:2500]}")
    parts.append(f"Candidate's resume:\n{resume_text}")
    parts.append(f"Today's date: {today}")
    parts.append(f"Template to fill in:\n\n{template}")
    parts.append(
        "Replace every [[PLACEHOLDER]] with real content. Do not invent a specific "
        f"street address — if one is needed use the job location ({location or 'the job location'}). "
        "Return only the completed letter."
    )
    return llm_client.ask("\n\n".join(parts), system=system, max_tokens=900)
