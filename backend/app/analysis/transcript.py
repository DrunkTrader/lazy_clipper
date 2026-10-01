"""Deterministic transcript cleanup and overlapping window generation."""
from dataclasses import dataclass
import math
import re
from typing import Any, Iterable


@dataclass(frozen=True)
class NormalizedSegment:
    start: float
    end: float
    text: str
    speaker: str | None = None
    words: list[dict[str, Any]] | None = None

    def as_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {"start": self.start, "end": self.end, "text": self.text}
        if self.speaker is not None:
            result["speaker"] = self.speaker
        if self.words:
            result["words"] = self.words
        return result


def _value(item: Any, key: str, default: Any = None) -> Any:
    if isinstance(item, dict):
        return item.get(key, default)
    return getattr(item, key, default)


def _clean_words(words: Any) -> list[dict[str, Any]] | None:
    if not isinstance(words, list):
        return None
    result = []
    for word in words:
        text = str(_value(word, "word", "")).strip()
        start, end = _value(word, "start"), _value(word, "end")
        if not text or start is None or end is None:
            continue
        try:
            start, end = float(start), float(end)
        except (TypeError, ValueError):
            continue
        if math.isfinite(start) and math.isfinite(end) and end >= start:
            result.append({"word": text, "start": start, "end": end})
    return result or None


def _raw_segments(raw_segments: Iterable[Any]) -> list[NormalizedSegment]:
    cleaned: list[NormalizedSegment] = []
    for item in raw_segments:
        text = re.sub(r"\s+", " ", str(_value(item, "text", "") or "")).strip()
        try:
            start, end = float(_value(item, "start")), float(_value(item, "end"))
        except (TypeError, ValueError):
            continue
        if not text or not math.isfinite(start) or not math.isfinite(end) or end <= start:
            continue
        cleaned.append(NormalizedSegment(max(0.0, start), end, text, _value(item, "speaker"), _clean_words(_value(item, "words"))))
    return sorted(cleaned, key=lambda segment: (segment.start, segment.end))


def normalize_segments(
    raw_segments: Iterable[Any],
    *,
    merge_gap: float = 0.8,
    min_duration: float = 1.0,
    min_words: int = 3,
    max_segment_duration: float = 30.0,
) -> list[dict[str, Any]]:
    """Clean, merge tiny adjacent segments, and split overly long segments.

    Timing is never invented for normal segments. Long segments without word
    timestamps use proportional boundaries only as a coarse analysis fallback;
    aligned words always remain authoritative when they are available.
    """
    source = _raw_segments(raw_segments)
    merged: list[NormalizedSegment] = []
    for segment in source:
        if merged and (segment.start - merged[-1].end) <= merge_gap and (
            merged[-1].end - merged[-1].start <= min_duration
            or len(merged[-1].text.split()) <= min_words
        ):
            previous = merged.pop()
            words = (previous.words or []) + (segment.words or []) or None
            merged.append(NormalizedSegment(previous.start, max(previous.end, segment.end), f"{previous.text} {segment.text}", previous.speaker or segment.speaker, words))
        else:
            merged.append(segment)

    result: list[dict[str, Any]] = []
    for segment in merged:
        duration = segment.end - segment.start
        words = segment.text.split()
        if duration <= max_segment_duration or len(words) < 2:
            result.append(segment.as_dict())
            continue
        count = max(1, math.ceil(duration / max_segment_duration))
        # Real word timestamps are preferred; proportional split is deliberately
        # coarse and only used so very long unaligned transcript data remains
        # usable for LLM chunking.
        if segment.words and len(segment.words) == len(words):
            for index in range(count):
                group = segment.words[round(len(segment.words) * index / count):round(len(segment.words) * (index + 1) / count)]
                if not group:
                    continue
                result.append({"start": group[0]["start"], "end": group[-1]["end"], "text": " ".join(word["word"] for word in group), "words": group})
        else:
            for index in range(count):
                left, right = round(len(words) * index / count), round(len(words) * (index + 1) / count)
                left, right = left, max(left + 1, right)
                start = segment.start + duration * index / count
                end = segment.start + duration * (index + 1) / count
                result.append({"start": start, "end": end, "text": " ".join(words[left:right]), **({"speaker": segment.speaker} if segment.speaker else {})})
    return result


def chunk_segments(
    segments: list[dict[str, Any]], *, window_seconds: float = 300.0, overlap_seconds: float = 45.0
) -> list[dict[str, Any]]:
    """Return deterministic overlapping transcript windows with grounded times."""
    if not segments:
        return []
    if window_seconds <= 0 or overlap_seconds < 0 or overlap_seconds >= window_seconds:
        raise ValueError("overlap_seconds must be non-negative and smaller than window_seconds")
    ordered = sorted(segments, key=lambda item: float(item["start"]))
    ordered = [dict(item, segment_index=item.get("segment_index", index)) for index, item in enumerate(ordered)]
    first, last = float(ordered[0]["start"]), max(float(item["end"]) for item in ordered)
    windows: list[dict[str, Any]] = []
    start = first
    previous_ids: tuple[int, ...] | None = None
    while start <= last:
        end = start + window_seconds
        included = [item for item in ordered if float(item["end"]) > start and float(item["start"]) < end]
        ids = tuple(int(item["segment_index"]) for item in included)
        if included and ids != previous_ids:
            windows.append({
                "start": start,
                "end": min(end, last),
                "segments": included,
                "text": "\n".join(
                    f'[{item["segment_index"]}] {float(item["start"]):.3f}-{float(item["end"]):.3f}: {item["text"]}'
                    for item in included
                ),
            })
            previous_ids = ids
        if end >= last:
            break
        start = end - overlap_seconds
    return windows


# Descriptive aliases make the utility convenient for callers and tests.
normalize_transcript = normalize_segments
chunk_transcript = chunk_segments
