"""Validation, deterministic scoring, and interval deduplication."""
from typing import Any, Iterable

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


MAX_CANDIDATES_PER_CHUNK = 5
MIN_MOMENT_SECONDS = 15
MAX_MOMENT_SECONDS = 180
DIMENSION_WEIGHTS = {
    "hook": 0.25,
    "clarity": 0.20,
    "standalone": 0.20,
    "novelty": 0.15,
    "emotional_interest": 0.10,
    "payoff": 0.10,
}


class StructuredModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False, str_strip_whitespace=True)


class CandidateScores(StructuredModel):
    hook: float = Field(ge=0, le=10)
    clarity: float = Field(ge=0, le=10)
    standalone: float = Field(ge=0, le=10)
    novelty: float = Field(ge=0, le=10)
    emotional_interest: float = Field(ge=0, le=10)
    payoff: float = Field(ge=0, le=10)


class CandidateMoment(StructuredModel):
    start: float = Field(ge=0, strict=True)
    end: float = Field(gt=0, strict=True)
    title: str = Field(min_length=1, max_length=500)
    description: str = Field(min_length=1, max_length=4000)
    reason: str = Field(min_length=1, max_length=4000)
    scores: CandidateScores
    source_segments: list[int] = Field(default_factory=list)

    @field_validator("end")
    @classmethod
    def end_after_start(cls, value: float, info):
        start = info.data.get("start")
        if start is not None and value <= start:
            raise ValueError("end must be greater than start")
        return value


class DetectedCandidate(StructuredModel):
    start_segment: int = Field(ge=0, strict=True)
    end_segment: int = Field(ge=0, strict=True)
    title: str = Field(min_length=1, max_length=500)
    description: str = Field(min_length=1, max_length=4000)
    reason: str = Field(min_length=1, max_length=4000)


class DetectionResponse(StructuredModel):
    candidates: list[DetectedCandidate] = Field(max_length=MAX_CANDIDATES_PER_CHUNK)


class CandidateReview(StructuredModel):
    candidate_id: int = Field(ge=0, strict=True)
    accepted: bool = Field(strict=True)
    reason: str = Field(min_length=1, max_length=4000)
    scores: CandidateScores | None = None

    @model_validator(mode="after")
    def accepted_requires_scores(self):
        if self.accepted and self.scores is None:
            raise ValueError("accepted candidates require all six scores")
        return self


class ValidationResponse(StructuredModel):
    candidates: list[CandidateReview] = Field(max_length=MAX_CANDIDATES_PER_CHUNK)

    def require_candidate_ids(self, proposed_ids: Iterable[int]) -> None:
        expected = list(proposed_ids)
        actual = [item.candidate_id for item in self.candidates]
        if len(set(expected)) != len(expected) or len(actual) != len(expected) or set(actual) != set(expected):
            raise ValueError("Reviews must uniquely match every proposed candidate ID")


def calculate_composite_score(scores: CandidateScores) -> float:
    values = scores.model_dump()
    return round(sum(DIMENSION_WEIGHTS[name] * float(values[name]) for name in DIMENSION_WEIGHTS), 4)


def _overlap_ratio(left: Any, right: Any) -> float:
    overlap = max(0.0, min(float(left.end), float(right.end)) - max(float(left.start), float(right.start)))
    shortest = min(float(left.end) - float(left.start), float(right.end) - float(right.start))
    return overlap / shortest if shortest > 0 else 0.0


def deduplicate_candidates(candidates: Iterable[CandidateMoment], overlap_threshold: float = 0.5) -> list[CandidateMoment]:
    """Keep the highest scoring candidate for substantially overlapping intervals."""
    if not 0 <= overlap_threshold <= 1:
        raise ValueError("overlap_threshold must be between 0 and 1")
    ranked = sorted(candidates, key=lambda item: (-calculate_composite_score(item.scores), item.start, item.end, item.title))
    kept: list[CandidateMoment] = []
    for candidate in ranked:
        if not any(_overlap_ratio(candidate, existing) >= overlap_threshold for existing in kept):
            kept.append(candidate)
    return sorted(kept, key=lambda item: (-calculate_composite_score(item.scores), item.start, item.end))
