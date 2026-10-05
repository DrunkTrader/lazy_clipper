from concurrent.futures import Future

import pytest


@pytest.fixture
def api_client(tmp_path, monkeypatch):
    # Import the application only after selecting an isolated test database.
    database = tmp_path / "lazyclipper.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{database}")
    monkeypatch.setenv("STORAGE_DIR", str(tmp_path / "storage"))

    from backend.app import main
    from backend.app.config import reset_settings_cache
    from backend.app.db import get_engine

    reset_settings_cache()
    get_engine.cache_clear()
    monkeypatch.setattr(main.JobSupervisor, "ingest", lambda self, project_id: None)
    from fastapi.testclient import TestClient

    with TestClient(main.app) as client:
        yield client
    reset_settings_cache()
    get_engine.cache_clear()


def test_ingest_persists_project_and_exposes_status(api_client):
    response = api_client.post(
        "/api/v1/ingest",
        json={"url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ"},
    )
    assert response.status_code == 202
    project_id = response.json()["project_id"]

    project = api_client.get(f"/api/v1/projects/{project_id}")
    assert project.status_code == 200
    assert project.json()["source_url"] == "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
    assert project.json()["status"] == "QUEUED"

    status = api_client.get(f"/api/v1/projects/{project_id}/status")
    assert status.status_code == 200
    assert status.json()["status"] == "QUEUED"


def test_ingest_rejects_non_youtube_url(api_client):
    response = api_client.post("/api/v1/ingest", json={"url": "https://example.com/video"})
    assert response.status_code == 422


def test_reload_and_duplicate_submission_do_not_enqueue_again(api_client, monkeypatch):
    from backend.app.main import app

    submitted = []
    futures = []
    def accept(wrapped, runner, args):
        submitted.append(args[0])
        future = Future()
        futures.append(future)
        return future

    monkeypatch.setattr(app.state.pipeline_executor, "submit", accept)
    first = api_client.post("/api/v1/ingest", json={"url": "https://youtu.be/dQw4w9WgXcQ?t=5"}).json()
    project_id = first["project_id"]
    for _ in range(2):
        for suffix in ["", "/status", "/transcript", "/moments", "/clips"]:
            assert api_client.get(f"/api/v1/projects/{project_id}{suffix}").status_code == 200
    second = api_client.post("/api/v1/ingest", json={"url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ"})
    assert second.json()["project_id"] == project_id
    assert submitted == [project_id]
    assert len(api_client.get("/api/v1/projects").json()) == 1

    from backend.app.config import get_settings
    from backend.app.db import session_factory
    from backend.app.models import Project, Video

    with session_factory()() as session:
        project = session.get(Project, project_id)
        source = get_settings().project_storage(project_id) / "source" / "video.mp4"
        source.write_bytes(b"persisted source")
        project.video = Video(youtube_id="dQw4w9WgXcQ", source_path=str(source))
        project.status = "READY"
        session.commit()
    futures[0].set_result(None)
    assert api_client.post("/api/v1/ingest", json={"url": "https://youtu.be/dQw4w9WgXcQ"}).json()["status"] == "ready"
    assert submitted == [project_id]
    source.unlink()
    # A GET never repairs/reprocesses missing media; explicit submission can.
    assert api_client.get(f"/api/v1/projects/{project_id}").json()["video"]["media_url"] is None
    assert submitted == [project_id]
    repaired = api_client.post("/api/v1/ingest", json={"url": "https://youtu.be/dQw4w9WgXcQ"}).json()
    assert repaired == {"project_id": project_id, "status": "queued"}
    assert submitted == [project_id, project_id]


def test_clips_are_scoped_playable_downloadable_and_do_not_expose_paths(api_client, monkeypatch):
    from backend.app.config import get_settings
    from backend.app.db import session_factory
    from backend.app.models import Clip, Moment, Project, Video

    with session_factory()() as session:
        project = Project(source_url="https://youtu.be/dQw4w9WgXcQ", status="READY")
        session.add(project)
        session.flush()
        root = get_settings().project_storage(project.id)
        source = root / "source" / "video.mp4"
        source.write_bytes(b"source-video")
        session.add(Video(project_id=project.id, source_path=str(source)))
        moment = Moment(project_id=project.id, title="Moment", description="Description", reason="Reason", start=0, end=2, score=8)
        session.add(moment)
        session.flush()
        output = root / "clips" / "clip.mp4"
        output.write_bytes(b"test-video-bytes")
        clip = Clip(project_id=project.id, moment_id=moment.id, start=0, end=2, status="READY", output_path=str(output))
        session.add(clip)
        session.commit()
        project_id, clip_id, moment_id = project.id, clip.id, moment.id

    base = f"/api/v1/projects/{project_id}"
    before = api_client.get(base).json()["updated_at"]
    response = api_client.get(base + "/clips").json()["clips"][0]
    assert "output_path" not in response
    assert response["status"] == "READY"
    assert response["media_url"].startswith("/api/v1/") and "http" not in response["media_url"]
    assert api_client.get(response["media_url"]).content == b"test-video-bytes"
    partial = api_client.get(response["media_url"], headers={"Range": "bytes=0-3"})
    assert partial.status_code == 206
    assert partial.content == b"test"
    unsatisfiable = api_client.get(response["media_url"], headers={"Range": "bytes=999-1000"})
    assert unsatisfiable.status_code == 416
    download = api_client.get(response["download_url"])
    assert download.status_code == 200
    assert download.headers["content-disposition"].startswith("attachment;")
    assert api_client.get(f"/api/v1/projects/another-project/clips/{clip_id}/media").status_code == 404
    assert api_client.get(base).json()["updated_at"] == before

    # Even a corrupted database path cannot expose a file outside this project.
    with session_factory()() as session:
        session.get(Clip, clip_id).output_path = str(source)
        session.commit()
    assert api_client.get(response["media_url"]).status_code == 404
    missing = api_client.get(base + "/clips").json()["clips"][0]
    assert missing["status"] == "FAILED"
    assert missing["failed_stage"] == "media"
    assert missing["error"]["message"] == "Internal server error while loading media."
    assert missing["media_url"] is None and missing["download_url"] is None

    from backend.app.main import app

    submitted = []
    futures = []
    def accept(wrapped, runner, args):
        submitted.append((runner, *args))
        future = Future()
        futures.append(future)
        return future

    monkeypatch.setattr(app.state.pipeline_executor, "submit", accept)
    request = {"moment_id": moment_id, "start": 0, "end": 2}
    queued = api_client.post(base + "/clips", json=request)
    assert queued.status_code == 202
    assert queued.json()["status"] == "QUEUED"
    assert api_client.post(base + "/clips", json=request).status_code == 409
    assert submitted == [(app.state.clip_runner, project_id, clip_id)]
    assert api_client.get(base + "/status").json()["status"] == "RENDERING"

    # A failed submission stays isolated and retryable, without exposing the
    # executor's internal exception or leaving the clip permanently queued.
    with session_factory()() as session:
        session.get(Project, project_id).status = "READY"
        session.get(Clip, clip_id).status = "FAILED"
        session.commit()
    futures[0].set_result(None)

    def unavailable(*args):
        raise RuntimeError("private executor diagnostic")

    monkeypatch.setattr(app.state.pipeline_executor, "submit", unavailable)
    response = api_client.post(base + "/clips", json=request)
    assert response.status_code == 503
    assert response.json()["failed_stage"] == "rendering"
    assert "private executor diagnostic" not in response.text
    assert api_client.get(base + "/status").json()["status"] == "READY"
    clip = api_client.get(base + "/clips").json()["clips"][0]
    assert clip["status"] == "FAILED"
    assert clip["error_message"] == "Internal server error while generating the clip."


def test_source_video_is_not_served(api_client):
    from backend.app.config import get_settings
    from backend.app.db import session_factory
    from backend.app.models import Project, Video

    with session_factory()() as session:
        project = Project(source_url="https://youtu.be/dQw4w9WgXcQ", status="READY")
        session.add(project)
        session.flush()
        source = get_settings().project_storage(project.id) / "source" / "video.mp4"
        source.write_bytes(b"source-video")
        session.add(Video(project_id=project.id, youtube_id="dQw4w9WgXcQ", source_path=str(source)))
        session.commit()
        project_id = project.id

    response = api_client.get(f"/api/v1/projects/{project_id}")
    assert response.status_code == 200
    assert response.json()["video"]["youtube_id"] == "dQw4w9WgXcQ"
    assert response.json()["video"]["media_url"] is None
    assert api_client.get(f"/api/v1/projects/{project_id}/media").status_code == 404


def test_project_list_is_bounded_and_paginates_stably(api_client):
    from datetime import datetime, timezone
    from backend.app.db import session_factory
    from backend.app.models import Project

    with session_factory()() as session:
        for index in range(31):
            session.add(Project(source_url=f"https://youtu.be/{index:011d}", created_at=datetime(2026, 1, 1, tzinfo=timezone.utc)))
        session.commit()
    first = api_client.get("/api/v1/projects").json()
    second = api_client.get("/api/v1/projects?limit=25&offset=25").json()
    assert len(first) == 25 and len(second) == 6
    ids = [row["id"] for row in first + second]
    assert len(set(ids)) == 31 and ids == sorted(ids, reverse=True)
    assert api_client.get("/api/v1/projects?limit=101").status_code == 422
    assert api_client.get("/api/v1/projects?offset=-1").status_code == 422


def test_segment_only_projection_does_not_load_words_or_mutate_saved_timing(api_client):
    from sqlalchemy import event
    from sqlalchemy.engine import Engine
    from backend.app.db import session_factory
    from backend.app.models import Project, TranscriptSegment

    words = [{"word": "Saved", "start": 0, "end": 1}]
    with session_factory()() as session:
        project = Project(source_url="https://youtu.be/dQw4w9WgXcQ")
        project.transcript_segments.append(TranscriptSegment(start=0, end=1, text="Saved", segment_index=0, words=words))
        session.add(project)
        session.commit()
        project_id = project.id
    statements = []

    def record(connection, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    event.listen(Engine, "before_cursor_execute", record)
    try:
        response = api_client.get(f"/api/v1/projects/{project_id}/transcript?include_words=false")
    finally:
        event.remove(Engine, "before_cursor_execute", record)
    assert response.status_code == 200
    segment = response.json()["segments"][0]
    assert segment["text"] == "Saved" and "words" not in segment
    selects = [statement for statement in statements if "FROM transcript_segments" in statement]
    assert selects and all("transcript_segments.words" not in statement for statement in selects)
    complete = api_client.get(f"/api/v1/projects/{project_id}/transcript?include_words=true").json()
    assert complete["segments"][0]["words"] == words
    with session_factory()() as session:
        assert session.get(Project, project_id).transcript_segments[0].words == words


def test_explicit_project_delete_quarantines_media_and_is_read_only_for_active_work(api_client):
    from backend.app.config import get_settings
    from backend.app.db import session_factory
    from backend.app.models import Project, Video

    with session_factory()() as session:
        project = Project(source_url="https://youtu.be/dQw4w9WgXcQ", status="READY")
        session.add(project)
        session.flush()
        root = get_settings().project_storage(project.id)
        source = root / "source" / "video.mp4"
        source.write_bytes(b"source")
        project.video = Video(source_path=str(source))
        session.commit()
        project_id = project.id
    response = api_client.delete(f"/api/v1/projects/{project_id}")
    assert response.status_code == 204 and not response.content
    assert not root.exists()
    assert api_client.get(f"/api/v1/projects/{project_id}").status_code == 404

    with session_factory()() as session:
        active = Project(source_url="https://youtu.be/abcdefghijk", status="ANALYZING")
        session.add(active)
        session.commit()
        active_id = active.id
    blocked = api_client.delete(f"/api/v1/projects/{active_id}")
    assert blocked.status_code == 409
    with session_factory()() as session:
        assert session.get(Project, active_id) is not None
