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
    monkeypatch.setattr(main, "run_pipeline", lambda project_id: None)
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
    monkeypatch.setattr(app.state.pipeline_executor, "submit", lambda runner, project_id: submitted.append(project_id))
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
        project_id, clip_id = project.id, clip.id

    base = f"/api/v1/projects/{project_id}"
    before = api_client.get(base).json()["updated_at"]
    response = api_client.get(base + "/clips").json()["clips"][0]
    assert "output_path" not in response
    assert response["status"] == "READY"
    assert api_client.get(response["media_url"]).content == b"test-video-bytes"
    partial = api_client.get(response["media_url"], headers={"Range": "bytes=0-3"})
    assert partial.status_code == 206
    assert partial.content == b"test"
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
    assert api_client.get(base + "/clips").json()["clips"][0]["status"] == "FAILED"

    from backend.app.main import app

    submitted = []
    monkeypatch.setattr(app.state.pipeline_executor, "submit", lambda runner, project_id: submitted.append((runner, project_id)))
    assert api_client.post(base + "/clips").status_code == 202
    assert api_client.post(base + "/clips").status_code == 409
    assert submitted == [(app.state.clip_runner, project_id)]
    assert api_client.get(base + "/status").json()["status"] == "RENDERING"
