"""Load and flatten candidate profile for form filling."""
from __future__ import annotations

from pathlib import Path


def load_candidate_profile(project_dir: Path) -> dict:
    """Build a flat dict from config/candidate_profile.yml for use in form filling."""
    import yaml

    path = Path(project_dir) / "config" / "candidate_profile.yml"
    if not path.exists():
        return {}

    try:
        raw = yaml.safe_load(path.read_text())
    except Exception:
        return {}

    if not isinstance(raw, dict):
        return {}

    personal = raw.get("personal", {}) or {}
    contact = raw.get("contact", {}) or {}
    address = raw.get("address", {}) or {}
    education = raw.get("education", {}) or {}
    compensation = raw.get("compensation", {}) or {}
    work_auth = raw.get("work_authorization", {}) or {}
    skills = raw.get("skills", {}) or {}
    history = raw.get("employment_history", []) or []

    phone_raw = contact.get("phone_primary", {})
    phone = phone_raw.get("number", "") if isinstance(phone_raw, dict) else str(phone_raw)

    profile: dict = {
        "first_name": personal.get("first_name", ""),
        "last_name": personal.get("last_name", ""),
        "full_name": personal.get("full_name", ""),
        "email": contact.get("email", ""),
        "phone": phone,
        "address": address.get("street", ""),
        "city": address.get("city", ""),
        "state": address.get("state_or_province", ""),
        "zip": str(address.get("postal_code", "")),
        "country": address.get("country", "United States"),
        "school": education.get("school", ""),
        "degree": education.get("degree", ""),
        "major": education.get("major_or_discipline", ""),
        "graduation_year": str(education.get("graduation_year", "")),
        "graduation_month": education.get("graduation_month", ""),
        "currently_enrolled": education.get("currently_enrolled", False),
        "desired_salary": str(compensation.get("desired_salary", "")),
        "desired_hourly_rate": str(compensation.get("desired_hourly_rate", "")),
        "authorized_to_work": work_auth.get("legally_authorized_to_work_in_country", True),
        "needs_sponsorship": work_auth.get("needs_sponsorship", False),
        "skills": skills.get("software_tools_text", ""),
        "memberships": skills.get("professional_memberships_text", ""),
        "additional_skills": skills.get("other_relevant_skills_text", ""),
    }

    if history:
        h = history[0]
        profile.update({
            "last_employer": h.get("company_name", ""),
            "last_title": h.get("position_title", ""),
            "last_start": h.get("date_hired", ""),
            "last_end": h.get("date_separated", ""),
            "last_reason_leaving": h.get("reason_for_leaving", ""),
        })

    return profile
