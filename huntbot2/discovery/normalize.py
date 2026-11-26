from __future__ import annotations

from urllib.parse import urlparse, parse_qs


def normalize_text(value: str | None) -> str:
    return " ".join((value or "").strip().split())


def normalize_title(value: str | None) -> str:
    return normalize_text(value)


def normalize_company(value: str | None) -> str:
    return normalize_text(value)


def normalize_location(value: str | None) -> str:
    return normalize_text(value)


def normalize_url(value: str | None) -> str:
    import re
    text = (value or "").strip()
    if not text:
        return ""
    parsed = urlparse(text)
    if not parsed.scheme or not parsed.netloc:
        return ""

    # Indeed: job ID is the `jk` query param.  Canonicalize to /viewjob?jk={id}
    # so the same job discovered via /rc/clk, /applystart, or /viewjob all hash
    # to the same URL.
    if "indeed.com" in parsed.netloc:
        qs = parse_qs(parsed.query)
        jk = (qs.get("jk") or [""])[0].strip()
        if jk:
            return f"https://www.indeed.com/viewjob?jk={jk}"

    # Glassdoor: job ID is the `jl` query param.  Put it in the path so
    # dedup_key_for_job can use netloc+path without also parsing the query.
    if "glassdoor.com" in parsed.netloc:
        qs = parse_qs(parsed.query)
        jl = (qs.get("jl") or [""])[0].strip()
        if jl:
            return f"https://www.glassdoor.com/job-listing/{jl}"

    if "linkedin.com" in parsed.netloc:
        # Search pages embed the job ID as ?currentJobId=NNN.
        if "/jobs/search" in parsed.path:
            qs = parse_qs(parsed.query)
            job_id = (qs.get("currentJobId") or [""])[0].strip()
            if job_id:
                return f"https://www.linkedin.com/jobs/view/{job_id}/"

        # Slug URLs: /jobs/view/some-title-at-company-4397659975
        # The trailing numeric segment IS the job ID. Canonicalize to
        # /jobs/view/{id}/ so the same job never gets two DB rows due to
        # slug variations (LinkedIn recycles IDs; slugs are cosmetic).
        if "/jobs/view/" in parsed.path:
            m = re.search(r"-(\d{7,})(?:/.*)?$", parsed.path)
            if m:
                return f"https://www.linkedin.com/jobs/view/{m.group(1)}/"
            # Already a pure-ID URL like /jobs/view/4398625353/
            m2 = re.match(r".*/jobs/view/(\d+)/?$", parsed.path)
            if m2:
                return f"https://www.linkedin.com/jobs/view/{m2.group(1)}/"

    return parsed._replace(query="", fragment="").geturl()
