"""Thin Claude (Anthropic API) client used by highlight ranking and metadata generation.

* :func:`get_llm_client` is the single factory (tests monkeypatch it with a fake client).
* :func:`request_structured` streams one request with JSON-schema structured output and the
  server-side refusal fallback, and returns the parsed JSON object.
* :func:`guarded` runs an LLM step and turns every failure (API errors, refusals,
  truncation, invalid output) into ``None`` plus a warning, so callers keep their
  non-LLM result instead of failing the job.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from typing import Any, TypeVar

from pydantic import ValidationError

from app.core.config import get_settings
from app.core.errors import DependencyMissing, IntegrationNotConfigured

log = logging.getLogger(__name__)

T = TypeVar("T")
FALLBACK_BETA = "server-side-fallback-2026-07-01"
DEFAULT_MAX_TOKENS = 32000


class LLMUnavailable(Exception):
    """Claude returned no usable answer (refusal, truncated output, missing/invalid JSON)."""


def llm_active(use_llm: bool = True) -> bool:
    """True when the caller asked for the LLM and it is configured (key present, enabled)."""
    return bool(use_llm) and get_settings().llm_configured


def get_llm_client() -> Any:
    """Return an ``anthropic.Anthropic`` client; raises when the LLM is not configured."""
    settings = get_settings()
    if not settings.llm_configured:
        raise IntegrationNotConfigured(
            "Claude is not configured: set ANTHROPIC_API_KEY (and LLM_ENABLED=true) to enable AI ranking "
            "and AI-written metadata.")
    try:
        import anthropic
    except ImportError as exc:  # pragma: no cover - dependency is pinned
        raise DependencyMissing("The 'anthropic' package is not installed.") from exc
    return anthropic.Anthropic(api_key=settings.anthropic_api_key)


def _first_text(message: Any) -> str | None:
    for block in getattr(message, "content", None) or []:
        if getattr(block, "type", None) == "text":
            return str(getattr(block, "text", ""))
    return None


def request_structured(*, system: str, prompt: str, schema: dict[str, Any],
                       max_tokens: int = DEFAULT_MAX_TOKENS) -> dict[str, Any]:
    """Send one structured-output request to Claude and return the decoded JSON object.

    Raises :class:`LLMUnavailable` on refusal / ``max_tokens`` truncation / missing or
    invalid JSON; Anthropic SDK errors propagate (use :func:`guarded`)."""
    settings = get_settings()
    client = get_llm_client()
    with client.beta.messages.stream(
        model=settings.llm_model,
        max_tokens=max_tokens,
        betas=[FALLBACK_BETA],
        fallbacks="default",
        output_config={"effort": settings.llm_effort, "format": {"type": "json_schema", "schema": schema}},
        system=system,
        messages=[{"role": "user", "content": prompt}],
    ) as stream:
        message = stream.get_final_message()
    stop_reason = getattr(message, "stop_reason", None)
    if stop_reason in ("refusal", "max_tokens"):
        details = getattr(message, "stop_details", None)
        raise LLMUnavailable(f"Claude stopped with stop_reason={stop_reason!r} ({details})")
    text = _first_text(message)
    if not text:
        raise LLMUnavailable("Claude returned no text content")
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise LLMUnavailable(f"Claude returned invalid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise LLMUnavailable("Claude returned JSON that is not an object")
    return data


def guarded(fn: Callable[[], T], *, purpose: str) -> T | None:
    """Run ``fn``; on any LLM/API failure log a warning and return ``None``."""
    try:
        import anthropic
    except ImportError:  # pragma: no cover
        log.warning("Claude unavailable for %s: anthropic package missing", purpose)
        return None
    try:
        return fn()
    except anthropic.RateLimitError as exc:
        log.warning("Claude rate-limited during %s; using the non-LLM result (%s)", purpose, exc)
    except anthropic.APIStatusError as exc:
        log.warning("Claude API error %s during %s; using the non-LLM result (%s)",
                    getattr(exc, "status_code", "?"), purpose, exc)
    except anthropic.APIConnectionError as exc:
        log.warning("Could not reach Claude during %s; using the non-LLM result (%s)", purpose, exc)
    except (LLMUnavailable, ValidationError, IntegrationNotConfigured, DependencyMissing) as exc:
        log.warning("Claude result unusable for %s; using the non-LLM result (%s)", purpose, exc)
    except Exception:  # noqa: BLE001 - an LLM hiccup must never fail the analysis
        log.warning("Unexpected error during Claude %s; using the non-LLM result", purpose, exc_info=True)
    return None
