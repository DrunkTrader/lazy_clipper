"""OpenAI-compatible, timestamp-grounded moment analysis."""
import json
import re
from pathlib import Path
from typing import Any

from ..analysis.moments import (
    MAX_CANDIDATES_PER_CHUNK,
    MAX_MOMENT_SECONDS,
    MIN_MOMENT_SECONDS,
    CandidateMoment,
    DetectionResponse,
    ValidationResponse,
    deduplicate_candidates,
)
from ..config import Settings


class LLMConfigurationError(RuntimeError):
    pass


class LLMResponseError(RuntimeError):
    pass


_PROMPTS = Path(__file__).with_name("prompts")


class LLMClient:
    """Small adapter around the OpenAI SDK usable with any compatible gateway."""

    def __init__(self, settings: Settings):
        if not settings.llm_base_url or not settings.llm_api_key or not settings.llm_model:
            raise LLMConfigurationError("LLM_BASE_URL, LLM_API_KEY, and LLM_MODEL are required for analysis")
        self._settings = settings
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise LLMConfigurationError("The openai package is required for analysis") from exc
        self._client = OpenAI(
            api_key=settings.llm_api_key,
            base_url=settings.llm_base_url,
            timeout=getattr(settings, "llm_timeout", 120.0),
            max_retries=getattr(settings, "llm_max_retries", 2),
        )

    def _json_completion(self, messages: list[dict[str, str]], stage: str, validator=None) -> dict[str, Any]:
        """Request JSON, correcting one malformed response and then fail cleanly.

        response_format is deliberately not used: a number of OpenAI-compatible
        providers reject it even though they support the chat completions API.
        """
        correction = (
            "Your previous response was not valid for the requested schema. "
            "Return ONLY valid JSON, with the required top-level candidates array; "
            "do not use markdown or prose."
        )
        last_error: Exception | None = None
        for attempt in range(2):
            try:
                request_messages = messages if attempt == 0 else messages + [{"role": "user", "content": correction}]
                response = self._client.chat.completions.create(
                    model=self._settings.llm_model,
                    messages=request_messages,
                    temperature=getattr(self._settings, "llm_temperature", 0.2),
                    max_tokens=getattr(self._settings, "llm_max_tokens", 4000),
                )
            except Exception as exc:
                # The SDK already applies its configured bounded retries for
                # transport/rate-limit failures. Do not misreport a provider
                # outage (for example HTTP 503) as malformed model JSON.
                raise LLMResponseError(f"{stage} request failed: {exc}") from exc
            try:
                content = response.choices[0].message.content
                if not content:
                    raise ValueError("empty response")
                cleaned = content.strip()
                fenced = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", cleaned, flags=re.DOTALL | re.IGNORECASE)
                if fenced:
                    cleaned = fenced.group(1)
                parsed = json.loads(cleaned)
                if not isinstance(parsed, dict) or "candidates" not in parsed or not isinstance(parsed["candidates"], list):
                    raise ValueError("response must contain a candidates array")
                if validator is not None:
                    validator(parsed)
                return parsed
            except Exception as exc:
                last_error = exc
        # The chained validation error retains diagnostics without copying
        # model/user payloads into the wrapper's exception message.
        raise LLMResponseError(f"{stage} response was not valid JSON/schema after one correction") from last_error

    def detect(self, segments: list[dict[str, Any]]) -> DetectionResponse:
        transcript = _format_segments(segments)
        prompt = _read_prompt("candidate_detection.txt") + transcript
        payload = self._json_completion(
            [{"role": "system", "content": "You identify honest clip candidates from transcripts."}, {"role": "user", "content": prompt}],
            "candidate detection",
            DetectionResponse.model_validate,
        )
        return DetectionResponse.model_validate(payload)

    def validate(self, segments: list[dict[str, Any]], candidates: list[dict[str, Any]]) -> ValidationResponse:
        transcript = _format_segments(segments)
        proposed = json.dumps(candidates, ensure_ascii=False, separators=(",", ":"))
        prompt = _read_prompt("candidate_validation.txt") + proposed + "\n\nSOURCE SEGMENTS:\n" + transcript

        def validate_reviews(payload):
            review = ValidationResponse.model_validate(payload)
            review.require_candidate_ids(item["candidate_id"] for item in candidates)

        payload = self._json_completion(
            [{"role": "system", "content": "You validate transcript clip candidates rigorously."}, {"role": "user", "content": prompt}],
            "candidate validation",
            validate_reviews,
        )
        return ValidationResponse.model_validate(payload)

def _read_prompt(name: str) -> str:
    return (_PROMPTS / name).read_text(encoding="utf-8")


def _format_segments(segments: list[dict[str, Any]]) -> str:
    """Make timestamps and segment identity impossible to lose in the prompt."""
    lines = []
    for index, segment in enumerate(segments):
        lines.append(f"[{index}] {float(segment['start']):.3f}-{float(segment['end']):.3f}: {str(segment['text']).strip()}")
    return "\n".join(lines)


class MomentAnalyzer:
    def __init__(self, settings: Settings, client: LLMClient | None = None):
        self.client = client or LLMClient(settings)
        self.settings = settings

    def analyze(self, chunks: list[dict[str, Any]]) -> list[CandidateMoment]:
        candidates: list[CandidateMoment] = []
        for chunk in chunks:
            segments = list(chunk.get("segments") or [])
            if not segments:
                continue
            detected = self.client.detect(segments)
            grounded: list[dict[str, Any]] = []
            for item in detected.candidates[:MAX_CANDIDATES_PER_CHUNK]:
                if item.start_segment > item.end_segment or item.end_segment >= len(segments):
                    continue
                source = segments[item.start_segment : item.end_segment + 1]
                start = float(source[0]["start"])
                end = float(source[-1]["end"])
                if end <= start or end - start < MIN_MOMENT_SECONDS or end - start > MAX_MOMENT_SECONDS:
                    continue
                grounded.append({
                    "candidate_id": len(grounded),
                    "start": start,
                    "end": end,
                    "title": item.title,
                    "description": item.description,
                    "reason": item.reason,
                    # The model sees chunk-local indexes, but persisted
                    # references must identify the original transcript rows.
                    "source_segments": [
                        int(source_item.get("segment_index", index))
                        for index, source_item in enumerate(source)
                    ],
                })
            if not grounded:
                continue
            review = self.client.validate(segments, grounded)
            # Keep the analyzer boundary explicit for alternate client adapters.
            try:
                review.require_candidate_ids(item["candidate_id"] for item in grounded)
            except ValueError as exc:
                raise LLMResponseError("Candidate review identity did not match the proposal") from exc
            reviews = {item.candidate_id: item for item in review.candidates}
            for item in grounded:
                verdict = reviews.get(item["candidate_id"])
                if verdict is None or not verdict.accepted or verdict.scores is None:
                    continue
                candidates.append(CandidateMoment(
                    start=item["start"],
                    end=item["end"],
                    title=item["title"],
                    description=item["description"],
                    reason=item["reason"],
                    source_segments=item["source_segments"],
                    scores=verdict.scores,
                ))
        return deduplicate_candidates(candidates)[: self.settings.max_moments]
