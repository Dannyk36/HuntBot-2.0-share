def generate_resume_variant(master_resume_md: str, job: dict, style: str = "concise") -> str:
    title = (job.get("title") or "Unknown Role").strip()
    company = (job.get("company") or "Unknown Company").strip()
    location = (job.get("location") or "Unknown Location").strip()

    profile_section = (
        "## Target Role\n"
        f"- Role: {title}\n"
        f"- Company: {company}\n"
        f"- Location: {location}\n"
        f"- Style: {style}\n"
    )

    if style == "concise":
        guidance = "## Focus\n- Prioritize high-impact bullets relevant to this role.\n"
    else:
        guidance = "## Focus\n- Provide full detail while preserving relevance to the target role.\n"

    return f"{master_resume_md.rstrip()}\n\n{profile_section}\n{guidance}"


def generate_resume_variant_llm(master_resume: str, job: dict, llm_client) -> str:
    """Rewrite master resume to match the target role using LLM. Returns tailored
    markdown. Uses the full job description (when available) so the resume mirrors
    the posting's actual requirements, not just its title."""
    title = (job.get("title") or "Unknown Role").strip()
    company = (job.get("company") or "Unknown Company").strip()
    location = (job.get("location") or "").strip()
    description = (job.get("description") or "").strip()
    keywords = job.get("score_keywords") or []

    system = (
        "You are an expert resume writer and ATS optimization specialist. Rewrite "
        "the candidate's resume to target the specific job below.\n"
        "Rules:\n"
        "- Lead with the experience and skills most relevant to this role.\n"
        "- Mirror the posting's own terminology and required skills WHERE the "
        "candidate genuinely has them.\n"
        "- Keep every quantified achievement and metric from the master resume.\n"
        "- Never invent experience, employers, titles, dates, or skills the "
        "candidate does not have.\n"
        "- Keep it concise (about one page) and ATS-friendly: standard section "
        "headings, no tables or multi-column layouts.\n"
        "- Preserve the markdown structure. Return only the rewritten resume."
    )
    parts = [
        f"## Target role\nTitle: {title}\nCompany: {company}\nLocation: {location}",
    ]
    if keywords:
        parts.append("Skills to emphasize (only if genuine): " + ", ".join(str(k) for k in keywords))
    if description:
        parts.append(f"## Job description\n{description[:3000]}")
    parts.append(f"## Candidate master resume\n{master_resume}")
    return llm_client.ask("\n\n".join(parts), system=system, max_tokens=2048)
