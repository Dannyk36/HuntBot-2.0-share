from __future__ import annotations

import base64
import json
import time
from dataclasses import dataclass, field
from pathlib import Path

import httpx


class LLMError(Exception):
    """Raised when an LLM API call fails."""


class LLMRateLimitError(LLMError):
    """Raised on HTTP 429 — carries retry_after seconds from the provider."""

    def __init__(self, message: str, retry_after: int = 60) -> None:
        super().__init__(message)
        self.retry_after = retry_after


@dataclass
class LLMClient:
    provider: str  # "anthropic" | "openai" | "openrouter"
    model: str
    api_key: str
    calls: list[dict[str, object]] = field(default_factory=list)
    _log_cursor: int = field(default=0, init=False, repr=False)

    def ask(
        self,
        prompt: str,
        *,
        system: str | None = None,
        max_tokens: int = 1024,
    ) -> str:
        return self._call(prompt, system=system, max_tokens=max_tokens, image_bytes=None)

    def ask_vision(
        self,
        prompt: str,
        image_bytes: bytes,
        *,
        system: str | None = None,
        max_tokens: int = 1024,
    ) -> str:
        """Send a text prompt + screenshot to a vision-capable model."""
        return self._call(prompt, system=system, max_tokens=max_tokens, image_bytes=image_bytes)

    def _call(
        self,
        prompt: str,
        *,
        system: str | None,
        max_tokens: int,
        image_bytes: bytes | None,
    ) -> str:
        start = time.perf_counter()
        error: str | None = None
        response_text = ""
        try:
            if self.provider == "anthropic":
                response_text = self._ask_anthropic(prompt, system, max_tokens, image_bytes)
            elif self.provider in ("openai", "openrouter"):
                response_text = self._ask_openai_format(prompt, system, max_tokens, image_bytes)
            else:
                raise LLMError(f"Unknown provider: {self.provider}")
            return response_text
        except LLMError as exc:
            error = str(exc)
            raise
        except Exception as exc:
            error = str(exc)
            raise LLMError(str(exc)) from exc
        finally:
            self.calls.append({
                "provider": self.provider,
                "model": self.model,
                "prompt_chars": len(prompt),
                "system_chars": len(system) if system else 0,
                "response_chars": len(response_text),
                "has_image": image_bytes is not None,
                "duration_ms": int((time.perf_counter() - start) * 1000),
                "error": error,
            })

    def write_call_log(self, path) -> None:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a", encoding="utf-8") as f:
            for record in self.calls[self._log_cursor:]:
                f.write(json.dumps(record) + "\n")
        self._log_cursor = len(self.calls)

    def _ask_anthropic(
        self, prompt: str, system: str | None, max_tokens: int, image_bytes: bytes | None
    ) -> str:
        headers = {
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }
        if image_bytes:
            img_b64 = base64.b64encode(image_bytes).decode()
            content = [
                {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": img_b64}},
                {"type": "text", "text": prompt},
            ]
        else:
            content = prompt
        body: dict = {
            "model": self.model,
            "max_tokens": max_tokens,
            "messages": [{"role": "user", "content": content}],
        }
        if system:
            body["system"] = system
        resp = httpx.post(
            "https://api.anthropic.com/v1/messages",
            headers=headers,
            json=body,
            timeout=90.0,
        )
        if not resp.is_success:
            try:
                err_body = resp.json()
                err_msg = err_body.get("error", {}).get("message", resp.text[:200])
            except Exception:
                err_msg = resp.text[:200]
            if resp.status_code == 429:
                retry_after = int(resp.headers.get("retry-after", "60"))
                raise LLMRateLimitError(f"Anthropic rate limited (retry after {retry_after}s)", retry_after=retry_after)
            raise LLMError(f"Anthropic HTTP {resp.status_code}: {err_msg}")
        return resp.json()["content"][0]["text"]

    def _ask_openai_format(
        self, prompt: str, system: str | None, max_tokens: int, image_bytes: bytes | None
    ) -> str:
        base_url = (
            "https://api.openai.com/v1"
            if self.provider == "openai"
            else "https://openrouter.ai/api/v1"
        )
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "content-type": "application/json",
            "HTTP-Referer": "http://localhost:8095",
            "X-Title": "HuntBot2",
        }
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        if image_bytes:
            img_b64 = base64.b64encode(image_bytes).decode()
            user_content = [
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{img_b64}"}},
                {"type": "text", "text": prompt},
            ]
        else:
            user_content = prompt
        messages.append({"role": "user", "content": user_content})
        body = {
            "model": self.model,
            "max_tokens": max_tokens,
            "messages": messages,
        }
        resp = httpx.post(
            f"{base_url}/chat/completions",
            headers=headers,
            json=body,
            timeout=90.0,
        )
        if not resp.is_success:
            try:
                err_body = resp.json()
                err_msg = (err_body.get("error", {}) or {}).get("message", resp.text[:200])
            except Exception:
                err_msg = resp.text[:200]
            if resp.status_code == 429:
                retry_after = int(resp.headers.get("retry-after", "60"))
                raise LLMRateLimitError(f"{self.provider.title()} rate limited (retry after {retry_after}s)", retry_after=retry_after)
            raise LLMError(f"{self.provider.title()} HTTP {resp.status_code}: {err_msg}")
        return resp.json()["choices"][0]["message"]["content"]


def build_client(provider: str, model: str, api_key: str) -> LLMClient:
    return LLMClient(provider=provider, model=model, api_key=api_key)


def _base_client(model_env_key: str, default_model: str) -> LLMClient | None:
    from huntbot2.config.env_store import get as env_get

    if (env_get("HUNTBOT2_LLM_ENABLED", "false") or "false").lower() != "true":
        return None

    provider = env_get("HUNTBOT2_LLM_PROVIDER", "anthropic") or "anthropic"
    key_map = {
        "anthropic": "ANTHROPIC_API_KEY",
        "openai": "OPENAI_API_KEY",
        "openrouter": "OPENROUTER_API_KEY",
    }
    api_key = env_get(key_map.get(provider, ""), "")
    if not api_key:
        return None

    model = env_get(model_env_key, default_model) or default_model
    return LLMClient(provider=provider, model=model, api_key=api_key)


_PROVIDER_KEY_ENV = {
    "anthropic": "ANTHROPIC_API_KEY",
    "openai": "OPENAI_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
}


def llm_health() -> dict:
    """Truthful LLM status: not just the 'enabled' flag, but whether a usable
    key is actually configured. Lets the UI distinguish 'on and working' from
    'on but silently failing because no key is set'.
    """
    from huntbot2.config.env_store import get as env_get

    enabled = (env_get("HUNTBOT2_LLM_ENABLED", "false") or "false").lower() == "true"
    provider = env_get("HUNTBOT2_LLM_PROVIDER", "anthropic") or "anthropic"
    key_present = bool(env_get(_PROVIDER_KEY_ENV.get(provider, ""), ""))
    model = env_get("HUNTBOT2_SCORING_MODEL", "claude-sonnet-4-6")
    return {
        "enabled": enabled,
        "provider": provider if enabled else None,
        "model": model if enabled else None,
        "key_present": key_present,
        "ready": enabled and key_present,
    }


def client_from_env() -> LLMClient | None:
    """Scoring client — uses HUNTBOT2_SCORING_MODEL."""
    return _base_client("HUNTBOT2_SCORING_MODEL", "claude-sonnet-4-6")


def client_for_tailoring() -> LLMClient | None:
    """Tailoring client — uses HUNTBOT2_TAILORING_MODEL."""
    return _base_client("HUNTBOT2_TAILORING_MODEL", "claude-sonnet-4-6")


# Vision-capable model defaults per provider
_VISION_DEFAULTS = {
    "anthropic": "claude-sonnet-4-6",
    "openai": "gpt-4o",
    "openrouter": "anthropic/claude-sonnet-4-5",
}


def client_for_discovery() -> LLMClient | None:
    """Return an LLM client configured for the discovery model (must support vision)."""
    from huntbot2.config.env_store import get as env_get

    if (env_get("HUNTBOT2_LLM_ENABLED", "false") or "false").lower() != "true":
        return None

    provider = env_get("HUNTBOT2_LLM_PROVIDER", "anthropic") or "anthropic"
    key_map = {
        "anthropic": "ANTHROPIC_API_KEY",
        "openai": "OPENAI_API_KEY",
        "openrouter": "OPENROUTER_API_KEY",
    }
    api_key = env_get(key_map.get(provider, ""), "")
    if not api_key:
        return None

    # Use dedicated discovery model if set, else fall back to vision default for provider
    model = (
        env_get("HUNTBOT2_DISCOVERY_MODEL", "")
        or _VISION_DEFAULTS.get(provider, "claude-sonnet-4-6")
    )
    return LLMClient(provider=provider, model=model, api_key=api_key)
