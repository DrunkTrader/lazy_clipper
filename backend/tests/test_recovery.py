"""Restart regressions using a real temporary DB and a deliberately idle executor."""
from pathlib import Path
from concurrent.futures import Future
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from backend.app import main
from backend.app.config import get_settings, reset_settings_cache
from backend.app.db import get_engine, init_db, session_factory
from backend.app.models import Clip, Moment, Project, TranscriptSegment, Video
from backend.app.services.pipeline import Pipeline


URL = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"


class IdleExecutor:
    """Accept submissions without running them, simulating lost in-memory work."""

    def __init__(self, **kwargs):
        self.submitted = []

    def submit(self, runner, *args):
        self.submitted.append((runner, args))
        return Future()

    def shutdown(self, **kwargs):
        pass


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'recovery.db'}")
    monkeypatch.setenv("STORAGE_DIR", str(tmp_path / "storage"))
    monkeypatch.setenv("LLM_API_KEY", "unit-test-key")
    monkeypatch.setenv("LLM_BASE_URL", "http://gateway.test/v1")
    monkeypatch.setenv("LLM_MODEL", "unit-test-model")
    reset_settings_cache()
    get_engine.cache_clear()
    settings = get_settings()
    init_db()
    engine = get_engine()
    sessions = session_factory()
    monkeypatch.setattr(main, "ThreadPoolExecutor", IdleExecutor)
    yield SimpleNamespace(settings=settings, sessions=sessions)
    engine.dispose()
    get_engine.cache_clear()
    reset_settings_cache()


def saved_project(runtime, status="ANALYZING", clip_status=None):
    with runtime.sessions() as session:
        project = Project(source_url=URL, status=status, analysis_completed=True)
        session.add(project)
        session.flush()
        root = runtime.settings.project_storage(project.id)
        source = root / "source" / "video.mp4"
        audio = root / "audio" / "audio.wav"
        transcript = root / "transcript" / "transcript.json"
        for path in (source, audio, transcript):
            path.write_bytes(b"saved artifact")
        project.video = Video(youtube_id="dQw4w9WgXcQ", duration=60, source_path=str(source), audio_path=str(audio))
        project.transcript_segments.append(TranscriptSegment(
            start=0, end=20, text="Stored words", segment_index=0,
            words=[{"word": "Stored", "start": 0, "end": 10}, {"word": "words", "start": 10, "end": 20}],
        ))
        moment = Moment(title="Saved", description="Saved", reason="Saved", start=0, end=20, score=8)
        project.moments.append(moment)
        session.flush()
        clip_id = None
        if clip_status:
            clip = Clip(project_id=project.id, moment_id=moment.id, start=0, end=20, status=clip_status)
            session.add(clip)
            session.flush()
            clip_id = clip.id
            # An output can exist even if a crash prevented the READY commit.
            (root / "clips" / f"{clip.id}.mp4").write_bytes(b"published before crash")
        session.commit()
        return project.id, moment.id, clip_id, root


@pytest.mark.parametrize("status", ["QUEUED", "INGESTING", "TRANSCRIBING", "ANALYZING"])
def test_startup_marks_interrupted_ingestion_retryable(runtime, status):
    project_id, _, _, root = saved_project(runtime, status)
    before = {str(p): (p.read_bytes(), p.stat().st_mtime_ns) for p in root.rglob("*") if p.is_file()}
    with TestClient(main.app) as client:
        response = client.get(f"/api/v1/projects/{project_id}/status").json()
        assert response["status"] == "FAILED"
        assert response["error"]["code"] == "PROCESSING_INTERRUPTED"
        assert "interrupted" in response["error"]["message"].lower()
        assert main.app.state.pipeline_executor.submitted == []
    assert {str(p): (p.read_bytes(), p.stat().st_mtime_ns) for p in root.rglob("*") if p.is_file()} == before


@pytest.mark.parametrize("clip_status", ["QUEUED", "RENDERING"])
def test_startup_recovers_clip_and_explicit_retry_reuses_its_id(runtime, clip_status):
    project_id, moment_id, clip_id, root = saved_project(runtime, "RENDERING", clip_status)
    with TestClient(main.app) as client:
        assert client.get(f"/api/v1/projects/{project_id}/status").json()["status"] == "READY"
        clip = client.get(f"/api/v1/projects/{project_id}/clips").json()["clips"][0]
        assert clip["status"] == "FAILED"
        assert clip["error"]["code"] == "PROCESSING_INTERRUPTED"
        assert clip["media_url"] is None
        assert main.app.state.pipeline_executor.submitted == []
        assert (root / "clips" / f"{clip_id}.mp4").read_bytes() == b"published before crash"
        request = {"moment_id": moment_id, "start": 0, "end": 20}
        retry = client.post(f"/api/v1/projects/{project_id}/clips", json=request)
        assert retry.status_code == 202
        assert retry.json()["id"] == clip_id
        assert client.post(f"/api/v1/projects/{project_id}/clips", json=request).status_code == 409
        assert len(main.app.state.pipeline_executor.submitted) == 1


def test_commit_before_dispatch_loss_is_recoverable_only_after_restart(runtime):
    with TestClient(main.app) as client:
        first = client.post("/api/v1/ingest", json={"url": URL}).json()
        project_id = first["project_id"]
        assert first["status"] == "queued"
        for _ in range(2):
            assert client.get(f"/api/v1/projects/{project_id}/status").json()["status"] == "QUEUED"
            assert client.post("/api/v1/ingest", json={"url": URL}).json()["project_id"] == project_id
        assert len(main.app.state.pipeline_executor.submitted) == 1
    # New lifespan/executor, same persisted database, no former worker alive.
    with TestClient(main.app) as client:
        assert client.get(f"/api/v1/projects/{project_id}/status").json()["status"] == "FAILED"
        assert main.app.state.pipeline_executor.submitted == []
        retry = client.post("/api/v1/ingest", json={"url": URL})
        assert retry.status_code == 202
        assert retry.json() == {"project_id": project_id, "status": "queued"}
        assert len(client.get("/api/v1/projects").json()) == 1
        assert len(main.app.state.pipeline_executor.submitted) == 1


def test_recovered_ingestion_retry_keeps_completed_stages(runtime, monkeypatch):
    project_id, moment_id, _, root = saved_project(runtime)

    class UnexpectedService:
        def __getattr__(self, name):
            raise AssertionError(f"Saved stage rerun: {name}")

    unused = UnexpectedService()
    pipeline = Pipeline(runtime.settings, youtube=unused, media=unused, transcriber=unused,
                        analyzer=unused, make_session=runtime.sessions)
    monkeypatch.setattr(main.JobSupervisor, "ingest", lambda self, project_id: pipeline.run(project_id))
    with runtime.sessions() as session:
        transcript_ids = [row.id for row in session.get(Project, project_id).transcript_segments]
    with TestClient(main.app) as client:
        assert client.get(f"/api/v1/projects/{project_id}/status").json()["status"] == "FAILED"
        assert client.post("/api/v1/ingest", json={"url": URL}).status_code == 202
        runner, args = main.app.state.pipeline_executor.submitted[0]
        runner(*args)
        assert client.get(f"/api/v1/projects/{project_id}/status").json()["status"] == "READY"
    with runtime.sessions() as session:
        project = session.get(Project, project_id)
        assert [row.id for row in project.transcript_segments] == transcript_ids
        assert [row.id for row in project.moments] == [moment_id]
        assert Path(project.video.source_path).read_bytes() == b"saved artifact"


@pytest.mark.parametrize("status", ["READY", "FAILED"])
def test_startup_leaves_terminal_projects_and_completed_clips_unchanged(runtime, status):
    project_id, _, clip_id, root = saved_project(runtime, status, "READY")
    with runtime.sessions() as session:
        session.get(Clip, clip_id).output_path = str(root / "clips" / f"{clip_id}.mp4")
        session.commit()
        timestamp = session.get(Project, project_id).updated_at
    for _ in range(2):
        with TestClient(main.app):
            assert main.app.state.pipeline_executor.submitted == []
        with runtime.sessions() as session:
            project = session.get(Project, project_id)
            assert project.status == status
            assert project.updated_at == timestamp
            assert session.get(Clip, clip_id).status == "READY"


def test_dispatch_exception_remains_safe_and_explicitly_retryable(runtime, monkeypatch):
    with TestClient(main.app) as client:
        executor = main.app.state.pipeline_executor

        def fail(*args):
            raise RuntimeError("synthetic dispatch failure")

        with monkeypatch.context() as patch:
            patch.setattr(executor, "submit", fail)
            response = client.post("/api/v1/ingest", json={"url": URL})
            assert response.status_code == 503
            assert "synthetic dispatch failure" not in response.text
        project = client.get("/api/v1/projects").json()[0]
        assert project["status"] == "FAILED"
        retry = client.post("/api/v1/ingest", json={"url": URL})
        assert retry.json() == {"project_id": project["id"], "status": "queued"}
        assert len(executor.submitted) == 1


def test_clip_recovery_is_idempotent_and_keeps_other_ready_clips(runtime):
    project_id, _, clip_id, root = saved_project(runtime, "RENDERING", "RENDERING")
    with runtime.sessions() as session:
        moment = Moment(project_id=project_id, title="Complete", description="Saved", reason="Saved", start=20, end=40, score=8)
        session.add(moment)
        session.flush()
        output = root / "clips" / "complete.mp4"
        output.write_bytes(b"completed output")
        completed = Clip(project_id=project_id, moment_id=moment.id, start=20, end=40, status="READY", output_path=str(output))
        session.add(completed)
        session.commit()
        completed_id = completed.id
    with TestClient(main.app):
        pass
    with runtime.sessions() as session:
        assert session.get(Clip, clip_id).status == "FAILED"
        timestamp = session.get(Project, project_id).updated_at
    with TestClient(main.app):
        pass
    with runtime.sessions() as session:
        assert session.get(Project, project_id).updated_at == timestamp
        assert session.get(Clip, completed_id).status == "READY"
        assert Path(session.get(Clip, completed_id).output_path).read_bytes() == b"completed output"


@pytest.mark.parametrize("status", ["READY", "FAILED"])
def test_recovery_fails_orphan_clip_even_when_project_is_terminal(runtime, status):
    project_id, _, clip_id, _ = saved_project(runtime, status, "QUEUED")
    with TestClient(main.app):
        pass
    with runtime.sessions() as session:
        assert session.get(Project, project_id).status == status
        assert session.get(Clip, clip_id).status == "FAILED"


def test_recovery_database_failure_rolls_back_and_prevents_startup(runtime, monkeypatch):
    from sqlalchemy import event
    from sqlalchemy.orm import Session
    from unittest.mock import Mock

    project_id, _, clip_id, _ = saved_project(runtime, "RENDERING", "RENDERING")
    executor = Mock()
    monkeypatch.setattr(main, "ThreadPoolExecutor", executor)

    def fail_commit(session):
        raise RuntimeError("synthetic recovery commit failure")

    event.listen(Session, "before_commit", fail_commit)
    try:
        with pytest.raises(RuntimeError, match="synthetic recovery commit failure"):
            with TestClient(main.app):
                pass
    finally:
        event.remove(Session, "before_commit", fail_commit)
    executor.assert_not_called()
    with runtime.sessions() as session:
        assert session.get(Project, project_id).status == "RENDERING"
        assert session.get(Clip, clip_id).status == "RENDERING"
