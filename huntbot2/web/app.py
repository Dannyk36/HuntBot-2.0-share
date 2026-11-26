from __future__ import annotations

from pathlib import Path
import os
import json
import asyncio
import shutil
import subprocess
import time
import threading
from datetime import UTC, datetime

from huntbot2.config.env_store import (
    get as env_get,
    set as env_set,
    load_all as env_load_all,
)

_SETTINGS_MAP: dict[str, str] = {
    "llm_enabled": "HUNTBOT2_LLM_ENABLED",
    "llm_provider": "HUNTBOT2_LLM_PROVIDER",
    "scoring_model": "HUNTBOT2_SCORING_MODEL",
    "tailoring_model": "HUNTBOT2_TAILORING_MODEL",
    "anthropic_api_key": "ANTHROPIC_API_KEY",
    "openai_api_key": "OPENAI_API_KEY",
    "openrouter_api_key": "OPENROUTER_API_KEY",
    "submit_policy": "HUNTBOT2_SUBMIT_POLICY",
    "theme": "HUNTBOT2_THEME",
    "min_score": "HUNTBOT2_MIN_SCORE",
    "browser_mode": "HUNTBOT2_BROWSER_MODE",
    "discovery_urls": "HUNTBOT2_DISCOVERY_URLS",
    "live_discover": "HUNTBOT2_LIVE_DISCOVER",
    "portals": "HUNTBOT2_PORTALS",
    "discovery_model": "HUNTBOT2_DISCOVERY_MODEL",
    "discover_max_jobs": "HUNTBOT2_DISCOVER_MAX_JOBS",
    "discover_max_urls": "HUNTBOT2_DISCOVER_MAX_URLS",
    "discover_max_actions": "HUNTBOT2_DISCOVER_MAX_ACTIONS",
}

_pipeline_runs: dict[str, dict] = {}
_pipeline_runs_lock = threading.Lock()

import sqlite3

import socket

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, StreamingResponse, JSONResponse
import secrets
import base64
from pydantic import BaseModel, Field

from huntbot2.web.repository import (
    CockpitRepository,
    GuardrailPolicy,
    PLATFORMS,
    RESUME_STEPS,
    RISKY_RESUME_STEPS,
    SESSION_WORKFLOW_STATES,
    TAKEOVER_REASON_CODES,
    PACING_PRESETS,
    REMEDIATION_ALLOWED_ACTIONS,
    REMEDIATION_PLATFORM_GLOBAL,
)
from huntbot2.web.alerts import configured_channels, should_send_webhook, send_webhook_notification


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _central_db_path() -> Path:
    """The shared DB holding accounts/sessions and single-user data. Always the
    repository-level DB, regardless of any active user workspace."""
    configured = os.getenv("HUNTBOT2_DB_PATH", str(_repo_root() / "data" / "huntbot2.db"))
    return Path(configured)


def _project_root() -> Path:
    from huntbot2.config.workspace import get_workspace
    ws = get_workspace()
    return ws if ws is not None else _repo_root()


def _db_path() -> Path:
    from huntbot2.config.workspace import get_workspace
    ws = get_workspace()
    if ws is not None:
        return ws / "data" / "huntbot2.db"
    return _central_db_path()


def _screenshot_dir() -> Path:
    configured = os.getenv("HUNTBOT2_SCREENSHOT_DIR", str(_project_root() / "artifacts" / "live"))
    return Path(configured)


from contextlib import asynccontextmanager


@asynccontextmanager
async def _lifespan(_app: "FastAPI"):
    # Start the scheduled auto-apply loop unless explicitly disabled (it is a
    # no-op until the operator enables scheduling, so this is safe by default).
    if not _truthy(os.getenv("HUNTBOT2_DISABLE_SCHEDULER")):
        threading.Thread(target=_scheduler_loop, daemon=True).start()
    try:
        yield
    finally:
        _scheduler_stop.set()


app = FastAPI(title="HuntBot2 Ops Cockpit", version="0.5.0", lifespan=_lifespan)
repo = CockpitRepository(_db_path())
_NOVNC_PORT = int(os.getenv("HUNTBOT2_NOVNC_PORT", "6080"))
_VNC_PORT = int(os.getenv("HUNTBOT2_VNC_PORT", "5900"))
_XVFB_DISPLAY = os.getenv("HUNTBOT2_XVFB_DISPLAY", ":99")
_PLATFORM_URLS = {
    "linkedin": "https://www.linkedin.com/jobs/",
    "indeed": "https://www.indeed.com/",
    "glassdoor": "https://www.glassdoor.com/Job/",
    "handshake": "https://app.joinhandshake.com/stu/postings",
}
_browser_procs: dict[str, dict] = {}

# noVNC static files path (served via explicit route below)
_NOVNC_STATIC = os.getenv("HUNTBOT2_NOVNC_STATIC", "/snap/novnc/170")


def _port_open(port: int, host: str = "127.0.0.1", timeout: float = 0.5) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except Exception:
        return False


def _display_lock_exists(display: str = ":99") -> bool:
    return os.path.exists(f"/tmp/.X{display.lstrip(':')}-lock")


def _novnc_available() -> tuple[bool, list[str]]:
    # noVNC static files are served by FastAPI directly; only Xvfb + x11vnc are required
    missing = [cmd for cmd in ("Xvfb", "x11vnc") if not shutil.which(cmd)]
    return len(missing) == 0, missing


policy = GuardrailPolicy(repo)
AUTH_FAILURE_TIMESTAMPS: list[datetime] = []


def _run_remediation_playbook(
    *,
    alert: dict[str, object],
    platform_scope: str,
    dry_run: bool,
    trigger_mode: str,
) -> dict[str, object]:
    gate = repo.remediation_gate(alert_type=str(alert["alert_type"]), platform_scope=platform_scope)
    actions: list[dict[str, object]] = []
    notes = ""
    status = "skipped"

    if not gate["allowed"]:
        notes = f"policy_blocked:{','.join(gate['reasons'])}"
    else:
        actions = [
            {"action": "pause_scheduler", "platform_scope": platform_scope},
            {"action": "force_checkpoint_required", "platform_scope": platform_scope},
            {"action": "downgrade_pacing_safe", "platform_scope": platform_scope},
            {"action": "open_takeover_request", "platform_scope": platform_scope},
        ]
        if dry_run:
            status = "dry_run"
            notes = "dry-run only; no state mutation"
        else:
            targets = list(PLATFORMS) if platform_scope == REMEDIATION_PLATFORM_GLOBAL else [platform_scope]
            for platform in targets:
                repo.update_scheduler_state(platform=platform, paused=True, note=f"auto-remediation alert={alert['alert_type']}")
                repo.set_platform_pacing(platform=platform, preset="safe", updated_by="remediation_engine", note=f"alert={alert['alert_type']}")
                repo.request_takeover(
                    platform=platform,
                    session_key="default",
                    reason_code="manual_review",
                    note=f"auto-remediation for alert {alert['alert_type']}",
                )
                repo.record_operator_event(
                    "remediation_action",
                    actor="remediation_engine",
                    platform=platform,
                    session_key="default",
                    details={"alert_id": alert["id"], "alert_type": alert["alert_type"], "actions": list(REMEDIATION_ALLOWED_ACTIONS)},
                )
            repo.update_policy_settings(require_human_checkpoint=True)
            status = "executed"
            notes = "applied safe actions; never auto-submit"

    run = repo.create_remediation_run(
        alert_id=int(alert["id"]),
        alert_type=str(alert["alert_type"]),
        platform_scope=platform_scope,
        trigger_mode=trigger_mode,
        dry_run=dry_run,
        status=status,
        notes=notes,
        actions=actions,
    )
    run["gate"] = gate
    return run


def _emit_alert(
    *,
    severity: str,
    alert_type: str,
    dedupe_key: str,
    message: str,
    meta: dict[str, object] | None = None,
) -> dict[str, object]:
    alert = repo.create_alert(
        severity=severity,
        alert_type=alert_type,
        dedupe_key=dedupe_key,
        message=message,
        meta=meta,
    )
    channel_results = {"in_app": {"sent": True}}
    if should_send_webhook():
        channel_results["webhook"] = send_webhook_notification(alert)
    alert["channels"] = configured_channels()
    alert["channel_results"] = channel_results

    platform_scope = str((meta or {}).get("platform") or REMEDIATION_PLATFORM_GLOBAL)
    if platform_scope != REMEDIATION_PLATFORM_GLOBAL and platform_scope not in PLATFORMS:
        platform_scope = REMEDIATION_PLATFORM_GLOBAL
    # Only auto-run remediation on FIRST occurrence; subsequent dedupes
    # (occurrence_count > 1) would otherwise spam the runs table each time
    # the dashboard polls /api/browser/health.
    if int(alert.get("occurrence_count") or 1) <= 1:
        alert["remediation"] = _run_remediation_playbook(
            alert=alert,
            platform_scope=platform_scope,
            dry_run=False,
            trigger_mode="auto",
        )
    return alert


def _track_auth_failure_and_maybe_alert(path: str) -> None:
    now = datetime.now(UTC)
    window_seconds = int(os.getenv("HUNTBOT2_ALERT_AUTH_BURST_WINDOW_SECONDS", "120"))
    threshold = int(os.getenv("HUNTBOT2_ALERT_AUTH_FAILURE_BURST_THRESHOLD", "5"))
    cutoff = now.timestamp() - window_seconds
    AUTH_FAILURE_TIMESTAMPS.append(now)
    recent = [t for t in AUTH_FAILURE_TIMESTAMPS if t.timestamp() >= cutoff]
    AUTH_FAILURE_TIMESTAMPS.clear()
    AUTH_FAILURE_TIMESTAMPS.extend(recent)
    if len(recent) >= threshold:
        _emit_alert(
            severity="critical",
            alert_type="auth_failures_burst",
            dedupe_key="auth_failures_burst",
            message=f"Auth failures burst detected ({len(recent)} in {window_seconds}s)",
            meta={"path": path, "count": len(recent), "window_seconds": window_seconds},
        )


def _truthy(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _auth_bypass_enabled() -> bool:
    return _truthy(os.getenv("HUNTBOT2_COCKPIT_AUTH_BYPASS"))


# Hosts treated as local when no cockpit auth is configured. Includes the
# Starlette/FastAPI TestClient host so the test suite and local dev keep
# working, while remote callers to an unconfigured cockpit are denied.
_LOOPBACK_HOSTS = {"127.0.0.1", "::1", "localhost", "testclient"}


def _verify_auth(request: Request) -> bool:
    if _auth_bypass_enabled():
        return True

    expected_token = os.getenv("HUNTBOT2_COCKPIT_AUTH_TOKEN")
    expected_user = os.getenv("HUNTBOT2_COCKPIT_AUTH_USER")
    expected_pass = os.getenv("HUNTBOT2_COCKPIT_AUTH_PASSWORD")
    auth_header = request.headers.get("authorization", "")

    if expected_token:
        if auth_header.lower().startswith("bearer "):
            token = auth_header.split(" ", 1)[1].strip()
            return secrets.compare_digest(token, expected_token)
        token = request.headers.get("x-cockpit-token", "")
        return bool(token) and secrets.compare_digest(token, expected_token)

    if expected_user and expected_pass:
        if not auth_header.lower().startswith("basic "):
            return False
        raw = auth_header.split(" ", 1)[1].strip()
        try:
            decoded = base64.b64decode(raw).decode("utf-8")
        except Exception:
            return False
        if ":" not in decoded:
            return False
        username, password = decoded.split(":", 1)
        return secrets.compare_digest(username, expected_user) and secrets.compare_digest(password, expected_pass)

    # No auth configured: secure by default — allow loopback only, deny remote
    # so an unconfigured cockpit is never exposed to the network.
    host = request.client.host if request.client else None
    return host in _LOOPBACK_HOSTS


# Endpoints reachable without any auth (so new users can sign in/up).
_PUBLIC_PATHS = {"/health", "/api/auth/login", "/api/auth/register", "/api/auth/signup-status"}


@app.middleware("http")
async def cockpit_auth_middleware(request: Request, call_next):
    if request.url.path in _PUBLIC_PATHS:
        return await call_next(request)

    # A valid user session both authenticates the request and scopes all data
    # access to that user's workspace.
    from huntbot2.config.workspace import reset_workspace, set_workspace
    from huntbot2.web.accounts import resolve_session
    user = resolve_session(_central_db_path(), request.cookies.get("hb_session"))
    if user is not None:
        request.state.user = user
        token = set_workspace(user["workspace_dir"])
        try:
            return await call_next(request)
        finally:
            reset_workspace(token)

    request.state.user = None
    if not _verify_auth(request):
        _track_auth_failure_and_maybe_alert(request.url.path)
        return JSONResponse(status_code=401, content={"detail": "unauthorized"}, headers={"WWW-Authenticate": "Basic"})
    return await call_next(request)


class TakeoverUpdate(BaseModel):
    takeover_requested: bool


class SessionWorkflowUpdate(BaseModel):
    state: str = Field(description="request_takeover|in_takeover|resumed|paused")
    reason_code: str | None = Field(default=None, description="login_required|captcha|mfa|manual_review")
    note: str | None = None


class TakeoverRequestPayload(BaseModel):
    reason_code: str = Field(description="login_required|captcha|mfa|manual_review")
    note: str | None = None


class ResumeRequestPayload(BaseModel):
    actor: str = "operator"


class ResumeStepUpdate(BaseModel):
    resume_step: str = Field(description="open_job|validate_page|fill_form|review|submit_gate")
    actor: str = "operator"
    note: str | None = None


class ScreenshotCaptureRequest(BaseModel):
    actor: str = "operator"
    reason: str = "manual_refresh"


class SchedulerUpdate(BaseModel):
    paused: bool | None = None
    window_start: str | None = None
    window_end: str | None = None
    timezone: str | None = None
    note: str | None = None
    actor: str = "operator"


class BrowserProfileUpdate(BaseModel):
    profile_path: str | None = None
    active: bool | None = None
    status: str | None = None
    note: str | None = None
    last_used_at: str | None = None


class PolicySettingsUpdate(BaseModel):
    daily_apply_cap: int | None = Field(default=None, ge=1, le=1000)
    min_seconds_between_actions: int | None = Field(default=None, ge=0, le=3600)
    require_human_checkpoint: bool | None = None
    actor: str = "operator"


class PolicyEvalRequest(BaseModel):
    event_type: str = Field(default="action")
    requires_submit: bool = False


class CheckpointRequest(BaseModel):
    approved: bool = True
    actor: str = "human"


class PlatformPacingUpdate(BaseModel):
    preset: str = Field(description="safe|balanced|aggressive")
    actor: str = "operator"
    note: str | None = None


class AlertStatusUpdate(BaseModel):
    actor: str = "operator"


class NotificationTestPayload(BaseModel):
    severity: str = "warning"
    alert_type: str = "notification_test"
    message: str = "HuntBot notification test"
    dedupe_key: str = "notification_test"


class RemediationPolicyUpdate(BaseModel):
    alert_type: str
    platform_scope: str = Field(description="linkedin|indeed|glassdoor|*")
    enabled: bool | None = None
    cooldown_minutes: int | None = Field(default=None, ge=0, le=1440)
    max_actions_per_day: int | None = Field(default=None, ge=1, le=100)
    actor: str = "operator"


class RemediationReplayRequest(BaseModel):
    platform_scope: str | None = Field(default=None, description="linkedin|indeed|glassdoor|*")
    actor: str = "operator"


class SettingsUpdateRequest(BaseModel):
    llm_enabled: bool | None = None
    llm_provider: str | None = None
    scoring_model: str | None = None
    tailoring_model: str | None = None
    anthropic_api_key: str | None = None
    openai_api_key: str | None = None
    openrouter_api_key: str | None = None
    submit_policy: str | None = None
    theme: str | None = None
    min_score: int | None = None
    browser_mode: str | None = None
    discovery_urls: list[str] | None = None
    live_discover: bool | None = None
    portals: list[str] | None = None
    discovery_model: str | None = None
    discover_max_jobs: int | None = None
    discover_max_urls: int | None = None
    discover_max_actions: int | None = None


class TestKeyRequest(BaseModel):
    provider: str
    key: str


class MasterResumeUpdate(BaseModel):
    content: str


class PipelineRunRequest(BaseModel):
    stage: str = Field(..., pattern="^(discover|score|tailor|apply|session|all)$")
    mode: str = Field("auto", pattern="^(auto|headless|headed)$")
    portals: list[str] | None = None  # override HUNTBOT2_PORTALS for this run


class AddUrlRequest(BaseModel):
    url: str


class ScheduleUpdate(BaseModel):
    enabled: bool | None = None
    interval_minutes: int | None = Field(default=None, ge=5, le=1440)
    jobs_per_run: int | None = Field(default=None, ge=0, le=100)
    window_start: str | None = Field(default=None, pattern=r"^\d{2}:\d{2}$")
    window_end: str | None = Field(default=None, pattern=r"^\d{2}:\d{2}$")
    timezone: str | None = None


class CalendarEntryCreate(BaseModel):
    day_of_week: int = Field(ge=0, le=6)
    time_hhmm: str = Field(pattern=r"^\d{2}:\d{2}$")
    platform: str
    jobs_per_run: int = Field(default=5, ge=0, le=100)
    mode: str = Field(default="supervised", pattern="^(supervised|automatic)$")
    enabled: bool = True


class CalendarEntryUpdate(BaseModel):
    day_of_week: int | None = Field(default=None, ge=0, le=6)
    time_hhmm: str | None = Field(default=None, pattern=r"^\d{2}:\d{2}$")
    platform: str | None = None
    jobs_per_run: int | None = Field(default=None, ge=0, le=100)
    mode: str | None = Field(default=None, pattern="^(supervised|automatic)$")
    enabled: bool | None = None


class ApplyUrlRequest(BaseModel):
    url: str
    mode: str = Field(default="supervised", pattern="^(supervised|automatic)$")


class RegisterRequest(BaseModel):
    username: str
    password: str
    display_name: str = ""


class LoginRequest(BaseModel):
    username: str
    password: str


class OnboardingSubmit(BaseModel):
    answers: dict[str, object]


class TemplateUpload(BaseModel):
    kind: str = Field(pattern="^(resume|cover)$")
    filename: str
    content_b64: str


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/queue/snapshot")
def queue_snapshot() -> dict[str, int]:
    return repo.queue_snapshot()


@app.get("/api/sessions/{session_key}/takeover")
def get_takeover(session_key: str) -> dict[str, object]:
    return repo.get_takeover_flag(session_key=session_key)


@app.post("/api/sessions/{session_key}/takeover")
def set_takeover(session_key: str, payload: TakeoverUpdate) -> dict[str, object]:
    return repo.set_takeover_flag(requested=payload.takeover_requested, session_key=session_key)


@app.get("/api/sessions/{session_key}/workflows")
def list_session_workflows(session_key: str) -> dict[str, object]:
    return {"session_key": session_key, "states": SESSION_WORKFLOW_STATES, "reasons": TAKEOVER_REASON_CODES, "workflows": repo.list_session_workflows(session_key=session_key)}


@app.get("/api/sessions/{session_key}/workflows/{platform}")
def get_session_workflow(session_key: str, platform: str) -> dict[str, object]:
    try:
        return repo.get_session_workflow(platform=platform, session_key=session_key)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/sessions/{session_key}/workflows/{platform}")
def update_session_workflow(session_key: str, platform: str, payload: SessionWorkflowUpdate) -> dict[str, object]:
    try:
        updated = repo.update_session_workflow(
            platform=platform,
            session_key=session_key,
            state=payload.state,
            reason_code=payload.reason_code,
            note=payload.note,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if payload.state in {"paused", "resumed"}:
        repo.record_operator_event(
            "pause" if payload.state == "paused" else "resume",
            actor="operator",
            platform=platform,
            session_key=session_key,
            details={"reason_code": payload.reason_code, "note": payload.note},
        )
    return updated


@app.post("/api/sessions/{session_key}/workflows/{platform}/request-takeover")
def request_takeover(session_key: str, platform: str, payload: TakeoverRequestPayload) -> dict[str, object]:
    try:
        updated = repo.request_takeover(
            platform=platform,
            session_key=session_key,
            reason_code=payload.reason_code,
            note=payload.note,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    repo.record_operator_event(
        "takeover_request",
        actor="operator",
        platform=platform,
        session_key=session_key,
        details={"reason_code": payload.reason_code, "note": payload.note},
    )
    return updated


@app.post("/api/sessions/{session_key}/workflows/{platform}/resume")
def resume_session(session_key: str, platform: str, payload: ResumeRequestPayload) -> dict[str, object]:
    try:
        workflow = repo.get_session_workflow(platform=platform, session_key=session_key)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if workflow["state"] not in {"request_takeover", "in_takeover", "paused"}:
        raise HTTPException(status_code=409, detail=f"cannot resume from state={workflow['state']}")

    profile = next(p for p in repo.list_browser_profiles() if p["platform"] == platform)
    if not profile["active"]:
        _emit_alert(
            severity="warning",
            alert_type="profile_inactive",
            dedupe_key=f"profile_inactive:{platform}",
            message=f"Resume blocked: profile inactive for {platform}",
            meta={"platform": platform, "session_key": session_key},
        )
        raise HTTPException(status_code=409, detail="profile inactive; cannot resume")

    guardrail = policy.evaluate(event_type="resume", requires_submit=True)
    if not guardrail["allowed"]:
        if "human_checkpoint_required" in guardrail["violations"]:
            _emit_alert(
                severity="warning",
                alert_type="checkpoint_required",
                dedupe_key=f"checkpoint_required:{platform}",
                message=f"Resume checkpoint required for {platform}",
                meta={"platform": platform, "session_key": session_key, "guardrail": guardrail},
            )
        _emit_alert(
            severity="critical",
            alert_type="resume_blocked_guardrail",
            dedupe_key=f"resume_blocked_guardrail:{platform}",
            message=f"Resume blocked by guardrail for {platform}",
            meta={"platform": platform, "session_key": session_key, "guardrail": guardrail},
        )
        raise HTTPException(status_code=409, detail={"message": "guardrail_blocked_resume", "guardrail": guardrail})

    repo.record_policy_event("action", meta={"event": "resume", "platform": platform, "actor": payload.actor})
    repo.record_operator_event("resume", actor=payload.actor, platform=platform, session_key=session_key, details={"workflow_state": workflow["state"]})
    return repo.update_session_workflow(
        platform=platform,
        session_key=session_key,
        state="resumed",
        note=f"resumed by {payload.actor}",
    )


@app.get("/api/sessions/{session_key}/resume-step/{platform}")
def get_resume_step(session_key: str, platform: str) -> dict[str, object]:
    try:
        step = repo.get_resume_step(platform=platform, session_key=session_key)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    step["allowed_steps"] = list(RESUME_STEPS)
    step["risky_steps"] = sorted(RISKY_RESUME_STEPS)
    return step


@app.post("/api/sessions/{session_key}/resume-step/{platform}")
def set_resume_step(session_key: str, platform: str, payload: ResumeStepUpdate) -> dict[str, object]:
    if payload.resume_step in RISKY_RESUME_STEPS:
        workflow = repo.get_session_workflow(platform=platform, session_key=session_key)
        if workflow["state"] not in {"in_takeover", "request_takeover"}:
            raise HTTPException(status_code=409, detail="risky resume step requires active takeover workflow")
        profile = next(p for p in repo.list_browser_profiles() if p["platform"] == platform)
        if not profile["active"]:
            raise HTTPException(status_code=409, detail="risky resume step requires active browser profile")
        guardrail = policy.evaluate(event_type="resume", requires_submit=True)
        if not guardrail["allowed"]:
            raise HTTPException(status_code=409, detail={"message": "guardrail_blocked_resume_step", "guardrail": guardrail})
    try:
        updated = repo.set_resume_step(
            platform=platform,
            session_key=session_key,
            resume_step=payload.resume_step,
            updated_by=payload.actor,
            note=payload.note,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    repo.record_operator_event(
        "resume_step_change",
        actor=payload.actor,
        platform=platform,
        session_key=session_key,
        details={"resume_step": payload.resume_step, "note": payload.note},
    )
    return updated


@app.get("/api/live/screenshot")
def screenshot_status() -> dict[str, object]:
    return repo.latest_screenshot(_screenshot_dir())


@app.get("/api/live/screenshot/stream")
async def screenshot_stream(continuous: bool = False) -> StreamingResponse:
    async def event_gen():
        last_filename = ""
        while True:
            shot = repo.latest_screenshot(_screenshot_dir())
            filename = str(shot.get("filename") or "")
            if filename != last_filename:
                yield f"event: screenshot\\ndata: {json.dumps(shot)}\\n\\n"
                last_filename = filename
                if not continuous:
                    break
            else:
                yield "event: heartbeat\\ndata: {}\\n\\n"
                if not continuous:
                    break
            await asyncio.sleep(1)

    return StreamingResponse(event_gen(), media_type="text/event-stream")


@app.post("/api/live/screenshot/capture")
def screenshot_capture(payload: ScreenshotCaptureRequest) -> dict[str, object]:
    trigger_file = os.getenv("HUNTBOT2_SCREENSHOT_TRIGGER_FILE")
    if trigger_file:
        Path(trigger_file).parent.mkdir(parents=True, exist_ok=True)
        Path(trigger_file).write_text(
            json.dumps({"actor": payload.actor, "reason": payload.reason, "requested_at": repo.policy_status()["settings"]["updated_at"]}),
            encoding="utf-8",
        )
    return {"accepted": True, "actor": payload.actor, "reason": payload.reason}


@app.get("/api/live/screenshot/{filename}")
def screenshot_file(filename: str) -> FileResponse:
    path = _screenshot_dir() / filename
    if not path.exists() or not path.is_file():
        raise HTTPException(status_code=404, detail="screenshot not found")
    return FileResponse(path)


@app.get("/api/browser/profiles")
def browser_profiles() -> dict[str, object]:
    return {"platforms": list(PLATFORMS), "profiles": repo.list_browser_profiles()}


@app.get("/api/browser/health")
def browser_health() -> dict[str, object]:
    summary = repo.session_health_summary()
    stale_hours = int(os.getenv("HUNTBOT2_ALERT_STALE_SESSION_HOURS", "12"))
    for item in summary["platforms"]:
        warning_codes = {w.get("code") for w in item.get("warnings", [])}
        if "profile_inactive" in warning_codes:
            _emit_alert(
                severity="warning",
                alert_type="profile_inactive",
                dedupe_key=f"profile_inactive:{item['platform']}",
                message=f"Profile inactive for {item['platform']}",
                meta={"platform": item["platform"]},
            )
        if "stale_last_used_at" in warning_codes:
            _emit_alert(
                severity="critical",
                alert_type="stale_session_warning_critical",
                dedupe_key=f"stale_session:{item['platform']}",
                message=f"Stale session warning for {item['platform']} (>{stale_hours}h)",
                meta={"platform": item["platform"], "last_used_at": item.get("last_used_at")},
            )
    return summary


@app.get("/api/scheduler")
def scheduler_state() -> dict[str, object]:
    return {"platforms": repo.list_scheduler_state()}


@app.post("/api/scheduler/{platform}")
def scheduler_update(platform: str, payload: SchedulerUpdate) -> dict[str, object]:
    try:
        updated = repo.update_scheduler_state(
            platform=platform,
            paused=payload.paused,
            window_start=payload.window_start,
            window_end=payload.window_end,
            timezone=payload.timezone,
            note=payload.note,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    event_type = "pause" if payload.paused is True else "resume" if payload.paused is False else "scheduler_change"
    if payload.paused is True:
        now_hhmm = datetime.now(UTC).strftime("%H:%M")
        if not (updated["window_start"] <= now_hhmm <= updated["window_end"]):
            _emit_alert(
                severity="warning",
                alert_type="scheduler_paused_outside_window",
                dedupe_key=f"scheduler_paused_outside_window:{platform}",
                message=f"Scheduler paused for {platform} outside window {updated['window_start']}-{updated['window_end']}",
                meta={"platform": platform, "now": now_hhmm, "window_start": updated["window_start"], "window_end": updated["window_end"]},
            )
    repo.record_operator_event(
        event_type,
        actor=payload.actor,
        platform=platform,
        details={
            "paused": payload.paused,
            "window_start": payload.window_start,
            "window_end": payload.window_end,
            "timezone": payload.timezone,
            "note": payload.note,
        },
    )
    return updated


@app.post("/api/browser/profiles/{platform}")
def browser_profile_update(platform: str, payload: BrowserProfileUpdate) -> dict[str, object]:
    try:
        return repo.upsert_browser_profile(
            platform=platform,
            profile_path=payload.profile_path,
            active=payload.active,
            status=payload.status,
            note=payload.note,
            last_used_at=payload.last_used_at,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/policy/status")
def policy_status() -> dict[str, object]:
    return repo.policy_status()


@app.get("/api/policy/settings")
def policy_settings() -> dict[str, object]:
    return repo.get_policy_settings()


@app.post("/api/policy/settings")
def policy_settings_update(payload: PolicySettingsUpdate) -> dict[str, object]:
    updated = repo.update_policy_settings(
        daily_apply_cap=payload.daily_apply_cap,
        min_seconds_between_actions=payload.min_seconds_between_actions,
        require_human_checkpoint=payload.require_human_checkpoint,
    )
    repo.record_operator_event(
        "policy_settings_change",
        actor=payload.actor,
        details={
            "daily_apply_cap": payload.daily_apply_cap,
            "min_seconds_between_actions": payload.min_seconds_between_actions,
            "require_human_checkpoint": payload.require_human_checkpoint,
        },
    )
    return updated


@app.post("/api/policy/evaluate")
def policy_evaluate(payload: PolicyEvalRequest) -> dict[str, object]:
    result = policy.evaluate(event_type=payload.event_type, requires_submit=payload.requires_submit)
    if "human_checkpoint_required" in result.get("violations", []):
        _emit_alert(
            severity="warning",
            alert_type="checkpoint_required",
            dedupe_key=f"checkpoint_required:{payload.event_type}",
            message=f"Checkpoint required for event_type={payload.event_type}",
            meta={"event_type": payload.event_type, "requires_submit": payload.requires_submit},
        )
    return result


@app.post("/api/policy/checkpoint")
def policy_checkpoint(payload: CheckpointRequest) -> dict[str, object]:
    if payload.approved:
        repo.record_policy_event("checkpoint_approved", meta={"actor": payload.actor})
    else:
        repo.record_policy_event("checkpoint_failed", meta={"actor": payload.actor})
        _emit_alert(
            severity="critical",
            alert_type="checkpoint_failed",
            dedupe_key="checkpoint_failed",
            message="Human checkpoint failed",
            meta={"actor": payload.actor},
        )
    repo.record_operator_event(
        "checkpoint_decision",
        actor=payload.actor,
        details={"approved": payload.approved},
    )
    return repo.policy_status()


@app.get("/api/operator/timeline")
def operator_timeline(
    limit: int = 50,
    event_type: str | None = None,
    platform: str | None = None,
    session_key: str | None = None,
    actor: str | None = None,
) -> dict[str, object]:
    return {
        "events": repo.list_operator_events(
            limit=limit,
            event_type=event_type,
            platform=platform,
            session_key=session_key,
            actor=actor,
        )
    }


@app.get("/api/pacing/presets")
def list_pacing_presets() -> dict[str, object]:
    return {"preset_options": list(PACING_PRESETS), "platforms": repo.list_platform_pacing()}


@app.get("/api/pacing/presets/{platform}")
def get_pacing_preset(platform: str) -> dict[str, object]:
    try:
        return repo.get_platform_pacing(platform)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/pacing/presets/{platform}")
def set_pacing_preset(platform: str, payload: PlatformPacingUpdate) -> dict[str, object]:
    try:
        updated = repo.set_platform_pacing(platform=platform, preset=payload.preset, updated_by=payload.actor, note=payload.note)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    repo.record_operator_event(
        "pacing_preset_change",
        actor=payload.actor,
        platform=platform,
        details={"preset": payload.preset, "note": payload.note},
    )
    return updated


@app.get("/api/alerts")
def list_alerts(status: str | None = None, severity: str | None = None, limit: int = 100) -> dict[str, object]:
    return {"alerts": repo.list_alerts(status=status, severity=severity, limit=limit)}


@app.post("/api/alerts/{alert_id}/ack")
def ack_alert(alert_id: int, payload: AlertStatusUpdate) -> dict[str, object]:
    try:
        updated = repo.update_alert_status(alert_id=alert_id, status="acked")
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    repo.record_operator_event("alert_acked", actor=payload.actor, details={"alert_id": alert_id})
    return updated


@app.post("/api/alerts/{alert_id}/resolve")
def resolve_alert(alert_id: int, payload: AlertStatusUpdate) -> dict[str, object]:
    try:
        updated = repo.update_alert_status(alert_id=alert_id, status="resolved")
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    repo.record_operator_event("alert_resolved", actor=payload.actor, details={"alert_id": alert_id})
    return updated


@app.post("/api/alerts/test-send")
def test_send_notification(payload: NotificationTestPayload) -> dict[str, object]:
    alert = _emit_alert(
        severity=payload.severity,
        alert_type=payload.alert_type,
        dedupe_key=payload.dedupe_key,
        message=payload.message,
        meta={"source": "test_send"},
    )
    return {"ok": True, "alert": alert}


@app.get("/api/remediation/runs")
def remediation_runs(limit: int = 100, alert_type: str | None = None, platform_scope: str | None = None) -> dict[str, object]:
    return {"runs": repo.list_remediation_runs(limit=limit, alert_type=alert_type, platform_scope=platform_scope)}


@app.get("/api/remediation/policies")
def remediation_policies(alert_type: str | None = None, platform_scope: str | None = None) -> dict[str, object]:
    return {"policies": repo.list_remediation_policies(alert_type=alert_type, platform_scope=platform_scope)}


@app.post("/api/remediation/policies")
def remediation_policy_update(payload: RemediationPolicyUpdate) -> dict[str, object]:
    try:
        updated = repo.upsert_remediation_policy(
            alert_type=payload.alert_type,
            platform_scope=payload.platform_scope,
            enabled=payload.enabled,
            cooldown_minutes=payload.cooldown_minutes,
            max_actions_per_day=payload.max_actions_per_day,
            updated_by=payload.actor,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    repo.record_operator_event(
        "remediation_policy_change",
        actor=payload.actor,
        platform=payload.platform_scope if payload.platform_scope in PLATFORMS else None,
        details=updated,
    )
    return updated


@app.post("/api/remediation/runs/{alert_id}/dry-run")
def remediation_dry_run(alert_id: int, payload: RemediationReplayRequest) -> dict[str, object]:
    try:
        alert = repo.get_alert(alert_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    inferred_scope = payload.platform_scope or str(alert.get("meta", {}).get("platform") or REMEDIATION_PLATFORM_GLOBAL)
    if inferred_scope != REMEDIATION_PLATFORM_GLOBAL and inferred_scope not in PLATFORMS:
        raise HTTPException(status_code=400, detail=f"unsupported platform_scope: {inferred_scope}")
    run = _run_remediation_playbook(alert=alert, platform_scope=inferred_scope, dry_run=True, trigger_mode="manual_dry_run")
    repo.record_operator_event(
        "remediation_dry_run",
        actor=payload.actor,
        platform=inferred_scope if inferred_scope in PLATFORMS else None,
        details={"alert_id": alert_id, "run_id": run["id"], "alert_type": alert["alert_type"]},
    )
    return run


@app.get("/api/ops/command-center")
def ops_command_center() -> dict[str, object]:
    return repo.ops_command_center_summary()


@app.get("/api/browser/novnc-status")
def browser_novnc_status() -> dict[str, object]:
    available, missing = _novnc_available()
    if not available:
        return {"available": False, "missing": missing}

    # Detect running state via port probing — survives server restarts
    # noVNC static files + WebSocket proxy run inside FastAPI, so only check x11vnc + Xvfb
    vnc_up = _port_open(_VNC_PORT)
    display_up = _display_lock_exists(_XVFB_DISPLAY)
    actually_running = vnc_up and display_up

    platforms: dict[str, object] = {}
    for platform in list(_PLATFORM_URLS):
        procs = _browser_procs.get(platform)
        proc_running = (
            procs is not None
            and procs.get("running", False)
            and all(
                p.poll() is None
                for p in [procs.get("xvfb"), procs.get("x11vnc")]
                if p is not None
            )
        )
        running = proc_running or actually_running
        # novnc proxied through this app — no separate port needed
        platforms[platform] = {"running": running, "proxied": True}
    return {"available": True, "platforms": platforms}


@app.post("/api/browser/launch/{platform}")
async def browser_launch(platform: str, request: Request) -> dict[str, object]:
    try:
        body = await request.json()
    except Exception:
        body = {}

    if platform not in _PLATFORM_URLS:
        raise HTTPException(status_code=422, detail=f"Unknown platform: {platform}")
    available, missing = _novnc_available()
    if not available:
        raise HTTPException(status_code=503, detail=f"noVNC not installed. Missing: {', '.join(missing)}")

    # Only block re-launch if processes are still alive in this process's memory
    procs = _browser_procs.get(platform)
    if procs and procs.get("running"):
        still_alive = all(
            p.poll() is None for p in [procs.get("xvfb"), procs.get("x11vnc")] if p is not None
        )
        if still_alive:
            raise HTTPException(status_code=409, detail=f"{platform} browser already running")

    # Kill any stale tracked processes
    stale = _browser_procs.pop(platform, None)
    if stale:
        for key in ("browser", "x11vnc", "xvfb"):
            p = stale.get(key)
            if p is not None:
                try:
                    p.terminate()
                except OSError:
                    pass

    # Strip Wayland vars so x11vnc and Chrome connect to Xvfb, not the host Wayland session
    env = {k: v for k, v in os.environ.items()
           if k not in ("WAYLAND_DISPLAY", "XDG_SESSION_TYPE", "WAYLAND_SOCKET")}
    env["DISPLAY"] = _XVFB_DISPLAY
    env["GDK_BACKEND"] = "x11"

    # Start Xvfb only if the display is not already running
    xvfb = None
    if not _display_lock_exists(_XVFB_DISPLAY):
        xvfb = subprocess.Popen(
            ["Xvfb", _XVFB_DISPLAY, "-screen", "0", "1280x800x24"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        await asyncio.sleep(0.8)

    # Start x11vnc only if VNC port is not already listening
    x11vnc = None
    if not _port_open(_VNC_PORT):
        x11vnc = subprocess.Popen(
            ["x11vnc", "-display", _XVFB_DISPLAY, "-rfbport", str(_VNC_PORT),
             "-nopw", "-forever", "-quiet", "-noxdamage"],
            env=env,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        await asyncio.sleep(0.5)

    profile_base = os.getenv(
        "HUNTBOT2_BROWSER_PROFILE_BASE",
        os.path.expanduser("~/.huntbot2/profiles"),
    )
    # Map platform names to the same profile directories the automation pipeline
    # uses, so VNC login sessions are immediately available to automation.
    _PROFILE_NAME_MAP = {
        "linkedin": "agent-linkedin",
        "handshake": "agent-linkedin",
    }
    profile_name = _PROFILE_NAME_MAP.get(platform, platform)
    profile_path = os.path.join(profile_base, profile_name)
    os.makedirs(profile_path, exist_ok=True)

    # Always use Playwright's bundled Chromium so the VNC login session and the
    # pipeline automation share the same binary and cookie encryption.  Using
    # the system Google Chrome here causes a binary mismatch: Chrome encrypts
    # cookies with the system keychain, Playwright's Chromium cannot decrypt
    # them and sees a blank session on every automated run.
    def _playwright_chromium_path() -> str:
        try:
            from playwright.sync_api import sync_playwright
            with sync_playwright() as _p:
                return _p.chromium.executable_path
        except Exception:
            return ""

    chromium = _playwright_chromium_path() or shutil.which("google-chrome") or "google-chrome"

    browser = subprocess.Popen(
        [chromium, f"--user-data-dir={profile_path}",
         "--no-sandbox", "--disable-dev-shm-usage",
         "--disable-gpu", "--start-maximized",
         "--disable-blink-features=AutomationControlled",
         "--password-store=basic",
         "--ozone-platform=x11",
         _PLATFORM_URLS[platform]],
        env=env,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )

    _browser_procs[platform] = {
        "xvfb": xvfb, "x11vnc": x11vnc,
        "browser": browser, "running": True,
    }

    actor = body.get("actor", "dashboard")
    # Sync the profile state so the Profiles/Health sections reflect reality
    from datetime import datetime as _dt, UTC as _UTC
    repo.upsert_browser_profile(
        platform=platform,
        active=True,
        status="active",
        last_used_at=_dt.now(_UTC).isoformat(),
    )
    repo.record_operator_event(
        "browser_launched", actor=actor, platform=platform,
        session_key="default",
        details={"profile_path": profile_path},
    )
    return {
        "status": "launched",
        "platform": platform,
    }


@app.post("/api/browser/stop/{platform}")
async def browser_stop(platform: str, request: Request) -> dict[str, object]:
    try:
        body = await request.json()
    except Exception:
        body = {}

    if platform not in _browser_procs:
        raise HTTPException(status_code=404, detail=f"{platform} browser not running")

    procs = _browser_procs.pop(platform)
    procs["running"] = False
    for key in ("browser", "x11vnc", "xvfb"):
        proc = procs.get(key)
        if proc is not None:
            try:
                proc.terminate()
            except OSError:
                pass

    actor = body.get("actor", "dashboard")
    # Mark the profile as idle again so Health/Profiles reflect it
    repo.upsert_browser_profile(
        platform=platform,
        active=False,
        status="idle",
    )
    repo.record_operator_event(
        "browser_stopped", actor=actor, platform=platform,
        session_key="default", details={},
    )
    return {"status": "stopped", "platform": platform}


@app.get("/novnc/{file_path:path}")
async def serve_novnc_static(file_path: str) -> FileResponse:
    """Serve noVNC static files through port 8095 so no extra port is needed."""
    if not file_path:
        file_path = "vnc.html"
    target = Path(_NOVNC_STATIC) / file_path
    if not target.is_file() or not target.resolve().is_relative_to(Path(_NOVNC_STATIC).resolve()):
        raise HTTPException(status_code=404, detail="Not found")
    return FileResponse(target)


@app.websocket("/novnc-ws")
async def novnc_ws_proxy(ws: WebSocket) -> None:
    """WebSocket-to-TCP bridge: browser noVNC ↔ x11vnc RFB on port 5900.

    Proxies through port 8095 so no extra ports need to be open on the server.
    """
    subproto = None
    requested = ws.headers.get("sec-websocket-protocol", "")
    for p in ("binary", "base64"):
        if p in requested:
            subproto = p
            break
    await ws.accept(subprotocol=subproto)

    try:
        reader, writer = await asyncio.open_connection("127.0.0.1", _VNC_PORT)
    except Exception:
        await ws.close(code=1011)
        return

    async def _browser_to_vnc() -> None:
        try:
            while True:
                data = await ws.receive_bytes()
                writer.write(data)
                await writer.drain()
        except Exception:
            pass

    async def _vnc_to_browser() -> None:
        try:
            while True:
                data = await reader.read(65536)
                if not data:
                    break
                await ws.send_bytes(data)
        except Exception:
            pass

    t1 = asyncio.create_task(_browser_to_vnc())
    t2 = asyncio.create_task(_vnc_to_browser())
    done, pending = await asyncio.wait({t1, t2}, return_when=asyncio.FIRST_COMPLETED)
    for t in pending:
        t.cancel()
    writer.close()
    try:
        await ws.close()
    except Exception:
        pass


@app.get("/api/logs")
def get_logs(level: str | None = None, source: str | None = None, limit: int = 200) -> dict:
    from huntbot2.logging.log_store import list_logs
    entries = list_logs(_db_path(), level=level or None, source=source or None,
                        limit=min(limit, 500))
    return {"logs": entries, "count": len(entries)}


@app.delete("/api/logs")
def delete_logs() -> dict:
    from huntbot2.logging.log_store import clear_logs
    deleted = clear_logs(_db_path())
    return {"deleted": deleted}


@app.post("/api/artifacts/prune")
def prune_artifacts_endpoint(keep: int = 50) -> dict:
    from huntbot2.tailoring.store import prune_artifacts
    keep = max(1, min(keep, 500))
    result = prune_artifacts(_db_path(), keep=keep)
    return result


@app.get("/api/artifacts")
def list_artifacts(limit: int = 100) -> dict:
    from pathlib import Path as _Path
    conn = sqlite3.connect(str(_db_path()))
    try:
        rows = conn.execute(
            """SELECT a.id, a.kind, a.path, a.meta_json, a.created_at,
                      j.title, j.company,
                      COALESCE(a.job_id, CAST(json_extract(a.meta_json, '$.job_id') AS INTEGER)) as job_id
               FROM artifacts a
               LEFT JOIN jobs j
                  ON j.id = COALESCE(a.job_id, CAST(json_extract(a.meta_json, '$.job_id') AS INTEGER))
               WHERE a.kind IN ('resume', 'cover', 'apply_screenshot')
               ORDER BY a.id DESC LIMIT ?""",
            [min(limit, 200)],
        ).fetchall()
    finally:
        conn.close()

    items = []
    for r in rows:
        p = _Path(r[2]) if r[2] else None
        items.append({
            "id": r[0],
            "kind": r[1],
            "path": r[2],
            "filename": p.name if p else None,
            "ext": p.suffix.lstrip(".").lower() if p else None,
            "file_exists": p.exists() if p else False,
            "created_at": r[4],
            "job_title": r[5],
            "job_company": r[6],
            "job_id": r[7],
        })
    return {"artifacts": items}


@app.get("/api/artifacts/{artifact_id}/download")
def download_artifact(artifact_id: int):
    from fastapi.responses import FileResponse
    from pathlib import Path as _Path
    conn = sqlite3.connect(str(_db_path()))
    try:
        row = conn.execute(
            "SELECT path, kind FROM artifacts WHERE id = ?", [artifact_id]
        ).fetchone()
    finally:
        conn.close()

    if not row:
        raise HTTPException(status_code=404, detail="Artifact not found")
    p = _Path(row[0])
    if not p.exists():
        raise HTTPException(status_code=404, detail="File no longer on disk")

    ext = p.suffix.lower()
    media = {
        "pdf": "application/pdf",
        "md": "text/markdown",
        "png": "image/png",
        "jpg": "image/jpeg",
        "jpeg": "image/jpeg",
    }.get(ext.lstrip("."), "application/octet-stream")
    return FileResponse(path=str(p), filename=p.name, media_type=media)


@app.get("/api/artifacts/{artifact_id}/view")
def view_artifact(artifact_id: int):
    from fastapi.responses import FileResponse
    from pathlib import Path as _Path
    conn = sqlite3.connect(str(_db_path()))
    try:
        row = conn.execute("SELECT path FROM artifacts WHERE id = ?", [artifact_id]).fetchone()
    finally:
        conn.close()
    if not row:
        raise HTTPException(status_code=404, detail="Artifact not found")
    p = _Path(row[0])
    if not p.exists():
        raise HTTPException(status_code=404, detail="File not found")
    ext = p.suffix.lower().lstrip(".")
    media = {
        "png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg",
        "pdf": "application/pdf", "txt": "text/plain", "md": "text/plain",
    }.get(ext, "application/octet-stream")
    # Render inline (preview) rather than forcing a download.
    return FileResponse(path=str(p), media_type=media, content_disposition_type="inline")


@app.delete("/api/artifacts/{artifact_id}")
def delete_artifact(artifact_id: int) -> dict:
    from pathlib import Path as _Path
    conn = sqlite3.connect(str(_db_path()))
    try:
        row = conn.execute("SELECT path FROM artifacts WHERE id = ?", [artifact_id]).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Artifact not found")
        conn.execute("DELETE FROM artifacts WHERE id = ?", [artifact_id])
        conn.commit()
    finally:
        conn.close()

    file_removed = False
    if row[0]:
        p = _Path(row[0])
        if p.exists():
            try:
                p.unlink()
                file_removed = True
            except OSError:
                pass
    return {"deleted": True, "file_removed": file_removed}


@app.delete("/api/artifacts/screenshots/all")
def delete_all_screenshots() -> dict:
    """Delete every apply_screenshot row + its file on disk, plus any orphan apply-*.png files."""
    from pathlib import Path as _Path
    import glob as _glob

    conn = sqlite3.connect(str(_db_path()))
    deleted_rows = 0
    deleted_files = 0
    try:
        rows = conn.execute(
            "SELECT id, path FROM artifacts WHERE kind = 'apply_screenshot'"
        ).fetchall()
        for row_id, path_str in rows:
            if path_str:
                p = _Path(path_str)
                if p.exists():
                    try:
                        p.unlink()
                        deleted_files += 1
                    except OSError:
                        pass
            conn.execute("DELETE FROM artifacts WHERE id = ?", [row_id])
            deleted_rows += 1
        conn.commit()
    finally:
        conn.close()

    artifacts_dir = _project_root() / "artifacts"
    for orphan in _glob.glob(str(artifacts_dir / "apply-*.png")):
        try:
            _Path(orphan).unlink()
            deleted_files += 1
        except OSError:
            pass

    return {"deleted_rows": deleted_rows, "deleted_files": deleted_files}


@app.get("/api/apply/pending-questions")
def apply_pending_questions() -> dict:
    from huntbot2.apply.qa_store import load_pending_questions
    return {"questions": load_pending_questions(_project_root())}


class AnswerPayload(BaseModel):
    answers: dict[str, str]


@app.post("/api/apply/pending-questions/answer")
def apply_save_answers(payload: AnswerPayload) -> dict:
    from huntbot2.apply.qa_store import resolve_pending_question
    root = _project_root()
    for label, answer in payload.answers.items():
        if answer.strip():
            resolve_pending_question(root, label, answer.strip())
    return {"saved": len(payload.answers)}


@app.get("/api/pipeline/last-run")
def pipeline_last_run() -> dict[str, object]:
    import glob as _glob
    artifacts_dir = _project_root() / "artifacts"
    pattern = str(artifacts_dir / "pipeline-observability-*.json")
    files = sorted(_glob.glob(pattern), reverse=True)
    if not files:
        return {"found": False}
    try:
        payload = json.loads(Path(files[0]).read_text())
        return {"found": True, **payload}
    except Exception:
        return {"found": False}


@app.get("/api/pipeline/funnel")
def pipeline_funnel() -> dict[str, object]:
    """Cumulative funnel — a job at 'tailored' has also passed through 'scored' and 'discovered'."""
    conn = sqlite3.connect(str(_db_path()))
    try:
        rows = conn.execute(
            "SELECT COALESCE(status, 'discovered'), COUNT(*) FROM jobs GROUP BY COALESCE(status, 'discovered')"
        ).fetchall()
    finally:
        conn.close()
    counts: dict[str, int] = dict(rows)
    # Stage progression: discovered → scored → tailored → queued → applied
    # stale_url exits the funnel — excluded from cumulative counts
    discovered = counts.get("discovered", 0) + counts.get("scored", 0) + counts.get("tailored", 0) + counts.get("queued", 0) + counts.get("applied", 0)
    scored = counts.get("scored", 0) + counts.get("tailored", 0) + counts.get("queued", 0) + counts.get("applied", 0)
    tailored = counts.get("tailored", 0) + counts.get("queued", 0) + counts.get("applied", 0)
    queued = counts.get("queued", 0) + counts.get("applied", 0)
    applied = counts.get("applied", 0)
    return {
        "discovered": discovered,
        "scored":     scored,
        "tailored":   tailored,
        "queued":     queued,
        "applied":    applied,
        "stale_url":  counts.get("stale_url", 0),
    }


@app.get("/api/pipeline/jobs")
def pipeline_jobs(
    status: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dict[str, object]:
    conn = sqlite3.connect(str(_db_path()))
    try:
        where = "WHERE j.status = ?" if status else ""
        base_params: list[object] = [status] if status else []
        rows = conn.execute(
            f"""
            SELECT j.id, j.company, j.title, j.score, j.status,
                   j.sponsorship_confidence, j.source, j.created_at,
                   CASE WHEN COUNT(a.id) > 0 THEN 1 ELSE 0 END AS has_tailoring,
                   j.location, j.score_reasons_json
            FROM jobs j
            LEFT JOIN artifacts a ON a.job_id = j.id AND a.kind IN ('resume', 'cover')
            {where}
            GROUP BY j.id
            ORDER BY j.score DESC NULLS LAST
            LIMIT ? OFFSET ?
            """,
            [*base_params, limit, offset],
        ).fetchall()
        total_row = conn.execute(
            f"SELECT COUNT(*) FROM jobs j {where}", base_params
        ).fetchone()
    finally:
        conn.close()

    def _parse_reasons(raw):
        if not raw:
            return []
        try:
            data = json.loads(raw)
            return data if isinstance(data, list) else []
        except Exception:
            return []

    return {
        "jobs": [
            {
                "job_id": r[0],
                "company": r[1],
                "title": r[2],
                "score": r[3],
                "status": r[4] or "discovered",
                "sponsorship_confidence": r[5],
                "has_tailoring": bool(r[8]),
                "source": r[6],
                "discovered_at": r[7],
                "location": r[9] or "",
                "score_reasons": _parse_reasons(r[10]),
            }
            for r in rows
        ],
        "total": total_row[0] if total_row else 0,
    }


@app.get("/api/pipeline/jobs/{job_id}")
def pipeline_job_detail(job_id: int) -> dict[str, object]:
    conn = sqlite3.connect(str(_db_path()))
    try:
        row = conn.execute(
            """SELECT id, company, title, url, score, score_reasons_json,
                      status, sponsorship_confidence, source, location,
                      created_at, updated_at
               FROM jobs WHERE id = ?""",
            [job_id],
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Job not found")
        artifacts = conn.execute(
            "SELECT kind, path FROM artifacts WHERE job_id = ?", [job_id]
        ).fetchall()
    finally:
        conn.close()

    return {
        "job_id": row[0],
        "company": row[1],
        "title": row[2],
        "url": row[3],
        "score": row[4],
        "score_reasons": json.loads(row[5]) if row[5] else [],
        "status": row[6] or "discovered",
        "sponsorship_confidence": row[7],
        "source": row[8],
        "location": row[9],
        "discovered_at": row[10],
        "updated_at": row[11],
        "artifacts": [{"kind": a[0], "path": a[1]} for a in artifacts],
    }


@app.get("/styles.css")
def serve_css() -> FileResponse:
    return FileResponse(
        Path(__file__).resolve().parent / "static" / "styles.css",
        media_type="text/css",
    )


@app.get("/colors_and_type.css")
def serve_design_tokens() -> FileResponse:
    return FileResponse(
        Path(__file__).resolve().parent / "static" / "colors_and_type.css",
        media_type="text/css",
    )


@app.get("/app.js")
def serve_js() -> FileResponse:
    return FileResponse(
        Path(__file__).resolve().parent / "static" / "app.js",
        media_type="application/javascript",
    )


@app.get("/assets/{filename}")
def serve_asset(filename: str) -> FileResponse:
    asset_path = Path(__file__).resolve().parent / "static" / "assets" / filename
    if not asset_path.exists() or not asset_path.is_file():
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Asset not found")
    return FileResponse(asset_path)


def _safe_json_list(raw: str, default: list) -> list:
    try:
        v = __import__("json").loads(raw)
        return v if isinstance(v, list) else default
    except Exception:
        return default


def _mask_key(value: str) -> str:
    if len(value) <= 7:
        return "●●●●●●●●"
    return value[:7] + "●●●●●●●●"


@app.get("/api/settings")
def settings_get() -> dict:
    import json as _json
    anthropic = env_get("ANTHROPIC_API_KEY", "")
    openai = env_get("OPENAI_API_KEY", "")
    openrouter = env_get("OPENROUTER_API_KEY", "")
    try:
        discovery_urls = _json.loads(env_get("HUNTBOT2_DISCOVERY_URLS", "[]"))
    except Exception:
        discovery_urls = []
    try:
        min_score_val = int(env_get("HUNTBOT2_MIN_SCORE", "60") or "60")
    except (ValueError, TypeError):
        min_score_val = 60
    return {
        "llm_enabled": env_get("HUNTBOT2_LLM_ENABLED", "false").lower() == "true",
        "llm_provider": env_get("HUNTBOT2_LLM_PROVIDER", "anthropic"),
        "scoring_model": env_get("HUNTBOT2_SCORING_MODEL", "claude-sonnet-4-6"),
        "tailoring_model": env_get("HUNTBOT2_TAILORING_MODEL", "claude-sonnet-4-6"),
        "submit_policy": env_get("HUNTBOT2_SUBMIT_POLICY", "stop_before_submit"),
        "theme": env_get("HUNTBOT2_THEME", "dark"),
        "min_score": min_score_val,
        "browser_mode": env_get("HUNTBOT2_BROWSER_MODE", "headed"),
        "discovery_urls": discovery_urls,
        "live_discover": env_get("HUNTBOT2_LIVE_DISCOVER", "false").lower() == "true",
        "portals": _safe_json_list(env_get("HUNTBOT2_PORTALS", '["linkedin"]'), ["linkedin"]),
        "discovery_model": env_get("HUNTBOT2_DISCOVERY_MODEL", ""),
        "discover_max_jobs": int(env_get("HUNTBOT2_DISCOVER_MAX_JOBS", "40") or "40"),
        "discover_max_urls": int(env_get("HUNTBOT2_DISCOVER_MAX_URLS", "4") or "4"),
        "discover_max_actions": int(env_get("HUNTBOT2_DISCOVER_MAX_ACTIONS", "15") or "15"),
        "api_keys": {
            "anthropic": {"set": bool(anthropic), "preview": _mask_key(anthropic) if anthropic else None},
            "openai": {"set": bool(openai), "preview": _mask_key(openai) if openai else None},
            "openrouter": {"set": bool(openrouter), "preview": _mask_key(openrouter) if openrouter else None},
        },
    }


@app.post("/api/settings")
def settings_update(payload: SettingsUpdateRequest) -> dict:
    import json as _json
    for field, value in payload.model_dump(exclude_none=True).items():
        env_key = _SETTINGS_MAP.get(field)
        if not env_key:
            continue
        if isinstance(value, bool):
            env_set(env_key, "true" if value else "false")
        elif isinstance(value, list):
            env_set(env_key, _json.dumps(value))
        else:
            env_set(env_key, str(value))
    return {"ok": True}


@app.post("/api/settings/test-key")
def settings_test_key(payload: TestKeyRequest) -> dict:
    import httpx

    key = payload.key.strip()
    if key == "__stored__":
        key_var = {"anthropic": "ANTHROPIC_API_KEY", "openai": "OPENAI_API_KEY", "openrouter": "OPENROUTER_API_KEY"}.get(payload.provider, "")
        key = env_get(key_var, "").strip()
    if not key:
        return {"ok": False, "error": "No key saved for this provider"}

    try:
        if payload.provider == "anthropic":
            resp = httpx.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": key,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json={
                    "model": "claude-haiku-4-5-20251001",
                    "max_tokens": 1,
                    "messages": [{"role": "user", "content": "hi"}],
                },
                timeout=10,
            )
        elif payload.provider == "openai":
            resp = httpx.get(
                "https://api.openai.com/v1/models",
                headers={"Authorization": f"Bearer {key}"},
                timeout=10,
            )
        elif payload.provider == "openrouter":
            resp = httpx.get(
                "https://openrouter.ai/api/v1/auth/key",
                headers={"Authorization": f"Bearer {key}"},
                timeout=10,
            )
        else:
            return {"ok": False, "error": f"Unknown provider: {payload.provider}"}

        if resp.status_code < 400:
            return {"ok": True}
        body_text = resp.text[:200]
        return {"ok": False, "error": f"HTTP {resp.status_code}: {body_text}"}
    except Exception as e:
        return {"ok": False, "error": f"Connection failed: {e}"}


def _master_resume_path() -> Path:
    root = os.getenv("HUNTBOT2_DATA_ROOT", str(_project_root()))
    return Path(root) / "config" / "master_resume.md"


@app.get("/api/llm/health")
def llm_health_status() -> dict:
    """Truthful LLM status for the dashboard: whether it's enabled, whether a
    usable key is actually configured, and the most recent LLM-related error."""
    from huntbot2.llm.client import llm_health
    from huntbot2.logging.log_store import list_logs

    health = llm_health()
    last_error = None
    try:
        for entry in list_logs(_db_path(), level="error", limit=25):
            details = entry.get("details") or {}
            text = f"{entry.get('message', '')} {json.dumps(details)}".lower()
            if entry.get("source") == "llm" or "llm" in text or "rate limit" in text:
                last_error = {"message": entry.get("message"), "at": entry.get("created_at")}
                break
    except Exception:
        last_error = None
    health["last_error"] = last_error
    return health


@app.get("/api/settings/master-resume")
def master_resume_get() -> dict:
    path = _master_resume_path()
    return {"content": path.read_text(encoding="utf-8") if path.exists() else ""}


@app.post("/api/settings/master-resume")
def master_resume_save(payload: MasterResumeUpdate) -> dict:
    path = _master_resume_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(payload.content, encoding="utf-8")
    return {"ok": True}


def _resolve_apply_submit_token(submit_policy: str, project_root: Path) -> str | None:
    """Authorize live submit only when policy allows it and a human checkpoint is valid."""
    from huntbot2.apply.runner import expected_submit_token, normalize_submit_policy

    if normalize_submit_policy(submit_policy) != "allow_submit_with_token":
        return None
    try:
        status = CockpitRepository(project_root / "data" / "huntbot2.db").policy_status()
    except Exception:
        return None
    if not status.get("checkpoint_valid"):
        return None
    return expected_submit_token()


def _run_pipeline_thread(run_id: str, run: dict, stage: str, project_dir: str, forced_mode: str = "auto", portals: list[str] | None = None) -> None:
    from huntbot2.automation.pipeline import STAGE_ORDER, STAGE_RUNNERS
    from huntbot2.db.schema import init_db as _init_db
    from huntbot2.config.env_store import load_all as _load_env
    from huntbot2.config.workspace import set_workspace
    from pathlib import Path as _Path
    import time as _time
    import os as _os

    # Adopt the caller's workspace so env_store/db reads resolve to the right
    # user inside this thread (contextvars don't cross thread boundaries).
    if _Path(project_dir).resolve() != _repo_root():
        set_workspace(project_dir)

    # Push .env settings into os.environ so pipeline stages can read them via os.getenv().
    # setdefault preserves any values already set in the actual process environment.
    for _k, _v in _load_env().items():
        _os.environ.setdefault(_k, _v)

    # Per-run portal override — takes precedence over .env for this thread only
    import json as _json
    if portals:
        _os.environ["HUNTBOT2_PORTALS"] = _json.dumps(portals)

    try:
        root = _Path(project_dir).resolve()
        _init_db(root / "data" / "huntbot2.db")

        stages_to_run = STAGE_ORDER if stage == "all" else [stage]
        use_llm = env_get("HUNTBOT2_LLM_ENABLED", "false").lower() == "true"

        score_client = None
        tailor_client = None
        if use_llm:
            from huntbot2.llm.client import client_from_env, client_for_tailoring
            score_client = client_from_env()
            tailor_client = client_for_tailoring()

        for stage_name in stages_to_run:
            run["events"].append({
                "type": "stage",
                "data": {"stage": stage_name, "status": "running",
                         "message": f"Running {stage_name}{'  ✦ AI' if use_llm else ''}…"},
            })
            start = _time.perf_counter_ns()
            try:
                mode = forced_mode if forced_mode != "auto" else env_get("HUNTBOT2_BROWSER_MODE", "headed")
                if stage_name == "score" and score_client is not None:
                    out = STAGE_RUNNERS[stage_name](root, mode, False, llm_client=score_client)
                elif stage_name == "tailor" and tailor_client is not None:
                    out = STAGE_RUNNERS[stage_name](root, mode, False, llm_client=tailor_client)
                elif stage_name == "apply":
                    # Read from .env (env_get) — settings changes via UI write here
                    _submit_policy = env_get("HUNTBOT2_SUBMIT_POLICY", "never_submit") or "never_submit"
                    _submit_token = _resolve_apply_submit_token(_submit_policy, root)
                    out = STAGE_RUNNERS[stage_name](root, mode, False, submit_policy=_submit_policy, submit_token=_submit_token, llm_client=tailor_client)
                elif stage_name == "session":
                    out = STAGE_RUNNERS[stage_name](root, mode, False, llm_client=score_client, llm_tailor_client=tailor_client)
                else:
                    out = STAGE_RUNNERS[stage_name](root, mode, False)
                msg = " ".join(f"{k}={v}" for k, v in (out or {}).items())
                duration = int((_time.perf_counter_ns() - start) / 1_000_000)
                run["events"].append({
                    "type": "stage",
                    "data": {"stage": stage_name, "status": "completed",
                             "message": msg, "duration_ms": duration},
                })
            except Exception as exc:
                from huntbot2.llm.client import LLMRateLimitError as _RateLimitError
                duration = int((_time.perf_counter_ns() - start) / 1_000_000)
                err_msg = str(exc)
                if isinstance(exc, _RateLimitError):
                    wait_s = exc.retry_after
                    run["events"].append({
                        "type": "stage",
                        "data": {"stage": stage_name, "status": "running",
                                 "message": f"Rate limited — waiting {wait_s}s then retrying…", "duration_ms": duration},
                    })
                    _time.sleep(wait_s)
                    # retry once after the wait
                    try:
                        start = _time.perf_counter_ns()
                        if stage_name == "score" and score_client is not None:
                            out = STAGE_RUNNERS[stage_name](root, mode, False, llm_client=score_client)
                        elif stage_name == "tailor" and tailor_client is not None:
                            out = STAGE_RUNNERS[stage_name](root, mode, False, llm_client=tailor_client)
                        elif stage_name == "apply":
                            _submit_policy = env_get("HUNTBOT2_SUBMIT_POLICY", "never_submit") or "never_submit"
                            _submit_token = _resolve_apply_submit_token(_submit_policy, root)
                            out = STAGE_RUNNERS[stage_name](root, mode, False, submit_policy=_submit_policy, submit_token=_submit_token, llm_client=tailor_client)
                        elif stage_name == "session":
                            out = STAGE_RUNNERS[stage_name](root, mode, False, llm_client=score_client, llm_tailor_client=tailor_client)
                        else:
                            out = STAGE_RUNNERS[stage_name](root, mode, False)
                        msg = " ".join(f"{k}={v}" for k, v in (out or {}).items())
                        duration = int((_time.perf_counter_ns() - start) / 1_000_000)
                        run["events"].append({
                            "type": "stage",
                            "data": {"stage": stage_name, "status": "completed",
                                     "message": msg, "duration_ms": duration},
                        })
                        continue
                    except Exception as retry_exc:
                        err_msg = str(retry_exc)
                        duration = int((_time.perf_counter_ns() - start) / 1_000_000)
                run["events"].append({
                    "type": "stage",
                    "data": {"stage": stage_name, "status": "failed",
                             "message": err_msg, "duration_ms": duration},
                })
                run["events"].append({"type": "done", "data": {"run_id": run_id, "status": "failed"}})
                run["status"] = "failed"
                run["done"] = True
                try:
                    from huntbot2.logging.log_store import write_log as _write_log
                    _db = _Path(project_dir).resolve() / "data" / "huntbot2.db"
                    _write_log(_db, level="error", source="pipeline",
                               message=f"Stage '{stage_name}' failed",
                               details={"stage": stage_name, "error": err_msg,
                                        "run_id": run_id, "duration_ms": duration})
                except Exception:
                    pass
                return

        run["status"] = "completed"
        run["events"].append({"type": "done", "data": {"run_id": run_id, "status": "completed"}})
        run["done"] = True
    except Exception as exc:
        run["events"].append({"type": "done", "data": {"run_id": run_id, "status": "failed", "message": str(exc)}})
        run["status"] = "failed"
        run["done"] = True


@app.post("/api/pipeline/run")
def pipeline_run_trigger(payload: PipelineRunRequest) -> dict:
    from uuid import uuid4
    run_id = f"run-{uuid4().hex[:8]}"
    run_dict = {"events": [], "done": False, "status": "running"}
    with _pipeline_runs_lock:
        _pipeline_runs[run_id] = run_dict
    t = threading.Thread(
        target=_run_pipeline_thread,
        args=(run_id, run_dict, payload.stage, str(_project_root()), payload.mode, payload.portals),
        daemon=True,
    )
    t.start()
    return {"run_id": run_id, "stage": payload.stage, "status": "started"}


@app.get("/api/pipeline/run/{run_id}/stream")
async def pipeline_run_stream(run_id: str) -> StreamingResponse:
    async def event_gen():
        run = _pipeline_runs.get(run_id)
        if not run:
            yield 'event: error\ndata: {"message": "run not found"}\n\n'
            return
        cursor = 0
        while True:
            events = run["events"]
            while cursor < len(events):
                ev = events[cursor]
                yield f"event: {ev['type']}\ndata: {json.dumps(ev['data'])}\n\n"
                cursor += 1
            if run["done"] and cursor >= len(events):
                break
            await asyncio.sleep(0.2)

    return StreamingResponse(event_gen(), media_type="text/event-stream")


def _add_url_via_playwright(url: str) -> dict | None:
    """Fetch job page with headless Playwright and extract title/company/location.
    Returns dict or None if Playwright unavailable."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return None
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            try:
                page = browser.new_page()
                page.goto(url, timeout=15000, wait_until="domcontentloaded")
                title = page.title() or ""
                company = ""
                for sel in ["[data-company-name]", ".topcard__org-name-link", ".company-name"]:
                    el = page.query_selector(sel)
                    if el:
                        company = (el.inner_text() or "").strip()
                        break
                location = ""
                for sel in ["[data-job-location]", ".topcard__flavor--bullet", ".location"]:
                    el = page.query_selector(sel)
                    if el:
                        location = (el.inner_text() or "").strip()
                        break
                return {"title": title, "company": company, "location": location}
            finally:
                browser.close()
    except Exception:
        return None


@app.post("/api/jobs/add-url")
def jobs_add_url(payload: AddUrlRequest) -> dict:
    url = payload.url.strip()
    if not url.startswith(("http://", "https://")):
        return {"error": "invalid_url"}

    info = _add_url_via_playwright(url) or {}
    title = info.get("title") or url
    company = info.get("company") or ""
    location = info.get("location") or ""

    conn = sqlite3.connect(str(_db_path()))
    try:
        existing = conn.execute("SELECT id FROM jobs WHERE url = ?", (url,)).fetchone()
        if existing:
            return {"job_id": existing[0], "title": title, "company": company, "location": location, "duplicate": True}
        from datetime import datetime, UTC as _UTC
        now = datetime.now(_UTC).isoformat()
        cur = conn.execute(
            "INSERT INTO jobs (source, title, company, location, url, created_at, status) VALUES (?,?,?,?,?,?,?)",
            ("manual", title, company, location, url, now, "discovered"),
        )
        conn.commit()
        return {"job_id": cur.lastrowid, "title": title, "company": company, "location": location, "status": "discovered"}
    finally:
        conn.close()


# ── Scheduled auto-apply ──────────────────────────────────────────────────────

_SCHEDULE_ENV = {
    "enabled": "HUNTBOT2_SCHEDULE_ENABLED",
    "interval_minutes": "HUNTBOT2_SCHEDULE_INTERVAL_MIN",
    "jobs_per_run": "HUNTBOT2_SCHEDULE_JOBS_PER_RUN",
    "window_start": "HUNTBOT2_SCHEDULE_WINDOW_START",
    "window_end": "HUNTBOT2_SCHEDULE_WINDOW_END",
    "timezone": "HUNTBOT2_SCHEDULE_TIMEZONE",
}

_scheduler_state: dict = {"last_run_at": None, "last_result": None}
_scheduler_lock = threading.Lock()
_scheduler_stop = threading.Event()


def _daily_cap() -> int:
    try:
        return int(repo.get_policy_settings().get("daily_apply_cap", 20))
    except Exception:
        return 20


def _count_applied_today(db_path: Path) -> int:
    today = datetime.now(UTC).date().isoformat()
    conn = sqlite3.connect(db_path)
    try:
        return int(conn.execute(
            "SELECT COUNT(*) FROM jobs WHERE status='applied' AND date(updated_at)=?",
            (today,),
        ).fetchone()[0])
    except Exception:
        return 0
    finally:
        conn.close()


def _count_available_tailored(db_path: Path) -> int:
    conn = sqlite3.connect(db_path)
    try:
        return int(conn.execute(
            "SELECT COUNT(*) FROM jobs WHERE status='tailored' AND url IS NOT NULL AND TRIM(url)!=''"
        ).fetchone()[0])
    except Exception:
        return 0
    finally:
        conn.close()


def _schedule_status_payload() -> dict:
    from huntbot2.automation.auto_apply import load_schedule_config, next_run_at
    cfg = load_schedule_config(daily_cap=_daily_cap())
    db = _db_path()
    now = datetime.now(UTC)
    with _scheduler_lock:
        last = _scheduler_state["last_run_at"]
        last_result = _scheduler_state["last_result"]
    applied_today = _count_applied_today(db)
    nxt = next_run_at(now, last, cfg)
    return {
        "enabled": cfg.enabled,
        "interval_minutes": cfg.interval_minutes,
        "jobs_per_run": cfg.jobs_per_run,
        "window_start": cfg.window_start,
        "window_end": cfg.window_end,
        "timezone": cfg.timezone,
        "daily_cap": cfg.daily_cap,
        "applied_today": applied_today,
        "remaining_today": max(0, cfg.daily_cap - applied_today),
        "available_tailored": _count_available_tailored(db),
        "last_run_at": last.isoformat() if last else None,
        "next_run_at": nxt.isoformat() if nxt else None,
        "last_result": last_result,
    }


def _scheduler_decide_and_run(now: datetime | None = None, force: bool = False) -> dict:
    """Run a scheduled apply cycle if one is due (or forced via 'Run now').
    Returns a small decision dict (also surfaced in the status endpoint).
    Submission still flows through the checkpoint-gated token, so a valid human
    checkpoint is required to actually submit."""
    from huntbot2.automation.auto_apply import (
        load_schedule_config,
        plan_apply_count,
        should_run_now,
    )

    now = now or datetime.now(UTC)
    cfg = load_schedule_config(daily_cap=_daily_cap())
    with _scheduler_lock:
        last = _scheduler_state["last_run_at"]

    if not force and not should_run_now(now, last, cfg):
        return {"ran": False, "reason": "not_due"}

    db = _db_path()
    count = plan_apply_count(
        jobs_per_run=cfg.jobs_per_run,
        daily_cap=cfg.daily_cap,
        applied_today=_count_applied_today(db),
        available_tailored=_count_available_tailored(db),
    )

    with _scheduler_lock:
        _scheduler_state["last_run_at"] = now

    if count <= 0:
        result = {"ran": True, "applied_planned": 0, "reason": "nothing_to_apply"}
    else:
        # Cap the apply batch to the per-run plan, then run the full pipeline in
        # the background so discovery/scoring/tailoring keep the queue fed.
        os.environ["HUNTBOT2_APPLY_BATCH_SIZE"] = str(count)
        from uuid import uuid4
        run_id = f"sched-{uuid4().hex[:8]}"
        run_dict = {"events": [], "done": False, "status": "running"}
        with _pipeline_runs_lock:
            _pipeline_runs[run_id] = run_dict
        threading.Thread(
            target=_run_pipeline_thread,
            args=(run_id, run_dict, "all", str(_project_root()), "headless"),
            daemon=True,
        ).start()
        result = {"ran": True, "applied_planned": count, "run_id": run_id}

    with _scheduler_lock:
        _scheduler_state["last_result"] = {**result, "at": now.isoformat()}
    return result


def _scheduler_loop() -> None:
    from huntbot2.config.workspace import reset_workspace, set_workspace
    while not _scheduler_stop.is_set():
        try:
            for _k, _v in env_load_all().items():
                os.environ.setdefault(_k, _v)
            # Weekly calendar entries take priority; fall back to the simple
            # interval scheduler only when no calendar entry is due.
            cal = _calendar_tick()
            if not cal.get("ran"):
                _scheduler_decide_and_run()
            # Tick each user workspace's calendar too — every account gets its
            # own schedule, jobs DB, and templates.
            users_root = _repo_root() / "data" / "users"
            if users_root.exists():
                for ws in sorted(users_root.iterdir()):
                    if not (ws / "data" / "huntbot2.db").exists():
                        continue
                    token = set_workspace(ws)
                    try:
                        _calendar_tick()
                    finally:
                        reset_workspace(token)
        except Exception:
            pass
        _scheduler_stop.wait(60)


@app.get("/api/schedule")
def schedule_get() -> dict:
    from huntbot2.automation.auto_apply import load_schedule_config
    cfg = load_schedule_config(daily_cap=_daily_cap())
    return {
        "enabled": cfg.enabled,
        "interval_minutes": cfg.interval_minutes,
        "jobs_per_run": cfg.jobs_per_run,
        "window_start": cfg.window_start,
        "window_end": cfg.window_end,
        "timezone": cfg.timezone,
        "daily_cap": cfg.daily_cap,
    }


@app.post("/api/schedule")
def schedule_update(payload: ScheduleUpdate) -> dict:
    for field, value in payload.model_dump(exclude_none=True).items():
        env_key = _SCHEDULE_ENV.get(field)
        if not env_key:
            continue
        if isinstance(value, bool):
            env_set(env_key, "true" if value else "false")
        else:
            env_set(env_key, str(value))
    return {"ok": True, **schedule_get()}


@app.get("/api/schedule/status")
def schedule_status() -> dict:
    return _schedule_status_payload()


@app.post("/api/schedule/run-now")
def schedule_run_now() -> dict:
    """Operator-triggered one-off cycle, bypassing the interval/window checks
    but still respecting the daily cap, jobs-per-run, and submit gate."""
    return _scheduler_decide_and_run(force=True)


# ── Weekly calendar (per-platform, per-day auto-apply) ────────────────────────

def _schedule_timezone() -> str:
    return env_get("HUNTBOT2_SCHEDULE_TIMEZONE", "UTC") or "UTC"


def _trigger_pipeline_run(*, run_id_prefix: str, platform: str | None, mode: str, count: int) -> str:
    """Launch a background discover→score→tailor→apply run scoped to a platform
    and submit mode. 'supervised' stops before submit; 'automatic' uses the
    auto-submit policy (still gated by the human checkpoint)."""
    from uuid import uuid4
    if platform:
        os.environ["HUNTBOT2_PORTALS"] = json.dumps([platform])
        os.environ["HUNTBOT2_LIVE_DISCOVER"] = "true"
    os.environ["HUNTBOT2_APPLY_BATCH_SIZE"] = str(max(1, count))
    os.environ["HUNTBOT2_SUBMIT_POLICY"] = "auto_submit" if mode == "automatic" else "stop_before_submit"
    env_set("HUNTBOT2_SUBMIT_POLICY", os.environ["HUNTBOT2_SUBMIT_POLICY"])

    run_id = f"{run_id_prefix}-{uuid4().hex[:8]}"
    run_dict = {"events": [], "done": False, "status": "running"}
    with _pipeline_runs_lock:
        _pipeline_runs[run_id] = run_dict
    threading.Thread(
        target=_run_pipeline_thread,
        args=(run_id, run_dict, "all", str(_project_root()), "headless"),
        daemon=True,
    ).start()
    return run_id


def _run_calendar_entry(entry, *, now: datetime | None = None) -> dict:
    from huntbot2.automation.auto_apply import plan_apply_count
    from huntbot2.automation.calendar_store import mark_entry_ran
    now = now or datetime.now(UTC)
    db = _db_path()
    count = plan_apply_count(
        jobs_per_run=entry.jobs_per_run,
        daily_cap=_daily_cap(),
        applied_today=_count_applied_today(db),
        available_tailored=_count_available_tailored(db),
    )
    mark_entry_ran(db, entry.id, now)
    # Even when no job is tailored yet, run the cycle so discovery refreshes the
    # queue for this platform; the apply stage is bounded by the cap regardless.
    run_id = _trigger_pipeline_run(
        run_id_prefix=f"cal{entry.id}", platform=entry.platform, mode=entry.mode,
        count=max(1, count),
    )
    result = {"ran": True, "entry_id": entry.id, "platform": entry.platform,
              "mode": entry.mode, "applied_planned": count, "run_id": run_id,
              "at": now.isoformat()}
    with _scheduler_lock:
        _scheduler_state["last_result"] = result
    return result


def _calendar_tick(now: datetime | None = None) -> dict:
    from huntbot2.automation.calendar_schedule import next_due_entry
    from huntbot2.automation.calendar_store import list_entries
    now = now or datetime.now(UTC)
    entries = list_entries(_db_path())
    due = next_due_entry(entries, now, timezone=_schedule_timezone())
    if due is None:
        return {"ran": False, "reason": "no_entry_due"}
    return _run_calendar_entry(due, now=now)


@app.get("/api/calendar")
def calendar_list() -> dict:
    from huntbot2.automation.calendar_schedule import DAY_NAMES, MODES, PLATFORMS
    from huntbot2.automation.calendar_store import list_entries
    entries = [vars(e) for e in list_entries(_db_path())]
    return {"entries": entries, "platforms": list(PLATFORMS),
            "modes": list(MODES), "day_names": DAY_NAMES,
            "timezone": _schedule_timezone()}


@app.post("/api/calendar")
def calendar_add(payload: CalendarEntryCreate) -> dict:
    from huntbot2.automation.calendar_store import add_entry
    try:
        entry = add_entry(
            _db_path(),
            day_of_week=payload.day_of_week,
            time_hhmm=payload.time_hhmm,
            platform=payload.platform,
            jobs_per_run=payload.jobs_per_run,
            mode=payload.mode,
            enabled=payload.enabled,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return vars(entry)


@app.post("/api/calendar/{entry_id}")
def calendar_update(entry_id: int, payload: CalendarEntryUpdate) -> dict:
    from huntbot2.automation.calendar_store import list_entries, update_entry
    update_entry(_db_path(), entry_id, **payload.model_dump(exclude_none=True))
    match = [vars(e) for e in list_entries(_db_path()) if e.id == entry_id]
    if not match:
        raise HTTPException(status_code=404, detail="entry not found")
    return match[0]


@app.delete("/api/calendar/{entry_id}")
def calendar_delete(entry_id: int) -> dict:
    from huntbot2.automation.calendar_store import delete_entry
    delete_entry(_db_path(), entry_id)
    return {"deleted": True, "entry_id": entry_id}


@app.post("/api/calendar/{entry_id}/run-now")
def calendar_run_now(entry_id: int) -> dict:
    from huntbot2.automation.calendar_store import list_entries
    match = [e for e in list_entries(_db_path()) if e.id == entry_id]
    if not match:
        raise HTTPException(status_code=404, detail="entry not found")
    return _run_calendar_entry(match[0])


# ── Accounts, onboarding, per-user stats ─────────────────────────────────────

def _signups_allowed() -> bool:
    return (os.getenv("HUNTBOT2_ALLOW_SIGNUPS", "true") or "true").strip().lower() not in {"0", "false", "no"}


@app.get("/api/auth/signup-status")
def auth_signup_status() -> dict:
    return {"signups_allowed": _signups_allowed()}


@app.post("/api/auth/register")
def auth_register(payload: RegisterRequest, request: Request) -> JSONResponse:
    from huntbot2.web.accounts import create_session, create_user
    if not _signups_allowed():
        raise HTTPException(status_code=403, detail="signups are disabled")
    try:
        user = create_user(
            _central_db_path(),
            username=payload.username,
            password=payload.password,
            display_name=payload.display_name,
            base_dir=_repo_root(),
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    token = create_session(_central_db_path(), user["id"])
    resp = JSONResponse({"ok": True, "user": {k: user[k] for k in ("id", "username", "display_name", "onboarded")}})
    resp.set_cookie("hb_session", token, httponly=True, samesite="lax", max_age=30 * 86400)
    return resp


@app.post("/api/auth/login")
def auth_login(payload: LoginRequest) -> JSONResponse:
    from huntbot2.web.accounts import authenticate, create_session
    user = authenticate(_central_db_path(), username=payload.username, password=payload.password)
    if user is None:
        _track_auth_failure_and_maybe_alert("/api/auth/login")
        raise HTTPException(status_code=401, detail="invalid username or password")
    token = create_session(_central_db_path(), user["id"])
    resp = JSONResponse({"ok": True, "user": {k: user[k] for k in ("id", "username", "display_name", "onboarded")}})
    resp.set_cookie("hb_session", token, httponly=True, samesite="lax", max_age=30 * 86400)
    return resp


@app.post("/api/auth/logout")
def auth_logout(request: Request) -> JSONResponse:
    from huntbot2.web.accounts import destroy_session
    destroy_session(_central_db_path(), request.cookies.get("hb_session"))
    resp = JSONResponse({"ok": True})
    resp.delete_cookie("hb_session")
    return resp


@app.get("/api/auth/me")
def auth_me(request: Request) -> dict:
    user = getattr(request.state, "user", None)
    if user is None:
        return {"authenticated": False, "signups_allowed": _signups_allowed()}
    return {"authenticated": True,
            "user": {k: user.get(k) for k in ("id", "username", "display_name", "onboarded", "is_admin")}}


@app.get("/api/admin/overview")
def admin_overview_endpoint(request: Request) -> dict:
    """Account counts + cross-account activity. Visible to the admin account
    (the first registered user) and to the legacy single-user operator."""
    from huntbot2.web.accounts import admin_overview
    user = getattr(request.state, "user", None)
    if user is not None and not user.get("is_admin"):
        raise HTTPException(status_code=403, detail="admin access required")
    return admin_overview(_central_db_path())


@app.get("/api/onboarding")
def onboarding_questions(request: Request) -> dict:
    from huntbot2.web.accounts import ONBOARDING_QUESTIONS
    user = getattr(request.state, "user", None)
    return {"questions": ONBOARDING_QUESTIONS,
            "onboarded": bool(user and user.get("onboarded"))}


@app.post("/api/onboarding")
def onboarding_submit(payload: OnboardingSubmit, request: Request) -> dict:
    from huntbot2.web.accounts import build_profile_yaml, save_onboarding
    user = getattr(request.state, "user", None)
    if user is None:
        raise HTTPException(status_code=401, detail="sign in to complete onboarding")

    profile_yaml = build_profile_yaml(payload.answers)
    config_dir = _project_root() / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "profile.yml").write_text(profile_yaml)

    platforms = payload.answers.get("platforms")
    if platforms:
        items = [p.strip().lower() for p in str(platforms).split(",") if p.strip()]
        if items:
            env_set("HUNTBOT2_PORTALS", json.dumps(items))

    save_onboarding(_central_db_path(), user["id"], dict(payload.answers))
    return {"ok": True, "profile_written": True}


@app.post("/api/onboarding/template")
def onboarding_template_upload(payload: TemplateUpload, request: Request) -> dict:
    """Accept a .docx resume/cover template (base64). The tailoring engine keeps
    the document's format and only rewrites the [[PLACEHOLDER]] content."""
    user = getattr(request.state, "user", None)
    if user is None:
        raise HTTPException(status_code=401, detail="sign in to upload templates")
    if not payload.filename.lower().endswith(".docx"):
        raise HTTPException(status_code=422, detail="template must be a .docx file")
    try:
        blob = base64.b64decode(payload.content_b64, validate=True)
    except Exception as exc:
        raise HTTPException(status_code=422, detail="invalid base64 content") from exc
    if len(blob) > 5 * 1024 * 1024:
        raise HTTPException(status_code=422, detail="template too large (max 5 MB)")

    config_dir = _project_root() / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    target = config_dir / ("resume_template.docx" if payload.kind == "resume" else "cover_letter_template.docx")
    target.write_bytes(blob)
    return {"ok": True, "saved_as": target.name, "bytes": len(blob)}


@app.get("/api/stats/applications")
def stats_applications() -> dict:
    """Per-user application history: totals by status and applied counts per
    platform — the visibility piece of the multi-user vision."""
    conn = sqlite3.connect(str(_db_path()))
    try:
        by_status = dict(conn.execute(
            "SELECT COALESCE(status,'discovered'), COUNT(*) FROM jobs GROUP BY COALESCE(status,'discovered')"
        ).fetchall())
        applied_by_platform = dict(conn.execute(
            "SELECT COALESCE(source,'unknown'), COUNT(*) FROM jobs WHERE status='applied' "
            "GROUP BY COALESCE(source,'unknown')"
        ).fetchall())
        recent = conn.execute(
            "SELECT id, title, company, source, updated_at FROM jobs WHERE status='applied' "
            "ORDER BY updated_at DESC LIMIT 20"
        ).fetchall()
    finally:
        conn.close()
    return {
        "by_status": by_status,
        "applied_total": sum(applied_by_platform.values()),
        "applied_by_platform": applied_by_platform,
        "recent_applications": [
            {"job_id": r[0], "title": r[1], "company": r[2], "platform": r[3], "applied_at": r[4]}
            for r in recent
        ],
    }


@app.post("/api/jobs/apply-url")
def jobs_apply_url(payload: ApplyUrlRequest) -> dict:
    """Add a job URL and immediately kick a one-off cycle for it (supervised by
    default — stops before submit). Use mode='automatic' to let it submit,
    still gated by the daily cap and human checkpoint."""
    url = payload.url.strip()
    if not url.startswith(("http://", "https://")):
        return {"error": "invalid_url"}

    info = _add_url_via_playwright(url) or {}
    title = info.get("title") or url
    company = info.get("company") or ""
    location = info.get("location") or ""

    conn = sqlite3.connect(str(_db_path()))
    try:
        existing = conn.execute("SELECT id FROM jobs WHERE url = ?", (url,)).fetchone()
        if existing:
            job_id = existing[0]
        else:
            from datetime import datetime as _dt, UTC as _UTC
            now = _dt.now(_UTC).isoformat()
            cur = conn.execute(
                "INSERT INTO jobs (source, title, company, location, url, created_at, status) "
                "VALUES (?,?,?,?,?,?,?)",
                ("manual", title, company, location, url, now, "discovered"),
            )
            conn.commit()
            job_id = cur.lastrowid
    finally:
        conn.close()

    run_id = _trigger_pipeline_run(
        run_id_prefix="url", platform=None, mode=payload.mode, count=1,
    )
    return {"job_id": job_id, "title": title, "company": company,
            "mode": payload.mode, "run_id": run_id}


@app.get("/")
def dashboard() -> FileResponse:
    static_path = Path(__file__).resolve().parent / "static" / "index.html"
    return FileResponse(static_path)
