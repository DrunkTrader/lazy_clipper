"""Shared duration policy for metadata, persisted retries, and explicit clips."""
import math

from ..errors import WorkloadLimitError


def require_source_duration(duration: float | None, maximum: float) -> None:
    if not isinstance(duration, (int, float)) or not math.isfinite(duration) or not 0 < duration <= maximum:
        raise WorkloadLimitError("SOURCE_DURATION_LIMIT")


def require_clip_duration(start: float, end: float, maximum: float) -> None:
    if not math.isfinite(end - start) or end - start > maximum:
        raise WorkloadLimitError("CLIP_TOO_LONG")
