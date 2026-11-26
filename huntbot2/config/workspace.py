"""Per-user workspace resolution.

A workspace is a directory holding one user's isolated state: their jobs DB
(data/huntbot2.db), config (profile.yml, master resume, docx templates, .env
with their own API keys), artifacts, and browser profiles. The active workspace
is request-scoped via a contextvar: web middleware sets it from the session
cookie, and background pipeline threads set it explicitly for the user they run
for. When no workspace is set, everything falls back to the repository root —
preserving single-user behaviour and the existing tests.
"""
from __future__ import annotations

from contextvars import ContextVar
from pathlib import Path

_current_workspace: ContextVar[str | None] = ContextVar("huntbot2_workspace", default=None)


def set_workspace(path: str | Path | None):
    """Set the active workspace for the current context. Returns the token so
    callers can reset; passing None clears it."""
    return _current_workspace.set(str(path) if path is not None else None)


def reset_workspace(token) -> None:
    _current_workspace.reset(token)


def get_workspace() -> Path | None:
    value = _current_workspace.get()
    return Path(value) if value else None


def workspace_env_path() -> Path | None:
    """Path to the active workspace's .env, or None when no workspace is set."""
    ws = get_workspace()
    return ws / "config" / ".env" if ws else None


def ensure_workspace_dirs(root: Path) -> Path:
    """Create the standard sub-directories for a user workspace."""
    for sub in ("data", "config", "artifacts", "browser-profiles"):
        (root / sub).mkdir(parents=True, exist_ok=True)
    return root
