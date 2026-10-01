"""Pydantic API contracts."""
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, computed_field


class IngestRequest(BaseModel):
    url: str = Field(min_length=1, max_length=2048)


class IngestResponse(BaseModel):
    project_id: str
    status: str


class VideoResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    youtube_id: str | None = None
    title: str | None = None
    duration: float | None = None
    thumbnail_url: str | None = None
    media_url: str | None = None


class ProjectResponse(BaseModel):
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


class StatusResponse(BaseModel):
    project_id: str
    status: str
    message: str | None = None
    error: str | None = None


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
