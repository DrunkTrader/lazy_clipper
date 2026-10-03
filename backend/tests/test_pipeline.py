from pathlib import Path

from backend.app.analysis.moments import CandidateMoment, CandidateScores
from backend.app.config import Settings
from backend.app.db import Base, get_engine, init_db, session_factory
from backend.app.models import Clip, Project
from backend.app.services.pipeline import Pipeline


class FakeYoutube:
    def get_metadata(self, url):
        from backend.app.services.youtube import VideoMetadata

        return VideoMetadata("abc123", "Test video", 120.0, None)

    def download_video(self, url, destination: Path):
        destination.mkdir(parents=True, exist_ok=True)
        path = destination / "video.mp4"
        path.write_bytes(b"not a real video in a unit test")
        return path


class FakeMedia:
    def extract_audio(self, source_path, audio_path):
        audio_path.parent.mkdir(parents=True, exist_ok=True)
        audio_path.write_bytes(b"audio")
        return audio_path

    def render_clip(self, source_path, output_path, start, end):
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"unit-test clip")
        return output_path


class FailingMedia(FakeMedia):
    def extract_audio(self, source_path, audio_path):
        raise RuntimeError("ffmpeg failed")


class FakeTranscriber:
    def transcribe(self, audio_path):
        return [
            {"start": 0, "end": 4, "text": "Here is a useful standalone idea."},
            {"start": 5, "end": 8, "text": "It has a clear payoff for viewers."},
        ]


class InvalidTranscriber:
    def transcribe(self, audio_path):
        return [{"start": 4, "end": 4, "text": "invalid"}]


class FakeAnalyzer:
    def analyze(self, chunks):
        return [
            CandidateMoment(
                start=0,
                end=8,
                title="A useful idea",
                description="A concise standalone idea.",
                reason="It has a setup and payoff.",
                scores=CandidateScores(
                    hook=8,
                    clarity=8,
                    standalone=9,
                    novelty=7,
                    emotional_interest=6,
                    payoff=9,
                ),
            )
        ]


def test_pipeline_persists_transcript_and_moments(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'pipeline.db'}"
    settings = Settings(database_url=database_url, storage_dir=tmp_path / "storage")
    engine = get_engine(database_url)
    Base.metadata.drop_all(engine)
    init_db(database_url)
    sessions = session_factory(database_url)
    with sessions() as session:
        project = Project(source_url="https://youtu.be/abc123")
        session.add(project)
        session.commit()
        project_id = project.id

    Pipeline(
        settings,
        youtube=FakeYoutube(),
        media=FakeMedia(),
        transcriber=FakeTranscriber(),
        analyzer=FakeAnalyzer(),
        make_session=sessions,
    ).run(project_id)

    with sessions() as session:
        project = session.get(Project, project_id)
        assert project.status == "READY"
        assert len(project.transcript_segments) == 2
        assert len(project.moments) == 1
        assert project.moments[0].score > 0
        assert project.clips == []
        moment_id = project.moments[0].id

    # Clip rendering is an explicit user action, not part of ingestion.
    with sessions() as session:
        clip = Clip(project_id=project_id, moment_id=moment_id, start=0, end=8)
        session.add(clip)
        session.commit()
        clip_id = clip.id
    Pipeline(settings, media=FakeMedia(), make_session=sessions).render_clip(project_id, clip_id)
    with sessions() as session:
        clip = session.get(Clip, clip_id)
        assert clip.status == "READY"
        assert clip.moment_id == moment_id
        assert Path(clip.output_path).is_file()

    class UnexpectedCall:
        def __getattr__(self, name):
            raise AssertionError(f"Persisted stage was rerun: {name}")

    # Simulate an explicit retry after a later failure. Previously this always
    # redownloaded the video and deleted/replaced transcript and moment rows.
    with sessions() as session:
        project = session.get(Project, project_id)
        project.status = "FAILED"
        source_mtime = Path(project.video.source_path).stat().st_mtime_ns
        session.commit()
    unused = UnexpectedCall()
    pipeline = Pipeline(settings, youtube=unused, media=unused, transcriber=unused, analyzer=unused, make_session=sessions)
    pipeline.run(project_id)
    pipeline.run(project_id)  # READY projects are also no-ops.
    with sessions() as session:
        project = session.get(Project, project_id)
        assert project.status == "READY"
        assert len(project.clips) == 1
        assert project.clips[0].id == clip_id
        assert Path(project.video.source_path).stat().st_mtime_ns == source_mtime


def test_pipeline_fails_when_normalization_removes_all_transcript_data(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'pipeline.db'}"
    settings = Settings(database_url=database_url, storage_dir=tmp_path / "storage")
    engine = get_engine(database_url)
    Base.metadata.drop_all(engine)
    init_db(database_url)
    sessions = session_factory(database_url)
    with sessions() as session:
        project = Project(source_url="https://youtu.be/abc123")
        session.add(project)
        session.commit()
        project_id = project.id

    Pipeline(
        settings,
        youtube=FakeYoutube(),
        media=FakeMedia(),
        transcriber=InvalidTranscriber(),
        make_session=sessions,
    ).run(project_id)

    with sessions() as session:
        project = session.get(Project, project_id)
        assert project.status == "FAILED"
        assert project.error_message == "Transcript normalization produced no valid segments"


def test_pipeline_keeps_metadata_when_media_fails(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'pipeline.db'}"
    settings = Settings(database_url=database_url, storage_dir=tmp_path / "storage")
    engine = get_engine(database_url)
    Base.metadata.drop_all(engine)
    init_db(database_url)
    sessions = session_factory(database_url)
    with sessions() as session:
        project = Project(source_url="https://youtu.be/abc123")
        session.add(project)
        session.commit()
        project_id = project.id

    Pipeline(settings, youtube=FakeYoutube(), media=FailingMedia(), make_session=sessions).run(project_id)

    with sessions() as session:
        project = session.get(Project, project_id)
        assert project.status == "FAILED"
        assert project.title == "Test video"
        assert project.video is not None
        assert project.video.title == "Test video"
        assert project.video.source_path is not None
        assert project.video.audio_path is None
