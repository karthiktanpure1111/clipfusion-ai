"""Progress-callback helpers shared by the analysis engines.

``ProgressFn = Callable[[float, str | None], None]`` receives the fraction (0..1) of the
current sub-step and an optional human-readable message (ARCHITECTURE.md section 3).
Exceptions raised by a callback propagate on purpose: the job framework cancels work by
raising ``JobCancelled`` from its progress callback.
"""

from __future__ import annotations

from collections.abc import Callable

ProgressFn = Callable[[float, str | None], None]


def report(progress: ProgressFn | None, fraction: float, message: str | None = None) -> None:
    """Call ``progress`` (if any) with ``fraction`` clamped to 0..1."""
    if progress is not None:
        progress(min(1.0, max(0.0, float(fraction))), message)


def scaled(progress: ProgressFn | None, lo: float, hi: float) -> ProgressFn | None:
    """Map a sub-step's 0..1 progress onto ``lo..hi`` of the parent callback."""
    if progress is None:
        return None

    def _inner(fraction: float, message: str | None = None) -> None:
        report(progress, lo + (hi - lo) * min(1.0, max(0.0, fraction)), message)

    return _inner
