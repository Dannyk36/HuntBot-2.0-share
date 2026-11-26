from __future__ import annotations

import time
from urllib.parse import quote_plus

from huntbot2.discovery.models import JobRecord
from huntbot2.discovery.normalize import (
    normalize_company,
    normalize_location,
    normalize_title,
    normalize_url,
)

_LINKEDIN_SEARCH = (
    "https://www.linkedin.com/jobs/search"
    "?keywords={kw}&location={loc}&sortBy=DD&f_TPR=r2592000"
)
_LINKEDIN_REMOTE = (
    "https://www.linkedin.com/jobs/search"
    "?keywords={kw}&f_WT=2&sortBy=DD&f_TPR=r2592000"
)


def build_linkedin_urls(roles: list[str], locations: list[str]) -> list[str]:
    urls = []
    for role in roles:
        kw = quote_plus(role)
        for loc in locations:
            if "remote" in loc.lower():
                urls.append(_LINKEDIN_REMOTE.format(kw=kw))
            else:
                urls.append(_LINKEDIN_SEARCH.format(kw=kw, loc=quote_plus(loc)))
    return urls


def _fetch_linkedin_page(url: str, timeout_ms: int = 20000) -> list[JobRecord]:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return []

    jobs: list[JobRecord] = []
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=True,
                args=[
                    "--no-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-blink-features=AutomationControlled",
                ],
            )
            ctx = browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
                ),
                viewport={"width": 1280, "height": 800},
            )
            page = ctx.new_page()
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
                page.wait_for_selector(".base-search-card", timeout=10000)
            except Exception:
                pass

            raw: list[dict] = page.evaluate("""() => {
                const cards = document.querySelectorAll('.base-search-card');
                return Array.from(cards).map(card => {
                    const titleEl = card.querySelector('.base-search-card__title');
                    const companyEl = card.querySelector('.base-search-card__subtitle');
                    const locationEl = card.querySelector('.job-search-card__location');
                    const linkEl = card.querySelector('a.base-card__full-link');
                    const timeEl = card.querySelector('time');
                    return {
                        title: titleEl?.textContent?.trim() || '',
                        company: companyEl?.textContent?.trim() || '',
                        location: locationEl?.textContent?.trim() || '',
                        url: linkEl?.href || '',
                        posted_at: timeEl?.getAttribute('datetime') || timeEl?.textContent?.trim() || '',
                    };
                }).filter(j => j.title && j.url);
            }""")

            for j in raw:
                t = normalize_title(j["title"])
                u = normalize_url(j["url"])
                if not t or not u:
                    continue
                jobs.append(
                    JobRecord(
                        source="linkedin",
                        title=t,
                        company=normalize_company(j.get("company", "")),
                        location=normalize_location(j.get("location", "")),
                        url=u,
                        posted_at=j.get("posted_at") or None,
                        raw_snapshot=str(j),
                    )
                )
            browser.close()
    except Exception:
        pass

    return jobs


def discover_live(roles: list[str], locations: list[str]) -> list[JobRecord]:
    """Scrape LinkedIn job search pages for the given roles and locations.

    Returns an empty list on total failure so callers can fall back to fixture.
    """
    urls = build_linkedin_urls(roles, locations)
    all_jobs: list[JobRecord] = []
    seen: set[str] = set()

    for url in urls:
        batch = _fetch_linkedin_page(url)
        for job in batch:
            if job.url not in seen:
                seen.add(job.url)
                all_jobs.append(job)
        if batch:
            time.sleep(1.5)

    return all_jobs
