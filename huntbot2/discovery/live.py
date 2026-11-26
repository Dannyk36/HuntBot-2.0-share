"""Phase 9C — Playwright-driven live job discovery.

Launches a browser (headed or headless) with the user's saved profile so
platform logins are preserved.  In headed mode the browser appears on the
Xvfb :99 display and is visible through noVNC.
"""
from __future__ import annotations

import os
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
_INDEED_SEARCH = (
    "https://www.indeed.com/jobs?q={kw}&l={loc}&sort=date&fromage=30"
)
_INDEED_REMOTE = (
    "https://www.indeed.com/jobs?q={kw}&remotejob=032b3046-06a3-4876-8dfd-474eb5e7ed11&sort=date"
)
_GLASSDOOR_SEARCH = (
    "https://www.glassdoor.com/Job/jobs.htm?sc.keyword={kw}&locKeyword={loc}&sortBy=date_desc"
)
_GLASSDOOR_REMOTE = (
    "https://www.glassdoor.com/Job/jobs.htm?sc.keyword={kw}&remoteWorkType=1&sortBy=date_desc"
)
# Handshake requires a logged-in (university) session — sign in once via Live
# View with the platform profile and the search pages become reachable.
_HANDSHAKE_SEARCH = (
    "https://app.joinhandshake.com/stu/postings"
    "?query={kw}&location_type=located&location_name={loc}&sort_direction=desc&sort_column=default"
)
_HANDSHAKE_REMOTE = (
    "https://app.joinhandshake.com/stu/postings"
    "?query={kw}&location_type=anywhere&sort_direction=desc&sort_column=default"
)
_ZIPRECRUITER_SEARCH = "https://www.ziprecruiter.com/jobs-search?search={kw}&location={loc}"
_DICE_SEARCH = "https://www.dice.com/jobs?q={kw}&location={loc}"
_WELLFOUND_SEARCH = "https://wellfound.com/jobs?keywords={kw}"
_MONSTER_SEARCH = "https://www.monster.com/jobs/search?q={kw}&where={loc}"


def _platform_of(url: str) -> str:
    hosts = {
        "linkedin.com": "linkedin",
        "indeed.com": "indeed",
        "glassdoor.com": "glassdoor",
        "joinhandshake.com": "handshake",
        "ziprecruiter.com": "ziprecruiter",
        "dice.com": "dice",
        "wellfound.com": "wellfound",
        "monster.com": "monster",
    }
    for host, name in hosts.items():
        if host in url:
            return name
    return "other"


def _build_search_urls(roles: list[str], locations: list[str], platforms: list[str]) -> list[str]:
    urls: list[str] = []
    for role in roles:
        kw = quote_plus(role)
        for loc in locations:
            remote = "remote" in loc.lower()
            loc_q = quote_plus(loc)
            if "linkedin" in platforms:
                if remote:
                    urls.append(_LINKEDIN_REMOTE.format(kw=kw))
                else:
                    urls.append(_LINKEDIN_SEARCH.format(kw=kw, loc=loc_q))
            if "indeed" in platforms:
                if remote:
                    urls.append(_INDEED_REMOTE.format(kw=kw))
                else:
                    urls.append(_INDEED_SEARCH.format(kw=kw, loc=loc_q))
            if "glassdoor" in platforms:
                if remote:
                    urls.append(_GLASSDOOR_REMOTE.format(kw=kw))
                else:
                    urls.append(_GLASSDOOR_SEARCH.format(kw=kw, loc=loc_q))
            if "handshake" in platforms:
                if remote:
                    urls.append(_HANDSHAKE_REMOTE.format(kw=kw))
                else:
                    urls.append(_HANDSHAKE_SEARCH.format(kw=kw, loc=loc_q))
            if "ziprecruiter" in platforms:
                urls.append(_ZIPRECRUITER_SEARCH.format(kw=kw, loc=loc_q))
            if "dice" in platforms:
                urls.append(_DICE_SEARCH.format(kw=kw, loc=loc_q))
            if "wellfound" in platforms:
                urls.append(_WELLFOUND_SEARCH.format(kw=kw))
            if "monster" in platforms:
                urls.append(_MONSTER_SEARCH.format(kw=kw, loc=loc_q))
    return list(dict.fromkeys(urls))


def _dismiss_overlay(page) -> None:
    for selector in (
        "[data-tracking-control-name='auth-wall_guest-homepage-login-form_modal_dismiss']",
        ".modal__dismiss",
        "button[aria-label='Dismiss']",
        ".contextual-sign-in-modal__modal-dismiss-icon",
    ):
        try:
            btn = page.wait_for_selector(selector, timeout=1500)
            if btn:
                btn.click()
                time.sleep(0.4)
                return
        except Exception:
            pass


def _scroll_to_load(page, scrolls: int = 4, pause: float = 1.2) -> None:
    for _ in range(scrolls):
        page.evaluate("window.scrollBy(0, Math.floor(window.innerHeight * 0.8))")
        time.sleep(pause)


def _extract_linkedin(page) -> list[dict]:
    return page.evaluate("""() => {
        // Strategy 1: authenticated React SPA — div[data-job-id] cards
        const authCards = [...document.querySelectorAll('div[data-job-id]')];
        if (authCards.length) {
            return authCards.slice(0, 60).map(c => {
                const lnk = c.querySelector('.job-card-list__title--link, .job-card-list__title');
                const rawHref = lnk ? (lnk.href || lnk.getAttribute('href') || '') : '';
                // Strip LinkedIn tracking params — keep only the job view path
                let url = rawHref;
                try {
                    const u = new URL(rawHref);
                    url = u.origin + u.pathname;
                } catch(e) {}
                // Title text is often duplicated in the auth version (React rendering
                // artefact — same string appears twice with no separator).
                // Strategy: take first newline segment, then strip any trailing repeat.
                const rawTitle = lnk ? lnk.textContent.trim() : '';
                let title = rawTitle.split('\\n')[0].trim();
                // Check for exact mid-string duplication of any length ≥6 chars
                for (let half = Math.floor(title.length / 2); half >= 6; half--) {
                    if (title.slice(0, half) === title.slice(half, half * 2)) {
                        title = title.slice(0, half).trim();
                        break;
                    }
                }
                const company = (
                    c.querySelector('.job-card-container__company-name') ||
                    c.querySelector('.artdeco-entity-lockup__subtitle')
                )?.textContent?.trim() || '';
                const location = (
                    c.querySelector('.job-card-container__metadata-item') ||
                    c.querySelector('.artdeco-entity-lockup__caption')
                )?.textContent?.trim() || '';
                return {
                    title,
                    company,
                    location,
                    url,
                    posted_at: c.querySelector('time')?.getAttribute('datetime') || '',
                };
            }).filter(j => j.title && j.url);
        }

        // Strategy 2: public/unauthenticated pages — base-search-card
        const sets = [
            {
                card: '.base-search-card',
                title: '.base-search-card__title',
                company: '.base-search-card__subtitle',
                location: '.job-search-card__location',
                link: 'a.base-card__full-link',
                time: 'time',
            },
            {
                card: 'li.jobs-search-results__list-item',
                title: '.job-card-list__title--link, .job-card-list__title',
                company: '.job-card-container__company-name',
                location: '.job-card-container__metadata-item',
                link: '.job-card-list__title--link, .job-card-list__title',
                time: 'time',
            },
        ];
        for (const s of sets) {
            const cards = [...document.querySelectorAll(s.card)];
            if (!cards.length) continue;
            return cards.slice(0, 60).map(c => {
                const lnk = c.querySelector(s.link);
                return {
                    title:     c.querySelector(s.title)?.textContent?.trim() || '',
                    company:   c.querySelector(s.company)?.textContent?.trim() || '',
                    location:  c.querySelector(s.location)?.textContent?.trim() || '',
                    url:       lnk?.href || lnk?.getAttribute('href') || '',
                    posted_at: c.querySelector(s.time)?.getAttribute('datetime') || '',
                };
            }).filter(j => j.title && j.url);
        }
        return [];
    }""")


def _extract_indeed(page) -> list[dict]:
    return page.evaluate("""() => {
        const cards = [...document.querySelectorAll('.job_seen_beacon, .resultContent')];
        return cards.slice(0, 60).map(c => {
            const titleEl = c.querySelector('.jobTitle a, h2.jobTitle a');
            const href = titleEl?.getAttribute('href') || '';
            return {
                title:    titleEl?.textContent?.trim() || '',
                company:  c.querySelector('.companyName')?.textContent?.trim() || '',
                location: c.querySelector('.companyLocation')?.textContent?.trim() || '',
                url:      href.startsWith('http') ? href : 'https://www.indeed.com' + href,
                posted_at: c.querySelector('.date')?.textContent?.trim() || '',
            };
        }).filter(j => j.title && j.url);
    }""")


def _extract_glassdoor(page) -> list[dict]:
    return page.evaluate("""() => {
        const cards = [...document.querySelectorAll('[data-test="jobListing"], .react-job-listing')];
        return cards.slice(0, 60).map(c => {
            const titleEl = c.querySelector('[data-test="job-title"], .job-title');
            const linkEl  = c.querySelector('a[data-test="job-title"], a.jobLink');
            const href    = linkEl?.getAttribute('href') || '';
            return {
                title:    titleEl?.textContent?.trim() || '',
                company:  c.querySelector('[data-test="employer-name"], .employer-name')?.textContent?.trim() || '',
                location: c.querySelector('[data-test="emp-location"], .location')?.textContent?.trim() || '',
                url:      href.startsWith('http') ? href : 'https://www.glassdoor.com' + href,
                posted_at: '',
            };
        }).filter(j => j.title && j.url);
    }""")


def _extract_handshake(page) -> list[dict]:
    return page.evaluate("""() => {
        // Handshake is a React SPA; job posting links are the most stable anchor.
        // Walk up from each posting link to find the card container, then pull
        // title/company/location from within that container.
        const links = [...document.querySelectorAll('a[href*="/stu/postings/"]')];
        const seen = new Set();
        const jobs = [];
        for (const a of links) {
            const href = a.href;
            // Strip query params — ID is the last path segment
            const canonical = href.split('?')[0].split('#')[0];
            if (seen.has(canonical)) continue;
            // Skip links that are just page navigation (no numeric posting ID)
            if (!/\/postings\/\d+/.test(canonical)) continue;
            seen.add(canonical);
            // Walk up to find the card container (up to 6 levels)
            let card = a;
            for (let i = 0; i < 6; i++) {
                if (!card.parentElement) break;
                card = card.parentElement;
                if (card.querySelectorAll('a[href*="/stu/postings/"]').length === 1) break;
            }
            // Title: prefer the link text itself, or a heading inside the card
            const title = a.textContent.trim()
                || card.querySelector('h3, h2, [class*="title"], [class*="name"]')?.textContent?.trim()
                || '';
            // Company: look for common patterns
            const companyEl = card.querySelector(
                '[class*="company"], [class*="employer"], [class*="org"], [data-hook="company"]'
            );
            const company = companyEl?.textContent?.trim() || '';
            // Location: look for location-like elements
            const locationEl = card.querySelector(
                '[class*="location"], [class*="city"], [data-hook="location"]'
            );
            const location = locationEl?.textContent?.trim() || '';
            if (!title) continue;
            jobs.push({title, company, location, url: canonical, posted_at: ''});
        }
        return jobs.slice(0, 60);
    }""")


def _extract_generic(page) -> list[dict]:
    """Best-effort extraction for unknown job board pages."""
    return page.evaluate("""() => {
        const anchors = [...document.querySelectorAll('a[href]')].filter(a => {
            const h = a.href.toLowerCase();
            return h.includes('/job') || h.includes('/career') || h.includes('/position');
        });
        const seen = new Set();
        return anchors.slice(0, 60).map(a => {
            if (seen.has(a.href)) return null;
            seen.add(a.href);
            const txt = a.textContent.trim();
            if (!txt || txt.length < 5 || txt.length > 120) return null;
            return {title: txt, company: '', location: '', url: a.href, posted_at: ''};
        }).filter(Boolean);
    }""")


def _scrape_url(page, url: str) -> list[dict]:
    platform = _platform_of(url)
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=25000)
    except Exception:
        return []

    if platform == "linkedin":
        try:
            page.wait_for_selector(
                ".base-search-card, .jobs-search-results__list-item, div[data-job-id]",
                timeout=10000,
            )
        except Exception:
            pass
        _dismiss_overlay(page)
        _scroll_to_load(page)
        return _extract_linkedin(page)

    if platform == "indeed":
        try:
            page.wait_for_selector(".job_seen_beacon, .resultContent", timeout=10000)
        except Exception:
            pass
        _scroll_to_load(page, scrolls=3)
        return _extract_indeed(page)

    if platform == "glassdoor":
        try:
            page.wait_for_selector("[data-test='jobListing'], .react-job-listing", timeout=10000)
        except Exception:
            pass
        _dismiss_overlay(page)
        _scroll_to_load(page, scrolls=3)
        return _extract_glassdoor(page)

    if platform == "handshake":
        try:
            page.wait_for_selector("a[href*='/stu/postings/']", timeout=12000)
        except Exception:
            pass
        _scroll_to_load(page, scrolls=4, pause=1.5)
        return _extract_handshake(page)

    # Unknown platform — generic extract
    try:
        page.wait_for_load_state("networkidle", timeout=8000)
    except Exception:
        pass
    return _extract_generic(page)


def discover_on_page(page, urls: list[str], seen: set | None = None) -> list[JobRecord]:
    """Scrape multiple search URLs on an already-open Playwright page.

    Unlike discover_live(), this does not open a new browser context — the
    caller owns the page and keeps it open between calls.  Used by the VNC
    session orchestrator so discovery and apply share one visible window.
    """
    if seen is None:
        seen = set()
    jobs: list[JobRecord] = []
    for url in urls:
        raw = _scrape_url(page, url)
        for j in raw:
            u = normalize_url(j.get("url", ""))
            t = normalize_title(j.get("title", ""))
            if not u or not t or u in seen:
                continue
            seen.add(u)
            jobs.append(
                JobRecord(
                    source=_platform_of(url),
                    title=t,
                    company=normalize_company(j.get("company", "")),
                    location=normalize_location(j.get("location", "")),
                    url=u,
                    posted_at=j.get("posted_at") or None,
                    raw_snapshot=str(j),
                )
            )
        time.sleep(1.0)
    return jobs


def _display_ready(display: str) -> bool:
    lock = f"/tmp/.X{display.lstrip(':')}-lock"
    return os.path.exists(lock)


def _scrape_platform_urls(
    p,
    urls: list[str],
    profile_dir: str,
    use_headed: bool,
    slow_mo: int,
    launch_env: dict,
    seen: set,
) -> list[JobRecord]:
    """Open one persistent browser context for a single platform's URLs."""
    jobs: list[JobRecord] = []
    os.makedirs(profile_dir, exist_ok=True)
    try:
        ctx = p.chromium.launch_persistent_context(
            user_data_dir=profile_dir,
            headless=not use_headed,
            slow_mo=slow_mo,
            args=[
                "--no-sandbox",
                    "--password-store=basic",
                "--disable-dev-shm-usage",
                "--disable-blink-features=AutomationControlled",
                "--start-maximized",
            ],
            env=launch_env,
            user_agent=(
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
            ),
            viewport=None if use_headed else {"width": 1280, "height": 800},
        )
        page = ctx.new_page()
        for url in urls:
            raw = _scrape_url(page, url)
            for j in raw:
                u = normalize_url(j.get("url", ""))
                t = normalize_title(j.get("title", ""))
                if not u or not t or u in seen:
                    continue
                seen.add(u)
                jobs.append(
                    JobRecord(
                        source=_platform_of(url),
                        title=t,
                        company=normalize_company(j.get("company", "")),
                        location=normalize_location(j.get("location", "")),
                        url=u,
                        posted_at=j.get("posted_at") or None,
                        raw_snapshot=str(j),
                    )
                )
            time.sleep(1.0)
        ctx.close()
    except Exception:
        pass
    return jobs


def discover_live(
    roles: list[str],
    locations: list[str],
    extra_urls: list[str] | None = None,
    platforms: list[str] | None = None,
    headed: bool = False,
    display: str = ":99",
    slow_mo: int = 0,
    profile_base: str | None = None,
) -> list[JobRecord]:
    """Discover jobs using a Playwright browser.

    Opens a separate persistent browser context per platform so each site
    gets its own session cookies (logged-in state).  Falls back to headless
    if the Xvfb display is not ready.  Returns an empty list on total failure.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return []

    if profile_base is None:
        profile_base = os.path.expanduser(
            os.getenv("HUNTBOT2_BROWSER_PROFILE_BASE", "~/.huntbot2/profiles")
        )
    if platforms is None:
        platforms = ["linkedin"]

    # Build per-platform URL buckets
    all_search_urls = _build_search_urls(roles, locations, platforms)
    for u in (extra_urls or []):
        if u not in all_search_urls:
            all_search_urls.append(u)

    if not all_search_urls:
        return []

    # Group URLs by platform so each gets its own browser profile / context
    platform_urls: dict[str, list[str]] = {}
    for url in all_search_urls:
        p_name = _platform_of(url)
        platform_urls.setdefault(p_name, []).append(url)

    # For extra URLs whose platform resolves to "other", group them together
    # under a generic profile so they still get a browser session
    for url in (extra_urls or []):
        if _platform_of(url) == "other":
            platform_urls.setdefault("other", []).append(url)

    use_headed = headed and _display_ready(display)
    launch_env = dict(os.environ)
    if use_headed:
        launch_env["DISPLAY"] = display

    all_jobs: list[JobRecord] = []
    seen: set[str] = set()

    with sync_playwright() as p:
        for platform_name, urls in platform_urls.items():
            # LinkedIn apply profile is stored as "agent-linkedin"; prefer that
            # for LinkedIn so discovery and apply share cookies.
            if platform_name in ("linkedin", "handshake"):
                # Handshake was logged in inside the LinkedIn browser, so both
                # platforms share the agent-linkedin profile and its cookies.
                agent_dir = os.path.join(profile_base, "agent-linkedin")
                profile_dir = agent_dir if os.path.exists(agent_dir) else os.path.join(profile_base, "linkedin")
            else:
                profile_dir = os.path.join(profile_base, platform_name)

            batch = _scrape_platform_urls(
                p, urls, profile_dir, use_headed, slow_mo, launch_env, seen
            )
            all_jobs.extend(batch)

    return all_jobs
