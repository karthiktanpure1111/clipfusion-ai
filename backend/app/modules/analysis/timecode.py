"""Human-readable timestamps used in highlight reasons, LLM prompts and metadata."""

from __future__ import annotations


def format_timestamp(seconds: float) -> str:
    """Format ``seconds`` as ``m:ss`` (or ``h:mm:ss`` from one hour on).

    >>> format_timestamp(761.4)
    '12:41'
    >>> format_timestamp(3725)
    '1:02:05'
    """
    total = max(0, int(seconds))
    hours, rem = divmod(total, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"
