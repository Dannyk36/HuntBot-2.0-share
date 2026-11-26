"""HuntBot 2.0 MCP server — exposes pipeline stages as MCP tools.

Run via:  huntbot2-mcp
Or:       python -m huntbot2.mcp.server

All tools run without consuming the inner LLM API by default (use_llm=False).
The outer MCP agent (e.g. Claude Code) receives structured results and makes
decisions — no OpenRouter credits needed for tool invocations.
"""
from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, UTC
from pathlib import Path


def _db_path() -> Path:
    env_path = os.getenv("HUNTBOT2_DB_PATH")
    if env_path:
        return Path(env_path)
    return Path(__file__).resolve().parents[2] / "data" / "huntbot2.db"


def _project_root() -> Path:
    env_root = os.getenv("HUNTBOT2_DATA_ROOT")
    if env_root:
        return Path(env_root)
    return Path(__file__).resolve().parents[2]


# ── Tool implementations ──────────────────────────────────────────────────────

async def _tool_huntbot_status(_args: dict) -> dict:
    db = _db_path()
    if not db.exists():
        return {"discovered": 0, "scored": 0, "tailored": 0, "queued": 0, "applied": 0,
                "apply_queue_pending": 0, "llm_enabled": False, "llm_ready": False,
                "llm_provider": None, "last_run": None}

    conn = sqlite3.connect(db)
    try:
        def _count(where: str, params: tuple = ()) -> int:
            return conn.execute(f"SELECT COUNT(*) FROM jobs WHERE {where}", params).fetchone()[0]
        discovered = _count("1=1")
        scored = _count("status = 'scored'")
        tailored = _count("status = 'tailored'")
        applied = _count("status = 'applied'")
        # "queued" is the job-pipeline stage (matches the cockpit funnel).
        queued = _count("status = 'queued'")
        # The apply routing queue is a separate axis — report it distinctly so
        # the two counts are never conflated.
        try:
            apply_queue_pending = conn.execute(
                "SELECT COUNT(*) FROM queue_items WHERE status = 'pending'"
            ).fetchone()[0]
        except Exception:
            apply_queue_pending = 0
        try:
            last_run_row = conn.execute(
                "SELECT started_at FROM runs ORDER BY id DESC LIMIT 1"
            ).fetchone()
            last_run = last_run_row[0] if last_run_row else None
        except Exception:
            last_run = None
    finally:
        conn.close()

    from huntbot2.llm.client import llm_health
    health = llm_health()
    return {
        "discovered": discovered, "scored": scored, "tailored": tailored,
        "queued": queued, "applied": applied,
        "apply_queue_pending": apply_queue_pending,
        "llm_enabled": health["enabled"],
        "llm_ready": health["ready"],
        "llm_provider": health["provider"],
        "last_run": last_run,
    }


async def _tool_huntbot_get_jobs(args: dict) -> list:
    status_filter = args.get("status")
    limit = int(args.get("limit", 50))
    db = _db_path()
    if not db.exists():
        return []
    conn = sqlite3.connect(db)
    try:
        if status_filter:
            rows = conn.execute(
                "SELECT id, title, company, location, url, status, score "
                "FROM jobs WHERE status = ? ORDER BY score DESC NULLS LAST LIMIT ?",
                (status_filter, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT id, title, company, location, url, status, score "
                "FROM jobs ORDER BY score DESC NULLS LAST LIMIT ?",
                (limit,),
            ).fetchall()
    finally:
        conn.close()
    return [
        {"job_id": r[0], "title": r[1], "company": r[2], "location": r[3],
         "url": r[4], "status": r[5], "score": r[6]}
        for r in rows
    ]


async def _tool_huntbot_discover(args: dict) -> dict:
    import asyncio
    from huntbot2.automation.pipeline import _stage_discover
    from huntbot2.db.schema import init_db
    root = _project_root()
    init_db(root / "data" / "huntbot2.db")
    portals = args.get("portals")
    prev_portals = os.environ.get("HUNTBOT2_PORTALS")
    if portals:
        os.environ["HUNTBOT2_PORTALS"] = json.dumps(portals)
    try:
        result = await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: _stage_discover(root, "headed", False),
        )
    finally:
        if portals:
            if prev_portals is None:
                os.environ.pop("HUNTBOT2_PORTALS", None)
            else:
                os.environ["HUNTBOT2_PORTALS"] = prev_portals
    return {
        "jobs_found": result.get("jobs_discovered", 0),
        "new": result.get("inserted", 0),
        "duplicate": result.get("skipped", 0),
        "source": result.get("source", "fixture"),
        "portals": portals or "all",
    }


async def _tool_huntbot_score(args: dict) -> dict:
    import asyncio
    from huntbot2.automation.pipeline import _stage_score
    from huntbot2.db.schema import init_db
    root = _project_root()
    init_db(root / "data" / "huntbot2.db")
    # MCP always runs rule-based (no inner LLM API) unless caller opts in
    use_llm = bool(args.get("use_llm", False))
    llm_client = None
    if use_llm:
        from huntbot2.llm.client import client_from_env
        llm_client = client_from_env()
    result = await asyncio.get_event_loop().run_in_executor(
        None, lambda: _stage_score(root, "headed", False, llm_client=llm_client)
    )
    db = _db_path()
    conn = sqlite3.connect(db)
    try:
        rows = conn.execute(
            "SELECT id, title, company, score FROM jobs "
            "WHERE status = 'scored' ORDER BY score DESC LIMIT 20"
        ).fetchall()
    finally:
        conn.close()
    shortlist = [{"job_id": r[0], "title": r[1], "company": r[2], "score": r[3]} for r in rows]
    return {
        "scored": result.get("shortlist_count", 0),
        "shortlist": shortlist,
        "shortlist_path": result.get("shortlist_path"),
    }


async def _tool_huntbot_tailor(args: dict) -> dict:
    import asyncio
    from huntbot2.automation.pipeline import _stage_tailor
    from huntbot2.db.schema import init_db
    root = _project_root()
    init_db(root / "data" / "huntbot2.db")
    use_llm = bool(args.get("use_llm", False))
    llm_client = None
    if use_llm:
        from huntbot2.llm.client import client_from_env
        llm_client = client_from_env()
    result = await asyncio.get_event_loop().run_in_executor(
        None, lambda: _stage_tailor(root, "headed", False, llm_client=llm_client)
    )
    return result


def _playwright_available() -> bool:
    try:
        import playwright.sync_api  # noqa: F401
        return True
    except ImportError:
        return False


def _chromium_binary_exists() -> bool:
    import glob as _glob
    patterns = [
        str(Path.home() / ".cache/ms-playwright/chromium-*/chrome-linux/chrome"),
        str(Path.home() / ".cache/ms-playwright/chromium-*/chrome-linux64/chrome"),
        str(Path.home() / ".cache/ms-playwright/chromium-*/chrome-win/chrome.exe"),
        str(Path.home() / ".cache/ms-playwright/chromium-*/chrome-mac/Chromium.app/Contents/MacOS/Chromium"),
    ]
    return any(_glob.glob(p) for p in patterns)


async def _tool_huntbot_health(_args: dict) -> dict:
    from huntbot2.apply.runner import normalize_submit_policy
    from huntbot2.config.env_store import get as env_get

    db = _db_path()
    pw_installed = _playwright_available()
    chromium_ready = pw_installed and _chromium_binary_exists()

    llm_enabled = (env_get("HUNTBOT2_LLM_ENABLED", "false") or "false").lower() == "true"
    llm_provider = env_get("HUNTBOT2_LLM_PROVIDER") or None
    llm_key_set = bool(
        env_get("HUNTBOT2_LLM_API_KEY")
        or os.getenv("ANTHROPIC_API_KEY")
        or os.getenv("OPENAI_API_KEY")
        or os.getenv("OPENROUTER_API_KEY")
    )

    policy_raw = env_get("HUNTBOT2_SUBMIT_POLICY", "never_submit") or "never_submit"
    policy = normalize_submit_policy(policy_raw)

    stale_count = 0
    if db.exists():
        conn = sqlite3.connect(db)
        try:
            stale_count = conn.execute("SELECT COUNT(*) FROM jobs WHERE status='stale_url'").fetchone()[0]
        except Exception:
            pass
        finally:
            conn.close()

    return {
        "db_exists": db.exists(),
        "playwright_installed": pw_installed,
        "chromium_ready": chromium_ready,
        "llm_enabled": llm_enabled,
        "llm_provider": llm_provider,
        "llm_key_configured": llm_key_set,
        "submit_policy": policy,
        "submit_token_required": policy == "allow_submit_with_token",
        "stale_url_jobs": stale_count,
        "ready_to_apply": db.exists() and pw_installed and chromium_ready,
    }


async def _tool_huntbot_apply(args: dict) -> dict:
    import asyncio
    from huntbot2.apply.runner import normalize_submit_policy, run_apply_demo
    from huntbot2.config.env_store import get as env_get

    if not _playwright_available():
        return {
            "status": "no_browser",
            "gate_decision": "blocked",
            "notes": "Playwright not installed — run: pip install playwright && playwright install chromium",
        }
    if not _chromium_binary_exists():
        return {
            "status": "no_browser",
            "gate_decision": "blocked",
            "notes": "Playwright installed but Chromium binary missing — run: playwright install chromium",
        }

    root = _project_root()
    policy_raw = args.get("policy") or env_get("HUNTBOT2_SUBMIT_POLICY", "never_submit") or "never_submit"
    policy = normalize_submit_policy(policy_raw)
    submit_token = args.get("submit_token")
    try:
        result = await asyncio.get_event_loop().run_in_executor(
            None, lambda: run_apply_demo(
                project_dir=root, mode="headed", dry_run=False, submit_policy=policy, submit_token=submit_token
            )
        )
        return {
            "gate_decision": result.gate_decision,
            "submit_policy": result.submit_policy,
            "status": result.status,
            "notes": result.notes,
        }
    except Exception as exc:
        return {"status": "error", "gate_decision": "blocked", "notes": str(exc)}


async def _tool_huntbot_simulate_apply(_args: dict) -> dict:
    """Dry-run apply: inspects the next queued job and reports what would happen, never submits."""
    import asyncio
    from huntbot2.apply.runner import run_apply_demo

    if not _playwright_available():
        return {
            "status": "no_browser",
            "gate_decision": "blocked",
            "notes": "Playwright not installed — run: pip install playwright && playwright install chromium",
        }
    if not _chromium_binary_exists():
        return {
            "status": "no_browser",
            "gate_decision": "blocked",
            "notes": "Playwright installed but Chromium binary missing — run: playwright install chromium",
        }

    root = _project_root()
    try:
        result = await asyncio.get_event_loop().run_in_executor(
            None, lambda: run_apply_demo(
                project_dir=root, mode="headed", dry_run=True, submit_policy="never_submit"
            )
        )
        return {
            "simulation": True,
            "gate_decision": result.gate_decision,
            "submit_policy": "never_submit",
            "status": result.status,
            "notes": result.notes,
        }
    except Exception as exc:
        return {"simulation": True, "status": "error", "gate_decision": "blocked", "notes": str(exc)}


async def _tool_huntbot_run(args: dict) -> dict:
    import asyncio
    from huntbot2.apply.runner import normalize_submit_policy
    from huntbot2.automation.pipeline import run_pipeline_now
    from huntbot2.config.env_store import get as env_get
    root = _project_root()
    use_llm = bool(args.get("use_llm", False))
    policy_raw = args.get("policy") or env_get("HUNTBOT2_SUBMIT_POLICY", "stop_before_submit") or "stop_before_submit"
    submit_policy = normalize_submit_policy(policy_raw)
    submit_token = args.get("submit_token")
    portals = args.get("portals")
    prev_portals = os.environ.get("HUNTBOT2_PORTALS")
    if portals:
        os.environ["HUNTBOT2_PORTALS"] = json.dumps(portals)
    try:
        result = await asyncio.get_event_loop().run_in_executor(
            None, lambda: run_pipeline_now(
                str(root), mode="headed", dry_run=False,
                submit_policy=submit_policy, submit_token=submit_token, use_llm=use_llm,
            )
        )
    finally:
        if portals:
            if prev_portals is None:
                os.environ.pop("HUNTBOT2_PORTALS", None)
            else:
                os.environ["HUNTBOT2_PORTALS"] = prev_portals
    return {
        "run_id": result["run_id"],
        "status": result["status"],
        "stages": result["stages"],
        "digest_path": result.get("digest_path"),
        "portals": portals or "all",
    }


async def _tool_huntbot_add_url(args: dict) -> dict:
    url = (args.get("url") or "").strip()
    if not url.startswith(("http://", "https://")):
        return {"error": "invalid_url", "message": "URL must start with http:// or https://"}
    db = _db_path()
    if not db.exists():
        from huntbot2.db.schema import init_db
        init_db(db)
    # Try Playwright extraction for title/company; fall back silently
    info: dict = {}
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            page = browser.new_page()
            page.goto(url, timeout=15_000, wait_until="domcontentloaded")
            info["title"] = page.title() or url
            for sel in ["[data-company-name]", ".topcard__org-name-link"]:
                el = page.query_selector(sel)
                if el:
                    info["company"] = (el.inner_text() or "").strip()
                    break
            browser.close()
    except Exception:
        info["title"] = url

    title = info.get("title") or url
    company = info.get("company") or ""
    conn = sqlite3.connect(db)
    try:
        existing = conn.execute("SELECT id FROM jobs WHERE url = ?", (url,)).fetchone()
        if existing:
            return {"job_id": existing[0], "title": title, "company": company, "duplicate": True}
        now = datetime.now(UTC).isoformat()
        cur = conn.execute(
            "INSERT INTO jobs (source, title, company, location, url, created_at, status) VALUES (?,?,?,?,?,?,?)",
            ("manual", title, company, "", url, now, "discovered"),
        )
        conn.commit()
        return {"job_id": cur.lastrowid, "title": title, "company": company, "status": "discovered"}
    finally:
        conn.close()


# ── MCP server wiring ─────────────────────────────────────────────────────────

_TOOL_HANDLERS = {
    "huntbot_health":          _tool_huntbot_health,
    "huntbot_status":          _tool_huntbot_status,
    "huntbot_get_jobs":        _tool_huntbot_get_jobs,
    "huntbot_add_url":         _tool_huntbot_add_url,
    "huntbot_discover":        _tool_huntbot_discover,
    "huntbot_score":           _tool_huntbot_score,
    "huntbot_tailor":          _tool_huntbot_tailor,
    "huntbot_simulate_apply":  _tool_huntbot_simulate_apply,
    "huntbot_apply":           _tool_huntbot_apply,
    "huntbot_run":             _tool_huntbot_run,
}

_SUBMIT_POLICY_PROP = {
    "type": "string",
    "enum": ["never_submit", "allow_submit_with_token"],
    "description": (
        "Submit policy. never_submit (default) stops before clicking Submit — safe for review. "
        "allow_submit_with_token actually submits and requires submit_token. "
        "Runtime accepts legacy aliases, but MCP clients should send canonical values."
    ),
}

_TOOL_SCHEMAS = [
    {
        "name": "huntbot_health",
        "description": (
            "Check HuntBot dependencies and configuration. Reports: DB exists, Playwright installed, "
            "Chromium binary ready, LLM configured, active submit policy, stale-URL job count. "
            "Call this before huntbot_apply if you are unsure whether the browser stack is available."
        ),
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "huntbot_status",
        "description": "Show pipeline funnel counts (discovered/scored/tailored/applied) and LLM mode. Call this first to understand current state.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "huntbot_get_jobs",
        "description": "List jobs from the database, optionally filtered by status. Returns job_id, title, company, location, url, status, score.",
        "inputSchema": {"type": "object", "properties": {
            "status": {"type": "string", "description": "Filter by status: discovered | scored | tailored | applied"},
            "limit":  {"type": "integer", "default": 50, "description": "Max jobs to return (default 50)"},
        }},
    },
    {
        "name": "huntbot_add_url",
        "description": "Add a specific job page URL to the pipeline. Extracts title/company via Playwright if available.",
        "inputSchema": {"type": "object", "properties": {
            "url": {"type": "string", "description": "Full job page URL (must start with http/https)"},
        }, "required": ["url"]},
    },
    {
        "name": "huntbot_discover",
        "description": "Scrape job search pages and insert new jobs. Runs rule-based (no inner LLM) by default. Uses live browser if HUNTBOT2_LIVE_DISCOVER=true, else uses fixture HTML.",
        "inputSchema": {"type": "object", "properties": {
            "portals": {"type": "array", "items": {"type": "string"},
                        "description": "Platforms to discover from, e.g. [\"indeed\",\"glassdoor\",\"linkedin\",\"handshake\"]. Omit for all configured portals."},
        }},
    },
    {
        "name": "huntbot_score",
        "description": "Score all unscored jobs against the candidate profile using keyword rules. No inner LLM API used by default. Returns shortlist of matching jobs.",
        "inputSchema": {"type": "object", "properties": {
            "use_llm": {"type": "boolean", "default": False, "description": "Use LLM for richer scoring (consumes API credits)"},
        }},
    },
    {
        "name": "huntbot_tailor",
        "description": "Generate a tailored resume + cover letter for the highest-scored untailored job. Runs rule-based by default (no LLM credits consumed).",
        "inputSchema": {"type": "object", "properties": {
            "use_llm": {"type": "boolean", "default": False, "description": "Use LLM for better tailoring (consumes API credits)"},
        }},
    },
    {
        "name": "huntbot_simulate_apply",
        "description": (
            "Dry-run apply: opens the next queued job in a headless browser and reports what would happen "
            "— apply path detected, page signals, gate decision — without ever clicking Submit. "
            "Use this to audit before committing to huntbot_apply."
        ),
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "huntbot_apply",
        "description": (
            "Attempt to apply to the next queued job using a headless browser. "
            "Returns status=no_browser if Playwright/Chromium is unavailable (check huntbot_health). "
            "Default policy is never_submit — safe to call without submit_token."
        ),
        "inputSchema": {"type": "object", "properties": {
            "policy": _SUBMIT_POLICY_PROP,
            "submit_token": {"type": "string", "description": "Required when the policy allows submit. Provided out-of-band by the human operator; not derivable from this schema."},
        }},
    },
    {
        "name": "huntbot_run",
        "description": "Run the full pipeline (discover → score → tailor → apply) in one call. Rule-based by default. Returns stage-by-stage results.",
        "inputSchema": {"type": "object", "properties": {
            "use_llm": {"type": "boolean", "default": False, "description": "Enable LLM for scoring/tailoring stages"},
            "policy": _SUBMIT_POLICY_PROP,
            "submit_token": {"type": "string", "description": "Required when the policy allows submit. Provided out-of-band by the human operator; not derivable from this schema."},
            "portals": {"type": "array", "items": {"type": "string"},
                        "description": "Platforms to target, e.g. [\"indeed\",\"glassdoor\",\"linkedin\",\"handshake\"]. Omit for all configured portals."},
        }},
    },
]


def main() -> None:
    """Entry point: start the MCP server on stdio."""
    import asyncio

    try:
        from mcp.server import Server
        from mcp.server.models import InitializationOptions
        from mcp.server import NotificationOptions
        import mcp.server.stdio
        import mcp.types as types
    except ImportError:
        raise SystemExit(
            "mcp SDK not installed. Run: pip install 'mcp>=1.0'\n"
            "Or: pip install -e '.[mcp]' from the project root"
        )

    server = Server("huntbot2")

    @server.list_tools()
    async def list_tools() -> list[types.Tool]:
        return [
            types.Tool(
                name=t["name"],
                description=t["description"],
                inputSchema=t["inputSchema"],
            )
            for t in _TOOL_SCHEMAS
        ]

    @server.call_tool()
    async def call_tool(name: str, arguments: dict) -> list[types.TextContent]:
        handler = _TOOL_HANDLERS.get(name)
        if handler is None:
            return [types.TextContent(type="text", text=json.dumps({"error": f"Unknown tool: {name}"}))]
        try:
            result = await handler(arguments or {})
            return [types.TextContent(type="text", text=json.dumps(result, default=str))]
        except Exception as exc:
            return [types.TextContent(type="text", text=json.dumps({"error": str(exc)}))]

    async def run() -> None:
        async with mcp.server.stdio.stdio_server() as (read_stream, write_stream):
            await server.run(
                read_stream,
                write_stream,
                InitializationOptions(
                    server_name="huntbot2",
                    server_version="0.1.0",
                    capabilities=server.get_capabilities(
                        notification_options=NotificationOptions(),
                        experimental_capabilities={},
                    ),
                ),
            )

    asyncio.run(run())


if __name__ == "__main__":
    main()
