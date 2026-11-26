"""Fetch full job description text from a job posting URL.

Tries JSON-LD structured data first (reliable for LinkedIn public pages),
then falls back to known HTML selectors. Returns plain text.
"""
from __future__ import annotations

import json
import re
from html import unescape


def fetch_job_description(url: str, *, timeout: float = 15.0) -> str | None:
    """Return plain-text job description from the posting URL, or None."""
    if not url or not url.startswith(("http://", "https://")):
        return None
    try:
        import httpx
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        }
        resp = httpx.get(url, headers=headers, timeout=timeout, follow_redirects=True)
        if not resp.is_success:
            return None
        return _extract_description(resp.text)
    except Exception:
        return None


def _extract_description(html: str) -> str | None:
    # 1. JSON-LD structured data — most reliable, works for LinkedIn public pages
    for m in re.finditer(
        r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
        html, re.DOTALL | re.IGNORECASE,
    ):
        try:
            data = json.loads(m.group(1))
            entries = data if isinstance(data, list) else [data]
            for entry in entries:
                if isinstance(entry, dict) and entry.get("@type") == "JobPosting":
                    desc = entry.get("description", "")
                    if desc:
                        return _clean(desc)
        except Exception:
            continue

    # 2. LinkedIn-specific HTML selectors
    patterns = [
        r'class="show-more-less-html__markup[^"]*"[^>]*>(.*?)</div>',
        r'class="description__text[^"]*"[^>]*>(.*?)</section>',
        r'class="jobs-description[^"]*"[^>]*>(.*?)</section>',
        # Indeed
        r'id="jobDescriptionText"[^>]*>(.*?)</div>',
        # Glassdoor
        r'class="desc[^"]*jobDescriptionContent[^"]*"[^>]*>(.*?)</div>',
    ]
    for pat in patterns:
        m = re.search(pat, html, re.DOTALL | re.IGNORECASE)
        if m:
            text = _clean(m.group(1))
            if len(text) > 100:
                return text

    return None


def _clean(raw: str) -> str:
    """Strip HTML tags, decode entities, collapse whitespace."""
    text = re.sub(r'<[^>]+>', ' ', raw)
    text = unescape(text)
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()
