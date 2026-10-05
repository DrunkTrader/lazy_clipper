"""Small public error contract. Exception text is never a public message."""
from typing import Literal

from pydantic import BaseModel
from sqlalchemy.exc import SQLAlchemyError


Stage = Literal["ingestion", "fetching", "transcription", "analysis", "rendering", "media", "database", "unknown"]

PUBLIC_MESSAGES: dict[Stage, str] = {
    "ingestion": "Internal server error during video ingestion.",
    "fetching": "Internal server error while fetching video data.",
    "transcription": "Internal server error during transcription.",
    "analysis": "Internal server error during AI analysis.",
    "rendering": "Internal server error while generating the clip.",
    "media": "Internal server error while loading media.",
    "database": "Internal server error while accessing project data.",
    "unknown": "Internal server error. Please try again later.",
}

# Persist application-owned messages in the existing columns. The structured
# code distinguishes interruption from provider/internal failures without a
# schema change; both remain explicit, user-requested retries.
INTERRUPTED_MESSAGES: dict[Stage, str] = {
    "ingestion": "Video ingestion was interrupted. Submit the video again to retry.",
    "transcription": "Transcription was interrupted. Submit the video again to retry.",
    "analysis": "AI analysis was interrupted. Submit the video again to retry.",
    "rendering": "Clip rendering was interrupted. Retry the affected clip.",
}

CAPTION_MESSAGES = {
    "CAPTION_ALIGNMENT_MISSING": "Stored word timing is unavailable for this range. The transcript needs repair before this clip can be created.",
    "CAPTION_NO_SPEECH": "This range has no captionable speech. Choose a range containing speech.",
}

TIMEOUT_MESSAGES: dict[Stage, str] = {
    "ingestion": "Video ingestion exceeded its time limit. Submit the video again to retry.",
    "transcription": "Transcription exceeded its time limit. Submit the video again to retry.",
    "analysis": "AI analysis exceeded its time limit. Submit the video again to retry.",
    "rendering": "Clip rendering exceeded its time limit. Retry the affected clip.",
}

LIMIT_MESSAGES = {
    "SOURCE_DURATION_LIMIT": "The source duration is unavailable or exceeds the configured processing limit. Choose a shorter video with a known duration.",
    "CLIP_TOO_LONG": "The clip exceeds the configured duration limit. Choose a shorter range.",
}


class WorkloadLimitError(ValueError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(LIMIT_MESSAGES[code])

# Only these application-authored validation/conflict messages may cross the API.
CLIENT_MESSAGES = {
    **LIMIT_MESSAGES,
    "PROCESSING_BUSY": "Processing capacity is full. Please try again shortly.",
    "STORAGE_BUDGET": "Storage capacity is full or reserved for existing work. Remove an old project or try again later.",
    "INVALID_REQUEST": "Invalid request. Please check your input.",
    "INVALID_URL": "Enter a valid YouTube video URL.",
    "INVALID_RANGE": "Clip end must be greater than clip start.",
    "RANGE_EXCEEDS_VIDEO": "Clip timestamps exceed the source video duration",
    "PROJECT_NOT_FOUND": "Project not found",
    "MOMENT_NOT_FOUND": "Moment not found in this project",
    "CLIP_NOT_FOUND": "Clip not found in this project",
    "PROJECT_BUSY": "Project processing is already in progress",
    "ANALYSIS_PENDING": "Project analysis is not complete",
    "SOURCE_MISSING": "The downloaded source video is missing",
    "CLIP_BUSY": "This clip is already being rendered",
    "CLIP_PENDING": "Clip is not ready",
    "CLIP_MISSING": "Clip file is not available",
}


class PublicError(BaseModel):
    code: str
    message: str


def processing_error(stage: Stage) -> PublicError:
    return PublicError(code="PROCESSING_ERROR", message=PUBLIC_MESSAGES[stage])


def interrupted_error(stage: Stage) -> PublicError:
    return PublicError(code="PROCESSING_INTERRUPTED", message=INTERRUPTED_MESSAGES[stage])


def failure_stage(exc: Exception, stage: Stage) -> Stage:
    return "database" if isinstance(exc, SQLAlchemyError) else stage


def stored_failure(message: str | None, fallback: Stage = "unknown") -> tuple[Stage, PublicError]:
    """Read new public-only fields, and safely classify pre-existing raw errors.

    Reusing error_message avoids a schema migration. No legacy text is returned;
    unrecognized historical failures receive the generic public message.
    """
    for stage, public_message in INTERRUPTED_MESSAGES.items():
        if message == public_message:
            return stage, interrupted_error(stage)
    for stage, public_message in TIMEOUT_MESSAGES.items():
        if message == public_message:
            return stage, PublicError(code="PROCESSING_TIMEOUT", message=public_message)
    for code, public_message in LIMIT_MESSAGES.items():
        if message == public_message:
            return ("ingestion" if code == "SOURCE_DURATION_LIMIT" else "rendering"), PublicError(code=code, message=public_message)
    for code, public_message in CAPTION_MESSAGES.items():
        if message == public_message:
            return "rendering", PublicError(code=code, message=public_message)
    for stage, public_message in PUBLIC_MESSAGES.items():
        if message == public_message:
            return stage, processing_error(stage)
    if fallback == "unknown" and message:
        legacy = message.lower()
        for stage, markers in (
            ("analysis", ("candidate detection", "candidate validation", "llm", "provider_error", "upstream_failed", "all routed attempts failed")),
            ("transcription", ("transcription", "transcript normalization", "whisper")),
            ("fetching", ("could not read youtube metadata",)),
            ("ingestion", ("could not download youtube", "ffmpeg audio extraction")),
        ):
            if any(marker in legacy for marker in markers):
                return stage, processing_error(stage)
    return fallback, processing_error(fallback)
