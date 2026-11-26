from huntbot2.discovery.models import JobRecord


def _contains_any(text: str, terms: list[str]) -> bool:
    lowered = text.lower()
    return any(term.lower() in lowered for term in terms if term.strip())


_SPONSOR_HIGH_SIGNALS = [
    "will sponsor", "visa sponsorship", "h1b sponsor", "h-1b sponsor",
    "sponsorship provided", "we sponsor", "immigration assistance",
    "work authorization provided", "sponsorship available", "h1 sponsor",
    "visa support provided", "willing to sponsor", "open to sponsoring",
]
_SPONSOR_NONE_SIGNALS = [
    "no sponsorship", "no visa", "cannot sponsor", "will not sponsor",
    "must be authorized to work", "must be legally authorized",
    "us citizens only", "us citizens and permanent residents",
    "citizen and permanent resident only", "sponsorship not available",
    "no work visa", "no h1b", "no h-1b", "no h1", "not able to sponsor",
]


def detect_sponsorship(job: JobRecord) -> str:
    """Detect visa sponsorship likelihood from job text and source URL."""
    url = (job.url or "").lower()
    source = (job.source or "").lower()
    text = f"{job.title} {job.company} {job.raw_snapshot or ''}".lower()

    if "myvisajobs" in url or "myvisajobs" in source:
        return "high"
    if _contains_any(text, _SPONSOR_NONE_SIGNALS):
        return "none"
    if _contains_any(text, _SPONSOR_HIGH_SIGNALS):
        return "high"
    if _contains_any(text, ["sponsor", "h1b", "h-1b", "visa"]):
        return "medium"
    return "unknown"


def score_job(job: JobRecord, config) -> dict:
    title_company = f"{job.title} {job.company}"
    excludes = getattr(getattr(config, "filters", None), "exclude_keywords", [])
    if _contains_any(title_company, excludes):
        return {"score": 0, "reasons": [], "reject_reason": "excluded keyword"}

    score = 0
    reasons: list[str] = []

    # Role match — graduated:
    #   60 = full role phrase ("project manager", "supply chain analyst")
    #   30 = partial: a generic role token ("manager", "analyst", "coordinator")
    #        appears in the title AND the area keyword ("supply", "operations",
    #        "logistics", "project", "inventory") appears in title or company.
    role_terms = getattr(getattr(config, "profile", None), "roles", [])
    role_match = _contains_any(job.title, role_terms)
    if role_match:
        score += 55
        reasons.append("role keyword match")
    else:
        title_lo = (job.title or "").lower()
        company_lo = (job.company or "").lower()
        role_tokens = set()
        area_tokens = set()
        for r in role_terms:
            parts = str(r).lower().split()
            if not parts:
                continue
            role_tokens.add(parts[-1])  # "manager", "analyst", "coordinator"
            for p in parts[:-1]:
                area_tokens.add(p)  # "supply", "operations", "project", etc.
        has_role_token = any(tok in title_lo for tok in role_tokens)
        has_area_token = any(tok in title_lo or tok in company_lo for tok in area_tokens)
        if has_role_token and has_area_token:
            score += 30
            reasons.append("partial role match")

    # Location match — graduated:
    #   40 = full location phrase, or the job is explicitly remote
    #   20 = the city alone matches (handles "Tempe, AZ" when user wants Phoenix)
    location_terms = [x.lower() for x in getattr(getattr(config, "profile", None), "locations", [])]
    job_loc = (job.location or "").lower()
    is_remote = "remote" in job_loc
    # An explicit target-location match is a stronger signal than generic remote,
    # which matches almost everything — weight them differently so scores spread.
    target_terms = [t for t in location_terms if t.strip() and t != "remote"]
    if any(term in job_loc for term in target_terms):
        score += 30
        reasons.append("location match")
    else:
        # Match on city or state alone: split "phoenix, az" → ["phoenix", "az"]
        loc_tokens = set()
        for term in location_terms:
            for part in term.replace(",", " ").split():
                if len(part) >= 2:
                    loc_tokens.add(part)
        if is_remote:
            score += 25
            reasons.append("remote role")
        elif any(tok in job_loc for tok in loc_tokens):
            score += 20
            reasons.append("partial location match")

    # Description relevance: reward postings whose text echoes the target roles.
    description = (job.raw_snapshot or "").lower()
    if description and role_terms:
        overlap = sum(1 for t in role_terms if t.strip() and t.lower() in description)
        if overlap:
            score += min(15, overlap * 8)
            reasons.append("role terms in description")

    sponsorship = detect_sponsorship(job)
    require_sponsorship = getattr(getattr(config, "filters", None), "require_sponsorship", None)
    if sponsorship == "high":
        score += 5
        reasons.append("sponsors visas")
    elif sponsorship == "none" and require_sponsorship:
        return {"score": 0, "reasons": [], "reject_reason": "no sponsorship",
                "sponsorship_confidence": sponsorship}

    # A posting that doesn't match the target role isn't a strong lead, however
    # well it matches on other axes — cap it below the high tier.
    if not role_match:
        score = min(score, 40)

    score = max(0, min(100, score))

    return {"score": score, "reasons": reasons, "reject_reason": None,
            "sponsorship_confidence": sponsorship}


def score_job_llm(job: JobRecord, config, llm_client) -> dict:
    """Score a job using LLM. Falls back to score_job() on any error."""
    import json as _json
    profile = getattr(config, "profile", None)
    filters = getattr(config, "filters", None)
    roles = getattr(profile, "roles", []) if profile else []
    locations = getattr(profile, "locations", []) if profile else []
    remote_only = getattr(profile, "remote_only", False) if profile else False
    exclude_kw = getattr(filters, "exclude_keywords", []) if filters else []

    system = (
        "You are a job fit evaluator. Score the job 0-100 for this candidate. "
        "Return JSON only — no explanation, no markdown."
    )
    prompt = (
        f"## Candidate profile\n"
        f"Roles: {roles}\n"
        f"Locations: {locations}\n"
        f"Remote only: {remote_only}\n"
        f"Exclude keywords: {exclude_kw}\n\n"
        f"## Job\n"
        f"Title: {job.title}\n"
        f"Company: {job.company}\n"
        f"Location: {job.location}\n"
        f"Description: {(job.raw_snapshot or '')[:2000]}\n\n"
        'Return: {"score": int, "reasons": [str, ...], "reject_reason": str | null}'
    )

    try:
        raw = llm_client.ask(prompt, system=system, max_tokens=256)
        text = raw.strip()
        if "```" in text:
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
            text = text.strip().rstrip("`").strip()
        data = _json.loads(text)
        sponsorship = detect_sponsorship(job)
        return {
            "score": max(0, min(100, int(data.get("score", 0)))),
            "reasons": list(data.get("reasons", [])),
            "reject_reason": data.get("reject_reason"),
            "sponsorship_confidence": data.get("sponsorship_confidence", sponsorship),
        }
    except Exception:
        return score_job(job, config)
