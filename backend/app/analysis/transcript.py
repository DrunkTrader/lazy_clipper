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
        raw_text = _value(word, "word", _value(word, "text", ""))
        text = raw_text.strip() if isinstance(raw_text, str) else ""
        start, end = _value(word, "start"), _value(word, "end")
        if not text or start is None or end is None:
            continue
        try:
            start, end = float(start), float(end)
        except (TypeError, ValueError):
            continue
        if not math.isfinite(start) or not math.isfinite(end):
            continue
        start = max(0.0, start)
        if end > start:
            item = {"word": text, "start": start, "end": end}
            confidence = _value(word, "confidence")
            if confidence is not None:
                try:
                    confidence = float(confidence)
                except (TypeError, ValueError):
                    confidence = None
                if confidence is not None and math.isfinite(confidence):
                    item["confidence"] = confidence
            result.append(item)
    return result or None


def require_word_alignment(segments: Iterable[Any]) -> None:
    """Reject spoken segments with no usable alignment, independent of token counts."""
    for segment in segments:
        if not str(_value(segment, "text", "") or "").strip():
            continue
        words = _clean_words(_value(segment, "words"))
        if not words:
            raise ValueError("Transcript speech has no valid word timestamps")
        try:
            start, end = float(_value(segment, "start")), float(_value(segment, "end"))
        except (TypeError, ValueError):
            continue  # Normalization owns missing/invalid segment boundaries.
        if math.isfinite(start) and math.isfinite(end) and end > start and not any(
            word["end"] > start and word["start"] < end for word in words
        ):
            raise ValueError("Transcript word timestamps do not overlap their speech segment")


def _raw_segments(raw_segments: Iterable[Any]) -> list[NormalizedSegment]:
    cleaned: list[NormalizedSegment] = []
    for item in raw_segments:
        text = re.sub(r"\s+", " ", str(_value(item, "text", "") or "")).strip()
        try:
            start, end = float(_value(item, "start")), float(_value(item, "end"))
        except (TypeError, ValueError):
            continue
        if not text or not math.isfinite(start) or not math.isfinite(end) or end <= max(0.0, start):
            continue
        cleaned.append(NormalizedSegment(max(0.0, start), end, text, _value(item, "speaker"), _clean_words(_value(item, "words"))))
    return sorted(cleaned, key=lambda segment: (segment.start, segment.end))


def _split_timed_segment(segment: NormalizedSegment, maximum: float) -> list[dict[str, Any]]:
    """Use word timing as the boundary source; never match it to whitespace tokens."""
    groups: list[list[dict[str, Any]]] = []
    for word in sorted(segment.words or [], key=lambda item: (item["start"], item["end"])):
        if not groups or word["end"] - groups[-1][0]["start"] > maximum:
            groups.append([])
        groups[-1].append(word)
    return [NormalizedSegment(
        group[0]["start"], max(word["end"] for word in group),
        " ".join(word["word"] for word in group), segment.speaker, group,
    ).as_dict() for group in groups]


def _split_unaligned_segment(segment: NormalizedSegment, maximum: float) -> list[dict[str, Any]]:
    """Coarse text-only analysis fallback; never manufacture caption word timing."""
    words = segment.text.split()
    if len(words) < 2:
        return [segment.as_dict()]
    duration = segment.end - segment.start
    count = min(len(words), max(1, math.ceil(duration / maximum)))
    result = []
    for index in range(count):
        left, right = round(len(words) * index / count), round(len(words) * (index + 1) / count)
        result.append(NormalizedSegment(
            segment.start + duration * index / count,
            segment.start + duration * (index + 1) / count,
            " ".join(words[left:right]), segment.speaker,
        ).as_dict())
    return result


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
        if duration <= max_segment_duration:
            result.append(segment.as_dict())
        elif segment.words:
            result.extend(_split_timed_segment(segment, max_segment_duration))
        else:
            result.extend(_split_unaligned_segment(segment, max_segment_duration))
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
