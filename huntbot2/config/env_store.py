from __future__ import annotations
from pathlib import Path
import threading as _threading

# Module-level path — tests override this attribute directly
_ENV_PATH: Path = Path(__file__).resolve().parents[2] / ".env"
_DEFAULT_ENV_PATH: Path = _ENV_PATH

# Module-level lock for thread-safe access to .env file
_lock = _threading.Lock()


def _env_path() -> Path:
    """Active .env path: the per-user workspace's .env when a workspace context
    is set (multi-user mode), otherwise the module default (single-user)."""
    from huntbot2.config.workspace import workspace_env_path
    ws_path = workspace_env_path()
    return ws_path if ws_path is not None else _ENV_PATH


def _read_lines() -> list[str]:
    path = _env_path()
    if not path.exists():
        return []
    return path.read_text().splitlines()


def _write_lines(lines: list[str]) -> None:
    path = _env_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + ("\n" if lines else ""))


def get(key: str, default: str | None = None) -> str | None:
    import os as _os
    path = _env_path()
    prefix = f"{key}="
    for line in _read_lines():
        if line.startswith(prefix):
            return line[len(prefix):]
    # Process env is only a fallback for the repo-level singleton config. In
    # test/per-user workspace contexts, falling back to process env leaks stale
    # settings across users/tests.
    if path == _DEFAULT_ENV_PATH and key in _os.environ:
        return _os.environ[key]
    return default


def _mirror_to_process_env() -> bool:
    """Mirror writes into os.environ only in single-user mode. In a per-user
    workspace the process env is shared across users, so mirroring would leak
    one user's settings (or API keys) to others."""
    from huntbot2.config.workspace import get_workspace
    return get_workspace() is None


# Named 'set' intentionally; callers import as 'env_set' to avoid shadowing at call sites
def set(key: str, value: str) -> None:
    import os as _os
    with _lock:
        lines = _read_lines()
        prefix = f"{key}="
        new_line = f"{key}={value}"
        for i, line in enumerate(lines):
            if line.startswith(prefix):
                lines[i] = new_line
                _write_lines(lines)
                if _mirror_to_process_env():
                    _os.environ[key] = value  # so os.getenv readers see it without a restart
                return
        lines.append(new_line)
        _write_lines(lines)
        if _mirror_to_process_env():
            _os.environ[key] = value


def delete(key: str) -> None:
    import os as _os
    with _lock:
        prefix = f"{key}="
        _write_lines([l for l in _read_lines() if not l.startswith(prefix)])
        if _mirror_to_process_env():
            _os.environ.pop(key, None)


def load_all() -> dict[str, str]:
    result: dict[str, str] = {}
    for line in _read_lines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if "=" in stripped:
            key, _, value = stripped.partition("=")
            result[key] = value
    return result
