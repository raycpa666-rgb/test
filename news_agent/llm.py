"""Thin wrapper around the Claude Messages API.

Both LLM stages (relevance assessment and summarization) are single calls that
must return JSON, so they share one helper that uses structured outputs and
raises `LLMUnavailable` whenever the caller should fall back to heuristics.
"""

from __future__ import annotations

import json
import logging
from typing import Any

log = logging.getLogger(__name__)


class LLMUnavailable(RuntimeError):
    """Raised when Claude can't be reached, or declines to answer."""


_client = None
_client_failed = False


def get_client():
    """Return a cached Anthropic client, or raise LLMUnavailable."""
    global _client, _client_failed
    if _client is not None:
        return _client
    if _client_failed:
        raise LLMUnavailable("Anthropic client unavailable")
    try:
        import anthropic
    except ImportError as exc:  # pragma: no cover - depends on install
        _client_failed = True
        raise LLMUnavailable("`anthropic` package is not installed") from exc
    try:
        # Resolves ANTHROPIC_API_KEY, ANTHROPIC_AUTH_TOKEN, or an `ant auth login` profile.
        _client = anthropic.Anthropic()
    except Exception as exc:  # noqa: BLE001 - any construction failure means fall back
        _client_failed = True
        raise LLMUnavailable(f"could not create Anthropic client: {exc}") from exc
    return _client


def complete_json(
    *,
    model: str,
    system: str,
    prompt: str,
    schema: dict[str, Any],
    effort: str = "medium",
    max_tokens: int = 8000,
) -> dict[str, Any]:
    """Ask Claude for a JSON object matching `schema` and return it parsed."""
    client = get_client()
    try:
        response = client.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=system,
            output_config={"effort": effort, "format": {"type": "json_schema", "schema": schema}},
            messages=[{"role": "user", "content": prompt}],
        )
    except Exception as exc:  # noqa: BLE001 - network/auth/rate-limit all mean fall back
        raise LLMUnavailable(f"Claude request failed: {exc}") from exc

    # Safety classifiers can decline a request: HTTP 200 with an empty/partial body.
    if response.stop_reason == "refusal":
        raise LLMUnavailable("Claude declined the request")
    if response.stop_reason == "max_tokens":
        raise LLMUnavailable("Claude response was truncated (raise max_tokens)")

    text = next((block.text for block in response.content if block.type == "text"), "")
    if not text.strip():
        raise LLMUnavailable("Claude returned an empty response")
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise LLMUnavailable(f"Claude returned unparseable JSON: {exc}") from exc
