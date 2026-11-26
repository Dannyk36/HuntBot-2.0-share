"""Helpers for parsing LLM responses."""
from __future__ import annotations

import re


def extract_json_text(raw: str) -> str:
    """Return the JSON payload from an LLM response.

    Handles responses wrapped in a markdown code fence (```json ... ```), with
    or without a language tag, and falls back to the stripped raw text when no
    fence is present. Replaces the fragile ``.split("```")[1].lstrip("json")``
    idiom, where ``lstrip`` stripped the character set ``{j,s,o,n}`` rather than
    the literal ``json`` prefix and could corrupt the payload.
    """
    text = (raw or "").strip()
    if "```" in text:
        match = re.search(r"```(?:[a-zA-Z0-9_-]+)?\s*([\s\S]*?)```", text)
        if match:
            return match.group(1).strip()
        # Unterminated fence — drop the opening fence and any language tag.
        text = re.sub(r"^```[a-zA-Z0-9_-]*\s*", "", text).strip()
    return text
