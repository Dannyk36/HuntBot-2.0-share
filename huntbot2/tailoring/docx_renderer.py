"""Edit Word document templates and convert to PDF via LibreOffice.

The .docx files are the source of truth for formatting. Only placeholder
text ([[...]]) is replaced in the cover letter; only the Professional Summary
paragraph is updated in the resume. LibreOffice converts the edited copy to
PDF, preserving every font, margin, and layout element from the originals.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from datetime import date
from pathlib import Path


# ── Public API ─────────────────────────────────────────────────────────────

def fill_cover_to_pdf(
    job: dict,
    template_path: Path,
    output_path: Path,
    *,
    llm_client=None,
    project_dir: Path | None = None,
) -> Path:
    """Fill [[PLACEHOLDER]] markers in the cover letter docx and convert to PDF."""
    from docx import Document

    replacements = _build_cover_replacements(job, template_path, llm_client, project_dir)

    tmp_docx = output_path.with_suffix(".docx")
    shutil.copy2(template_path, tmp_docx)
    doc = Document(tmp_docx)

    for para in doc.paragraphs:
        _replace_in_para(para, replacements)
    for table in doc.tables:
        for row in table.rows:
            for cell in row.cells:
                for para in cell.paragraphs:
                    _replace_in_para(para, replacements)

    doc.save(tmp_docx)
    _docx_to_pdf(tmp_docx, output_path)
    tmp_docx.unlink(missing_ok=True)
    return output_path


def tailor_resume_to_pdf(
    job: dict,
    template_path: Path,
    output_path: Path,
    *,
    llm_client=None,
    master_resume_md: str = "",
) -> Path:
    """Tailor the Professional Summary in the resume docx and convert to PDF."""
    from docx import Document

    tmp_docx = output_path.with_suffix(".docx")
    shutil.copy2(template_path, tmp_docx)
    doc = Document(tmp_docx)

    if llm_client and master_resume_md:
        new_summary = _generate_summary(job, master_resume_md, llm_client)
        _replace_section_body(doc, "PROFESSIONAL SUMMARY", new_summary)

    doc.save(tmp_docx)
    _docx_to_pdf(tmp_docx, output_path)
    tmp_docx.unlink(missing_ok=True)
    return output_path


# ── Cover letter helpers ───────────────────────────────────────────────────

def _build_cover_replacements(
    job: dict,
    template_path: Path,
    llm_client,
    project_dir: Path | None,
) -> dict[str, str]:
    from docx import Document

    # Extract every unique [[...]] placeholder from the template
    doc = Document(template_path)
    full_text = "\n".join(p.text for p in doc.paragraphs)
    placeholders = list(dict.fromkeys(re.findall(r"\[\[[^\]]+\]\]", full_text)))

    # Values we can fill from job data alone
    city_state = job.get("location", "")
    known: dict[str, str] = {
        "[[Today’s Date]]": date.today().strftime("%B %d, %Y"),   # curly apostrophe
        "[[Today’s Date]]":      date.today().strftime("%B %d, %Y"),   # straight apostrophe
        "[[COMPANY NAME]]":      job.get("company", ""),
        "[[ROLE TITLE]]":        job.get("title", ""),
        # Street address placeholder is intentionally left blank — we only have city/state
        "[[STREhET ADDRESS]]":   "",  # typo preserved from original doc
        "[[STREET ADDRESS]]":    "",
        "[[CITY, STATE ZIP]]":   city_state,
        "[[CITY, STATE]]":       city_state,
    }

    resume_text = ""
    if project_dir is not None:
        rp = project_dir / "config" / "master_resume.md"
        if rp.exists():
            resume_text = rp.read_text()

    content_placeholders = [p for p in placeholders if p not in known]

    if llm_client and content_placeholders:
        description = job.get("description", "")
        keywords = job.get("score_keywords", [])
        system = (
            "You are an expert cover letter writer for the candidate described in the supplied resume. "
            "Return ONLY a valid JSON object. No markdown fences. No explanation. "
            "Every value must be specific and draw directly from the resume and job description — "
            "no generic filler text, no instructions, no examples in brackets."
        )
        job_context = (
            f"ROLE: {job.get('title', '')}\n"
            f"COMPANY: {job.get('company', '')}\n"
            f"LOCATION: {job.get('location', '')}\n"
        )
        if keywords:
            job_context += f"SCORING KEYWORDS MATCHED: {', '.join(str(k) for k in keywords)}\n"
        if description:
            job_context += f"\nFULL JOB DESCRIPTION:\n{description[:3000]}\n"
        prompt = (
            f"Fill each cover letter placeholder for this application.\n\n"
            f"{job_context}\n"
            f"CANDIDATE RESUME:\n{resume_text}\n\n"
            f"Rules:\n"
            f"- Mirror the job's own keywords and phrases where genuine\n"
            f"- Draw specific achievements/metrics from the resume\n"
            f"- Write as the candidate in first person, 1-3 sentences per placeholder\n"
            f"- Return ONLY the JSON object below with all values filled\n\n"
            + json.dumps({p: "" for p in content_placeholders}, indent=2)
        )
        from huntbot2.llm.parsing import extract_json_text
        raw = llm_client.ask(prompt, system=system, max_tokens=2500)
        text = extract_json_text(raw)
        try:
            known.update(json.loads(text))
        except json.JSONDecodeError:
            # Truncated response — fill what we can, leave the rest blank
            for ph in content_placeholders:
                known.setdefault(ph, "")
    elif content_placeholders:
        # LLM disabled — fill with empty string so the letter is clean rather than
        # littered with raw [[...]] markers. User must enable LLM for real content.
        for ph in content_placeholders:
            known[ph] = ""

    return {ph: known.get(ph, "") for ph in placeholders}


# ── Resume helpers ─────────────────────────────────────────────────────────

def _generate_summary(job: dict, master_resume_md: str, llm_client) -> str:
    system = (
        "You are an expert resume writer for the candidate described in the supplied resume. "
        "Write a 3–4 sentence Professional Summary tailored to the target role. "
        "Mirror the job's own language and keywords. Draw specific facts and metrics "
        "from the resume. Return only the summary text — no heading, no markdown."
    )
    description = job.get("description", "")
    keywords = job.get("score_keywords", [])
    job_context = (
        f"Target role: {job.get('title', '')} at {job.get('company', '')} "
        f"in {job.get('location', '')}\n"
    )
    if keywords:
        job_context += f"Key matched keywords: {', '.join(str(k) for k in keywords)}\n"
    if description:
        job_context += f"\nJob description (first 2000 chars):\n{description[:2000]}\n"
    prompt = f"{job_context}\nResume:\n{master_resume_md}"
    return llm_client.ask(prompt, system=system, max_tokens=400).strip()


def _replace_section_body(doc, section_heading: str, new_text: str) -> None:
    """Replace the first content paragraph that follows a section heading."""
    found_heading = False
    for para in doc.paragraphs:
        if found_heading and para.text.strip():
            _set_para_text(para, new_text)
            return
        if para.text.strip().upper() == section_heading.upper():
            found_heading = True


def _set_para_text(para, new_text: str) -> None:
    """Overwrite a paragraph's text, keeping paragraph-level formatting intact."""
    if para.runs:
        para.runs[0].text = new_text
        for run in para.runs[1:]:
            run.text = ""


# ── Shared helpers ─────────────────────────────────────────────────────────

def _replace_in_para(para, replacements: dict[str, str]) -> None:
    """Replace [[PLACEHOLDER]] text in a paragraph.

    Handles two cases:
    - Placeholder is fully contained in a single run → simple replacement,
      run-level formatting (bold, italic, font) is preserved.
    - Placeholder is split across multiple runs (as Word sometimes fragments
      text) → consolidate all runs into the first run's text.
    """
    if not any(ph in para.text for ph in replacements):
        return

    # Pass 1: replace within individual runs (preserves per-run formatting)
    found: set[str] = set()
    for run in para.runs:
        for ph, val in replacements.items():
            if ph in run.text:
                run.text = run.text.replace(ph, val)
                found.add(ph)

    # Pass 2: handle placeholders still split across runs
    remaining = {ph: val for ph, val in replacements.items()
                 if ph not in found and ph in para.text}
    if remaining and para.runs:
        rebuilt = para.text
        for ph, val in remaining.items():
            rebuilt = rebuilt.replace(ph, val)
        para.runs[0].text = rebuilt
        for run in para.runs[1:]:
            run.text = ""


def _docx_to_pdf(docx_path: Path, pdf_path: Path) -> None:
    """Convert docx → PDF using LibreOffice, preserving all formatting."""
    import os, tempfile
    out_dir = pdf_path.parent
    out_dir.mkdir(parents=True, exist_ok=True)

    # Use an isolated per-call user profile so concurrent/repeated calls
    # don't contend on the same lock file (which causes LibreOffice to hang).
    with tempfile.TemporaryDirectory(prefix="soffice-") as tmp_profile:
        env = {**os.environ, "HOME": tmp_profile}
        subprocess.run(
            [
                "libreoffice", "--headless", "--nolockcheck",
                "-env:UserInstallation=file://" + tmp_profile,
                "--convert-to", "pdf",
                "--outdir", str(out_dir),
                str(docx_path),
            ],
            check=True,
            capture_output=True,
            timeout=120,
            env=env,
        )

    # LibreOffice names the output after the input stem
    generated = out_dir / (docx_path.stem + ".pdf")
    if generated.exists() and generated != pdf_path:
        generated.rename(pdf_path)
