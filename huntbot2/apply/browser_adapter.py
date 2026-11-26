from __future__ import annotations

from pathlib import Path
from typing import Any


def _safe_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def probe_job_page(
    url: str,
    mode: str = "headed",
    timeout_ms: int = 10000,
    screenshot_path: str | None = None,
) -> dict:
    """Read-only browser probe for a job page.

    Never clicks submit and performs no write actions. If Playwright is not available,
    returns a deterministic fallback response.
    """
    probe_mode = mode if mode in {"headed", "headless"} else "headed"
    result: dict[str, Any] = {
        "ok": False,
        "adapter_available": False,
        "reason": None,
        "mode": probe_mode,
        "requested_url": url,
        "final_url": None,
        "page_title": None,
        "counts": {
            "input": 0,
            "select": 0,
            "textarea": 0,
            "button": 0,
            "form": 0,
        },
        "fields": [],
        "timed_out": False,
    }
    if screenshot_path is not None:
        result["screenshot_path"] = screenshot_path

    try:
        from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
        from playwright.sync_api import sync_playwright
    except Exception:
        result["reason"] = "playwright_unavailable"
        return result

    result["adapter_available"] = True

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=(probe_mode == "headless"))
            context = browser.new_context()
            page = context.new_page()
            try:
                page.goto(url, timeout=timeout_ms, wait_until="domcontentloaded")
            except PlaywrightTimeoutError:
                result["timed_out"] = True
                result["reason"] = "timeout"

            result["final_url"] = page.url
            try:
                result["page_title"] = page.title()
            except Exception:
                result["page_title"] = ""

            try:
                result["html"] = page.content()
            except Exception:
                result["html"] = ""

            selectors = {
                "input": "input",
                "select": "select",
                "textarea": "textarea",
                "button": "button",
                "form": "form",
            }
            for key, selector in selectors.items():
                result["counts"][key] = len(page.query_selector_all(selector))

            field_entries: list[dict[str, str]] = []
            elements = page.query_selector_all("input, select, textarea, button")
            for el in elements[:20]:
                entry = {
                    "tag": _safe_text(el.evaluate("e => e.tagName.toLowerCase()")),
                    "name": _safe_text(el.get_attribute("name")),
                    "id": _safe_text(el.get_attribute("id")),
                    "placeholder": _safe_text(el.get_attribute("placeholder")),
                }
                field_entries.append(entry)

            result["fields"] = field_entries

            if screenshot_path is not None:
                screenshot_file = Path(screenshot_path)
                screenshot_file.parent.mkdir(parents=True, exist_ok=True)
                page.screenshot(path=str(screenshot_file), full_page=True)
                result["screenshot_path"] = str(screenshot_file)

            result["ok"] = True

            context.close()
            browser.close()
    except Exception as exc:
        result["ok"] = False
        result["reason"] = f"probe_error:{type(exc).__name__}"

    return result
