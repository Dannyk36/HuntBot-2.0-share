import hashlib
from urllib.parse import urlparse, parse_qs

from huntbot2.discovery.models import JobRecord
from huntbot2.discovery.normalize import (
    normalize_company,
    normalize_location,
    normalize_title,
    normalize_url,
)


def _norm(value: str) -> str:
    return value.lower().strip()


def _linkedin_job_id(url: str) -> str:
    """Extract the canonical numeric job ID from a normalized LinkedIn URL.

    normalize_url() already converts slug and search URLs to /jobs/view/{id}/,
    so this just plucks the digit segment.
    """
    import re
    m = re.search(r"/jobs/view/(\d+)", url)
    return m.group(1) if m else ""


def dedup_key_for_job(job: JobRecord) -> str:
    canonical_url = normalize_url(job.url)
    parsed = urlparse(canonical_url)

    netloc = parsed.netloc.lower()

    # LinkedIn: use numeric job ID (slug variants, search-page params all map here)
    if "linkedin.com" in netloc and "/jobs/view/" in parsed.path:
        job_id = _linkedin_job_id(canonical_url)
        url_part = f"linkedin.com/jobs/view/{job_id}" if job_id else f"{netloc}{parsed.path}"

    # Indeed: normalize_url produces /viewjob?jk={id}; extract jk into path
    elif "indeed.com" in netloc:
        qs = parse_qs(parsed.query)
        jk = (qs.get("jk") or [""])[0].strip()
        url_part = f"indeed.com/viewjob/{jk}" if jk else f"{netloc}{parsed.path}"

    # Glassdoor: normalize_url produces /job-listing/{jl}; netloc+path is canonical
    else:
        url_part = f"{netloc}{parsed.path}"

    material = "|".join(
        [
            _norm(normalize_company(job.company)),
            _norm(normalize_title(job.title)),
            _norm(normalize_location(job.location)),
            _norm(url_part),
        ]
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()
