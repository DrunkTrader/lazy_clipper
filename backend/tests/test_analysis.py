from backend.app.analysis.moments import CandidateMoment, CandidateScores, calculate_composite_score, deduplicate_candidates
from backend.app.analysis.transcript import chunk_segments, normalize_segments


def scores(hook: float) -> CandidateScores:
    return CandidateScores(
        hook=hook,
        clarity=hook,
        standalone=hook,
        novelty=hook,
        emotional_interest=hook,
        payoff=hook,
    )


def candidate(start: float, end: float, hook: float, title: str) -> CandidateMoment:
    return CandidateMoment(
        start=start,
        end=end,
        title=title,
        description="A complete idea.",
        reason="It has a setup and payoff.",
        scores=scores(hook),
    )


def test_normalization_removes_bad_segments_merges_short_segments_and_splits_long_ones():
    result = normalize_segments([
        {"start": 3, "end": 3, "text": "invalid"},
        {"start": 0, "end": 0.4, "text": "A useful"},
        {"start": 0.5, "end": 2.0, "text": "idea here."},
        {"start": 10, "end": 80, "text": " ".join(f"word{i}" for i in range(70))},
    ], max_segment_duration=30)

    assert result[0]["start"] == 0
    assert result[0]["end"] == 2
    assert result[0]["text"] == "A useful idea here."
    assert len(result[1:]) == 3
    assert all(item["end"] > item["start"] for item in result)


def test_chunking_has_overlap():
    segments = [
        {"start": 0, "end": 60, "text": "one"},
        {"start": 55, "end": 120, "text": "two"},
        {"start": 115, "end": 180, "text": "three"},
    ]
    chunks = chunk_segments(segments, window_seconds=120, overlap_seconds=30)
    assert len(chunks) == 2
    assert chunks[0]["end"] == 120
    assert chunks[1]["start"] == 90
    assert "two" in chunks[0]["text"] and "two" in chunks[1]["text"]


def test_score_and_deduplication_are_deterministic():
    assert calculate_composite_score(scores(8)) == 8
    duplicate_low = candidate(10, 50, 5, "low")
    duplicate_high = candidate(15, 48, 9, "high")
    separate = candidate(100, 140, 7, "separate")
    result = deduplicate_candidates([duplicate_low, separate, duplicate_high])
    assert [item.title for item in result] == ["high", "separate"]
