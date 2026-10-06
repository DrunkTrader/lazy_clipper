"""Pydantic API contracts."""
from datetime import datetime
from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict, Field, computed_field, model_serializer, model_validator

from .errors import PublicError, Stage, stored_failure


class FailureFields(BaseModel):
    failed_stage: Stage | None = None
    error: PublicError | None = None
    keep_null_error: ClassVar[bool] = False

    @model_serializer(mode="wrap")
    def serialize_failure(self, handler):
        result = handler(self)
        # Preserve existing successful response shapes; add fields on failure.
        if self.failed_stage is None:
            result.pop("failed_stage", None)
        if self.error is None and not self.keep_null_error:
            result.pop("error", None)
        return result


class IngestRequest(BaseModel):
    url: str = Field(min_length=1, max_length=2048)


class IngestResponse(BaseModel):
    project_id: str
    status: str


class CreateClipRequest(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False)
    moment_id: str = Field(min_length=1, max_length=36)
    start: float = Field(ge=0)
    end: float = Field(gt=0)

    @model_validator(mode="after")
    def validate_range(self) -> "CreateClipRequest":
        if self.end <= self.start:
            raise ValueError("Clip end must be greater than clip start")
        return self


class VideoResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    youtube_id: str | None = None
    title: str | None = None
    duration: float | None = None
    thumbnail_url: str | None = None


class ProjectResponse(FailureFields):
    model_config = ConfigDict(from_attributes=True)

    id: str
    source_url: str
    title: str | None = None
    status: str
    status_message: str | None = None
    error_message: str | None = None
    created_at: datetime
    updated_at: datetime
    video: VideoResponse | None = None

    @model_validator(mode="after")
    def sanitize_failure(self):
        if self.status == "FAILED" or self.error_message:
            self.failed_stage, self.error = stored_failure(self.error_message)
            self.error_message = self.error.message
            self.status_message = self.error.message
        return self


class StatusResponse(FailureFields):
    keep_null_error: ClassVar[bool] = True
    project_id: str
    status: str
    message: str | None = None


class TranscriptSegmentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    start: float
    end: float
    text: str
    speaker: str | None = None
    segment_index: int
    words: list[dict[str, Any]] | None = None


class TranscriptResponse(BaseModel):
    project_id: str
    segments: list[TranscriptSegmentResponse]


class MomentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    title: str
    description: str
    start: float
    end: float
    score: float
    reason: str
    rank: int
    source_segments: list[int] | None = None
    dimensions: dict[str, float] | None = None

    @computed_field
    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)


class MomentsResponse(BaseModel):
    project_id: str
    moments: list[MomentResponse]


class ClipResponse(FailureFields):
    model_config = ConfigDict(from_attributes=True)

    id: str
    project_id: str
    moment_id: str
    start: float
    end: float
    status: str
    error_message: str | None = None
    media_url: str | None = None
    download_url: str | None = None

    @model_validator(mode="after")
    def sanitize_failure(self):
        if self.status == "FAILED" or self.error_message:
            self.failed_stage, self.error = stored_failure(self.error_message, "rendering")
            self.error_message = self.error.message
        return self


class ClipsResponse(BaseModel):
    project_id: str
    clips: list[ClipResponse]
