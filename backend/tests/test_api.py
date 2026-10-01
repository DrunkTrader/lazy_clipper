import pytest


@pytest.fixture
def api_client(tmp_path, monkeypatch):
    # Import the application only after selecting an isolated test database.
    database = tmp_path / "lazyclipper.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{database}")
    monkeypatch.setenv("STORAGE_DIR", str(tmp_path / "storage"))

    from backend.app import main
    from backend.app.config import reset_settings_cache

    reset_settings_cache()
    monkeypatch.setattr(main, "run_pipeline", lambda project_id: None)
    from fastapi.testclient import TestClient

    with TestClient(main.app) as client:
        yield client


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
