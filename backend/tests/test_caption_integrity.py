"""F04 behavioral regressions; known failures remain explicit until phase 4."""
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from backend.app.analysis.transcript import normalize_segments
from backend.app.analysis.transcript import require_word_alignment
from backend.app.config import Settings
from backend.app.services.captions import build_ass
from backend.app.services.pipeline import Pipeline


def timed_words():
    return [{"word": text, "start": index * 10.0, "end": index * 10.0 + 5}
            for index, text in enumerate(["Hello", ",", "world!", "It's", "aligned."])]


def test_long_segment_keeps_timing_despite_punctuation_tokenization_mismatch():
    words = timed_words()
    normalized = normalize_segments([{"start": 0, "end": 60, "text": "Hello, world! It's aligned.", "words": words}])
    assert [word for segment in normalized for word in segment.get("words", [])] == words
    assert all(segment["end"] > segment["start"] for segment in normalized)


def test_aligned_split_preserves_each_original_word_exactly_once():
    words = timed_words()
    normalized = normalize_segments([{
        "start": 0, "end": 60, "text": " ".join(w["word"] for w in words), "words": words,
    }])
    assert [word for segment in normalized for word in segment.get("words", [])] == words


@pytest.mark.parametrize("words", [
    [], [{"word": "outside", "start": 30, "end": 31}],
    [{"word": "too brief", "start": 0.001, "end": 0.004}],
])
def test_caption_builder_rejects_zero_dialogue_events(words):
    with pytest.raises(ValueError):
        build_ass(words, 0, 20)


@pytest.mark.parametrize("segments,code", [
    ([{"start": 0, "end": 20, "text": "Speech without word timing", "words": None}], "CAPTION_ALIGNMENT_MISSING"),
    ([{"start": 0, "end": 20, "text": "Mismatched timing", "words": [{"word": "mismatched", "start": 30, "end": 40}]}], "CAPTION_ALIGNMENT_MISSING"),
    ([{"start": 30, "end": 40, "text": "Speech elsewhere", "words": [{"word": "elsewhere", "start": 30, "end": 40}]}], "CAPTION_NO_SPEECH"),
])
def test_pipeline_does_not_render_a_captioned_clip_without_selected_words(tmp_path, segments, code):
    media = Mock()
    pipeline = Pipeline(Settings(_env_file=None, storage_dir=tmp_path), media=media)
    project = SimpleNamespace(transcript_segments=segments)
    clip = SimpleNamespace(id="clip", start=0, end=20)
    with pytest.raises(ValueError) as error:
        pipeline._render_clip_media(project, clip, tmp_path / "source.mp4", tmp_path)
    assert error.value.code == code
    media.render_clip.assert_not_called()
    assert not (tmp_path / "clips" / "clip.ass").exists()


def test_missing_word_alignment_is_a_transcription_failure(tmp_path):
    from backend.app.db import get_engine, init_db, session_factory
    from backend.app.models import Project
    from backend.tests.test_pipeline import FakeMedia, FakeYoutube

    settings = Settings(_env_file=None, database_url=f"sqlite:///{tmp_path / 'alignment.db'}", storage_dir=tmp_path)
    init_db(settings.database_url)
    sessions = session_factory(settings.database_url)
    with sessions() as session:
        project = Project(source_url="https://youtu.be/dQw4w9WgXcQ")
        session.add(project)
        session.commit()
        project_id = project.id
    analyzer = Mock()
    analyzer.analyze.return_value = []
    transcriber = SimpleNamespace(transcribe=lambda _: [{"start": 0, "end": 20, "text": "Unaligned speech"}])
    Pipeline(settings, youtube=FakeYoutube(), media=FakeMedia(), transcriber=transcriber,
             analyzer=analyzer, make_session=sessions).run(project_id)
    try:
        with sessions() as session:
            project = session.get(Project, project_id)
            assert project.status == "FAILED"
            assert project.error_message == "Internal server error during transcription."
            analyzer.analyze.assert_not_called()
    finally:
        get_engine(settings.database_url).dispose()


def test_timed_split_preserves_speaker_and_does_not_depend_on_whitespace():
    words = timed_words()
    result = normalize_segments([{"start": 0, "end": 60, "text": "无空格文本", "speaker": "Guest", "words": words}])
    assert len(result) > 1
    assert all(row["speaker"] == "Guest" for row in result)
    assert [word for row in result for word in row["words"]] == words


def test_nonfinite_alignment_is_not_clamped_into_valid_speech():
    with pytest.raises(ValueError):
        require_word_alignment([{
            "text": "Invalid timing", "words": [{"word": "Invalid", "start": float("nan"), "end": 1}],
        }])


def test_no_speech_cannot_turn_a_clip_into_ready(tmp_path):
    from backend.app.db import get_engine, init_db, session_factory
    from backend.app.models import Clip, Moment, Project, TranscriptSegment, Video
    from backend.app.schemas import ClipResponse

    settings = Settings(_env_file=None, database_url=f"sqlite:///{tmp_path / 'silence.db'}", storage_dir=tmp_path)
    init_db(settings.database_url)
    sessions = session_factory(settings.database_url)
    with sessions() as session:
        project = Project(source_url="https://youtu.be/dQw4w9WgXcQ", status="RENDERING")
        session.add(project)
        session.flush()
        source = settings.project_storage(project.id) / "source" / "video.mp4"
        source.write_bytes(b"source fixture")
        project.video = Video(duration=60, source_path=str(source))
        project.transcript_segments.append(TranscriptSegment(
            segment_index=0, start=30, end=40, text="Speech elsewhere",
            words=[{"word": "elsewhere", "start": 30, "end": 40}],
        ))
        moment = Moment(title="Silent", description="Fixture", reason="Fixture", start=0, end=20, score=8)
        project.moments.append(moment)
        session.flush()
        clip = Clip(project_id=project.id, moment_id=moment.id, start=0, end=20, status="QUEUED")
        session.add(clip)
        session.commit()
        project_id, clip_id = project.id, clip.id
    media = Mock()
    Pipeline(settings, media=media, make_session=sessions).render_clip(project_id, clip_id)
    try:
        with sessions() as session:
            clip = session.get(Clip, clip_id)
            assert clip.status == "FAILED"
            assert ClipResponse.model_validate(clip).error.code == "CAPTION_NO_SPEECH"
            assert session.get(Project, project_id).status == "READY"
            media.render_clip.assert_not_called()
    finally:
        get_engine(settings.database_url).dispose()
