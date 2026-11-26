"""LLM-driven browser agent for job discovery.

The agent sees the live browser via screenshots and uses an LLM to decide every
action: what to search for, what to click, when to scroll, which jobs to extract.
It runs a vision-action loop until it's found enough jobs or hit the action limit.
"""
from __future__ import annotations

import json
import os
import re
import time

from huntbot2.discovery.models import JobRecord
from huntbot2.discovery.normalize import (
    normalize_company,
    normalize_location,
    normalize_title,
    normalize_url,
)

_SYSTEM_PROMPT = """You are an autonomous job-hunting agent. Your only goal is to find relevant job listings.

CANDIDATE PROFILE:
- Target roles: {roles}
- Target locations: {locations}
- Background: operations management, supply chain, project management, process improvement

YOUR MISSION:
Browse {platform} and find job listings that match the target profile. You will see screenshots of the browser.
For each screenshot, decide the best next action to take.

PRIORITIES:
1. Search for the target roles in the target locations
2. Dismiss any login prompts, modals, or overlays before continuing
3. Scroll through job listings to see all available positions
4. Extract titles, companies, locations, and URLs of relevant jobs
5. Skip promoted/ad jobs unless they're genuinely relevant to the roles
6. Prefer jobs posted in the last 14 days

RESPONSE FORMAT — always respond with a single JSON object, no other text:
{{
  "reasoning": "what I see and why I'm taking this action",
  "action": "one of: navigate | type | click | scroll | extract | done",

  for navigate:  "url": "full URL to navigate to"
  for type:      "selector": "CSS selector or placeholder text", "text": "text to type"
  for click:     "selector": "CSS selector, link text, or button label"
  for scroll:    "direction": "down" or "up", "amount": pixels (300-1000)
  for extract:   "jobs": [ {{"title": "...", "company": "...", "location": "...", "url": "...", "posted": "..."}} ]
  for done:      (no extra fields)
}}

IMPORTANT:
- Use "extract" when you can see job listings on screen — list ALL visible jobs in one action
- Use "done" when you have extracted all jobs from the current search or can't find more
- Do NOT try to click individual job listings one by one — use "extract" to capture them all at once
- Navigate to specific search URLs rather than typing in the search bar when possible
"""

_PLATFORM_START_URLS = {
    "linkedin": "https://www.linkedin.com/jobs/search?keywords={kw}&location={loc}&sortBy=DD&f_TPR=r604800",
    "linkedin_remote": "https://www.linkedin.com/jobs/search?keywords={kw}&f_WT=2&sortBy=DD&f_TPR=r604800",
    "indeed": "https://www.indeed.com/jobs?q={kw}&l={loc}&sort=date&fromage=14",
    "glassdoor": "https://www.glassdoor.com/Job/jobs.htm?sc.keyword={kw}&locKeyword={loc}&sortBy=date_desc",
}


def _parse_action(raw: str) -> dict:
    """Extract JSON from LLM response, stripping markdown fences."""
    text = raw.strip()
    if "```" in text:
        blocks = re.findall(r"```(?:json)?\s*([\s\S]*?)```", text)
        if blocks:
            text = blocks[0].strip()
    try:
        return json.loads(text)
    except Exception:
        # Try to find the first { ... } block
        m = re.search(r"\{[\s\S]*\}", text)
        if m:
            try:
                return json.loads(m.group())
            except Exception:
                pass
    return {"action": "done", "reasoning": "could not parse LLM response"}


def _screenshot(page) -> bytes:
    return page.screenshot(type="jpeg", quality=60, full_page=False)


def _execute_action(page, action: dict) -> str:
    """Execute a Playwright action from the LLM decision. Returns a status string."""
    kind = action.get("action", "done")

    if kind == "navigate":
        url = action.get("url", "")
        if url:
            page.goto(url, wait_until="domcontentloaded", timeout=20000)
            time.sleep(1.5)
        return f"navigated to {url[:60]}"

    if kind == "type":
        sel = action.get("selector", "")
        text = action.get("text", "")
        if sel and text:
            try:
                el = page.wait_for_selector(sel, timeout=4000)
                el.triple_click()
                el.type(text)
                page.keyboard.press("Enter")
                time.sleep(2)
            except Exception:
                page.keyboard.type(text)
                page.keyboard.press("Enter")
                time.sleep(2)
        return f"typed '{text}' into {sel}"

    if kind == "click":
        sel = action.get("selector", "")
        if sel:
            try:
                el = page.wait_for_selector(sel, timeout=4000)
                el.click()
                time.sleep(1.5)
            except Exception:
                try:
                    page.click(f"text={sel}", timeout=3000)
                    time.sleep(1.5)
                except Exception:
                    pass
        return f"clicked {sel}"

    if kind == "scroll":
        direction = action.get("direction", "down")
        amount = int(action.get("amount", 500))
        sign = 1 if direction == "down" else -1
        page.evaluate(f"window.scrollBy(0, {sign * amount})")
        time.sleep(1.0)
        return f"scrolled {direction} {amount}px"

    return "no-op"


def discover_with_agent(
    roles: list[str],
    locations: list[str],
    llm_client,
    extra_urls: list[str] | None = None,
    platforms: list[str] | None = None,
    headed: bool = True,
    display: str = ":99",
    max_actions: int = 15,
    max_jobs: int = 40,
    max_urls: int = 4,
    profile_base: str | None = None,
) -> list[JobRecord]:
    """Run the LLM browser agent to discover jobs.

    Hard limits:
    - max_actions: LLM decisions per URL (default 15)
    - max_jobs: stop globally once this many jobs are collected (default 40)
    - max_urls: cap how many search URLs are visited per run (default 4)
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

    from urllib.parse import quote_plus

    # Build initial URLs for each role × location combination
    start_urls: list[str] = []
    for role in roles:
        kw = quote_plus(role)
        for loc in locations:
            remote = "remote" in loc.lower()
            if "linkedin" in platforms:
                tmpl = _PLATFORM_START_URLS["linkedin_remote"] if remote else _PLATFORM_START_URLS["linkedin"]
                start_urls.append(tmpl.format(kw=kw, loc=quote_plus(loc)))
    for u in (extra_urls or []):
        if u not in start_urls:
            start_urls.append(u)

    # Hard cap on number of URLs visited per run
    start_urls = start_urls[:max_urls]

    primary_platform = platforms[0] if platforms else "linkedin"
    # Use a separate profile dir from the viewer browser to avoid ProcessSingleton lock conflicts
    profile_dir = os.path.join(profile_base, f"agent-{primary_platform}")
    os.makedirs(profile_dir, exist_ok=True)

    # Check display availability — fall back to headless silently
    display_lock = f"/tmp/.X{display.lstrip(':')}-lock"
    use_headed = headed and os.path.exists(display_lock)
    # Strip Wayland vars so Chromium uses the Xvfb X11 display, not the physical Wayland session
    launch_env = {k: v for k, v in os.environ.items()
                  if k not in ("WAYLAND_DISPLAY", "XDG_SESSION_TYPE", "WAYLAND_SOCKET")}
    launch_env["GDK_BACKEND"] = "x11"
    if use_headed:
        launch_env["DISPLAY"] = display

    all_jobs: list[JobRecord] = []
    seen: set[str] = set()

    system = _SYSTEM_PROMPT.format(
        roles=", ".join(roles),
        locations=", ".join(locations),
        platform=primary_platform.title(),
    )

    try:
        with sync_playwright() as p:
            ctx = p.chromium.launch_persistent_context(
                user_data_dir=profile_dir,
                headless=not use_headed,
                slow_mo=400,
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

            for start_url in start_urls:
                if len(all_jobs) >= max_jobs:
                    break

                # Navigate to the starting URL for this search
                try:
                    page.goto(start_url, wait_until="domcontentloaded", timeout=20000)
                    time.sleep(2)
                except Exception:
                    continue

                actions_taken = 0
                while actions_taken < max_actions and len(all_jobs) < max_jobs:
                    # Capture what the LLM sees
                    try:
                        screenshot = _screenshot(page)
                    except Exception:
                        break

                    prompt = (
                        f"Current URL: {page.url}\n"
                        f"Action #{actions_taken + 1} of max {max_actions}\n"
                        f"Jobs extracted so far this session: {len(all_jobs)}\n\n"
                        "What should I do next? Respond with a single JSON action."
                    )

                    try:
                        raw = llm_client.ask_vision(prompt, screenshot, system=system, max_tokens=1500)
                    except Exception:
                        # Vision not supported — fall back to text-only with page text
                        try:
                            page_text = page.inner_text("body")[:3000]
                            raw = llm_client.ask(
                                f"{prompt}\n\nPage content:\n{page_text}",
                                system=system,
                                max_tokens=1500,
                            )
                        except Exception:
                            break

                    action = _parse_action(raw)
                    actions_taken += 1

                    if action.get("action") == "done":
                        break

                    if action.get("action") == "extract":
                        # LLM has identified jobs — store them
                        for j in action.get("jobs", []):
                            u = normalize_url(j.get("url", ""))
                            t = normalize_title(j.get("title", ""))
                            if not t or u in seen:
                                continue
                            if not u:
                                u = page.url
                            seen.add(u)
                            all_jobs.append(
                                JobRecord(
                                    source=primary_platform,
                                    title=t,
                                    company=normalize_company(j.get("company", "")),
                                    location=normalize_location(j.get("location", "")),
                                    url=u,
                                    posted_at=j.get("posted") or None,
                                    raw_snapshot=json.dumps(j),
                                )
                            )
                        # After extract, continue scrolling to find more
                    else:
                        _execute_action(page, action)

            ctx.close()
    except Exception:
        pass

    return all_jobs
