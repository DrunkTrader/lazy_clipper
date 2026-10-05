"""Exercise error boundaries with realistic upstream failures and legacy rows."""
import logging

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError

from backend.app.api.errors import install_error_handlers
from backend.app.config import Settings, reset_settings_cache
from backend.app.db import init_db, session_factory
from backend.app.errors import PUBLIC_MESSAGES
from backend.app.logging import configure_logging, log_failure
from backend.app.models import Clip, Moment, Project, Video
from backend.app.services.pipeline import Pipeline
from backend.tests.test_api import api_client as api_client
from backend.tests.test_pipeline import FakeAnalyzer, FakeMedia, FakeTranscriber, FakeYoutube


UPSTREAM = (
    "candidate detection request failed: 502 All routed attempts failed "
    "provider_error provider=example execution_id=exec-regression "
    "https://gateway.example/private api_key='test-private-key'"
)


@pytest.mark.parametrize("stage,service,method", [
    ("fetching", "youtube", "get_metadata"),
    ("ingestion", "youtube", "download_video"),
    ("ingestion", "media", "extract_audio"),
    ("transcription", "transcriber", "transcribe"),
    ("analysis", "analyzer", "analyze"),
])
def test_pipeline_persists_only_public_failure_and_logs_diagnostics(tmp_path, monkeypatch, caplog, stage, service, method):
    settings = Settings(database_url=f"sqlite:///{tmp_path / 'errors.db'}", storage_dir=tmp_path / "storage")
    init_db(settings.database_url)
    sessions = session_factory(settings.database_url)
    with sessions() as session:
        project = Project(source_url="https://youtu.be/dQw4w9WgXcQ")
        session.add(project)
        session.commit()
        project_id = project.id
    pipeline = Pipeline(settings, youtube=FakeYoutube(), media=FakeMedia(), transcriber=FakeTranscriber(),
                        analyzer=FakeAnalyzer(), make_session=sessions)

    def fail(*args):
        raise RuntimeError(UPSTREAM)

    monkeypatch.setattr(getattr(pipeline, service), method, fail)
    pipeline.run(project_id)
    with sessions() as session:
        project = session.get(Project, project_id)
        assert project.status == "FAILED"
        assert project.error_message == PUBLIC_MESSAGES[stage]
        assert project.status_message == PUBLIC_MESSAGES[stage]
    assert f"project={project_id}" in caplog.text
    assert f"stage={stage} status=failed type=RuntimeError" in caplog.text
    assert "Traceback" in caplog.text
    assert "provider_error provider=example execution_id=exec-regression" in caplog.text
    assert "test-private-key" not in caplog.text
    assert "gateway.example" not in caplog.text


def test_legacy_errors_are_sanitized_on_every_read_without_writing(api_client):
    with session_factory()() as session:
        project = Project(source_url="https://youtu.be/dQw4w9WgXcQ", status="FAILED",
                          error_message=UPSTREAM, status_message=UPSTREAM)
        session.add(project)
        session.flush()
        moment = Moment(project_id=project.id, title="Saved moment", description="", reason="", start=0, end=5, score=8)
        session.add(moment)
        session.flush()
        session.add(Clip(project_id=project.id, moment_id=moment.id, start=0, end=5,
                         status="FAILED", error_message=UPSTREAM))
        session.commit()
        project_id = project.id
    base = f"/api/v1/projects/{project_id}"
    for path in ["/api/v1/projects", base, base + "/status", base + "/clips"]:
        response = api_client.get(path)
        assert response.status_code == 200
        assert "provider_error" not in response.text
        assert "exec-regression" not in response.text
        assert "test-private-key" not in response.text
        data = response.json()
        item = data[0] if isinstance(data, list) else data["clips"][0] if "clips" in data else data
        stage = "rendering" if path.endswith("/clips") else "analysis"
        assert item["failed_stage"] == stage
        assert item["error"] == {"code": "PROCESSING_ERROR", "message": PUBLIC_MESSAGES[stage]}
    with session_factory()() as session:
        assert session.get(Project, project_id).error_message == UPSTREAM


@pytest.mark.parametrize("status", [500, 502, 503, 504])
@pytest.mark.parametrize("path,stage", [
    ("/api/v1/ingest", "ingestion"),
    ("/api/v1/projects/p/clips", "rendering"),
    ("/api/v1/projects/p/clips/c/media", "media"),
    ("/api/v1/projects", "database"),
    ("/unknown", "unknown"),
])
def test_http_failure_contract(status, path, stage, caplog):
    app = FastAPI()
    install_error_handlers(app)

    @app.post(path)
    def fail():
        raise HTTPException(status_code=status, detail=UPSTREAM)

    with TestClient(app) as client:
        response = client.post(path)
    assert response.status_code == status
    assert response.json() == {"status": "failed", "failed_stage": stage,
                               "error": {"code": "PROCESSING_ERROR", "message": PUBLIC_MESSAGES[stage]}}
    assert "execution_id=exec-regression" in caplog.text
    assert "test-private-key" not in caplog.text


def test_database_and_unhandled_errors_are_sanitized(caplog):
    app = FastAPI()
    install_error_handlers(app)

    @app.get("/database")
    def fail_database():
        raise OperationalError("SELECT private_sql", {"text": "private-row"}, RuntimeError("database unavailable"))

    @app.get("/unknown")
    def fail_unknown():
        raise RuntimeError(UPSTREAM)

    with TestClient(app, raise_server_exceptions=False) as client:
        for path, stage in [("/database", "database"), ("/unknown", "unknown")]:
            response = client.get(path)
            assert response.status_code == 500
            assert response.json()["error"]["message"] == PUBLIC_MESSAGES[stage]
    assert "private-row" not in caplog.text
    assert "private_sql" not in caplog.text
    assert "database unavailable" in caplog.text


def test_inline_database_driver_values_are_not_retained(caplog):
    configure_logging()
    app = FastAPI()
    install_error_handlers(app)

    @app.get("/database")
    def fail_database():
        raise OperationalError("SELECT inline-private-marker", {"value": "private-row"}, RuntimeError("inline-private-marker"))

    with TestClient(app, raise_server_exceptions=False) as client:
        assert client.get("/database").status_code == 500
    assert "inline-private-marker" not in caplog.text
    assert "private-row" not in caplog.text
    assert "database driver failure sqlstate=unknown" in caplog.text


@pytest.mark.parametrize("method,path,payload", [
    ("get", "/api/v1/projects/missing", None),
    ("post", "/api/v1/projects/missing/clips", {"moment_id": "m", "start": 0, "end": 1}),
    ("post", "/api/v1/ingest", {"url": "not-a-youtube-url"}),
])
def test_expected_client_errors_are_warning_without_traceback(api_client, caplog, method, path, payload):
    caplog.set_level(logging.DEBUG, logger="lazyclipper")
    caplog.clear()
    response = getattr(api_client, method)(path, json=payload) if payload is not None else getattr(api_client, method)(path)
    assert response.status_code in {404, 409, 422}
    records = [record for record in caplog.records if record.name == "lazyclipper"]
    assert records and records[-1].levelno == logging.WARNING
    assert "Traceback" not in caplog.text


def test_failed_database_commit_rolls_back_and_persists_safe_database_error(tmp_path, monkeypatch, caplog):
    settings = Settings(database_url=f"sqlite:///{tmp_path / 'commit-error.db'}", storage_dir=tmp_path / "storage")
    init_db(settings.database_url)
    sessions = session_factory(settings.database_url)
    with sessions() as session:
        project = Project(source_url="https://youtu.be/dQw4w9WgXcQ")
        session.add(project)
        session.commit()
        project_id = project.id
    worker_session = sessions()
    commit = worker_session.commit

    def fail_once():
        monkeypatch.setattr(worker_session, "commit", commit)
        try:
            raise RuntimeError("database unavailable\nDETAIL: private-driver-row")
        except RuntimeError as exc:
            raise OperationalError("INSERT private_sql", {"text": "private-row"}, exc) from exc

    monkeypatch.setattr(worker_session, "commit", fail_once)
    Pipeline(settings, make_session=lambda: worker_session).run(project_id)
    with sessions() as session:
        project = session.get(Project, project_id)
        assert project.status == "FAILED"
        assert project.error_message == PUBLIC_MESSAGES["database"]
    assert "stage=database status=failed type=OperationalError" in caplog.text
    assert "private-row" not in caplog.text
    assert "private-driver-row" not in caplog.text
    assert "private_sql" not in caplog.text


def test_validation_does_not_echo_inputs_and_success_shapes_are_unchanged(api_client, caplog):
    response = api_client.post("/api/v1/ingest", json={"url": {"private": "private-input"}})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "INVALID_URL"
    response = api_client.post("/api/v1/projects/p/clips", json={"moment_id": "m", "start": 5, "end": 4})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "INVALID_RANGE"
    assert "private-input" not in caplog.text
    created = api_client.post("/api/v1/ingest", json={"url": "https://youtu.be/dQw4w9WgXcQ"}).json()
    assert set(created) == {"project_id", "status"}
    base = f"/api/v1/projects/{created['project_id']}"
    data = api_client.get(base + "/status").json()
    assert set(data) == {"project_id", "status", "message", "error"}
    assert data["error"] is None
    data = api_client.get(base).json()
    assert "failed_stage" not in data and "error" not in data
    assert "status=created" in caplog.text


def test_submission_failure_persists_safe_ingestion_error(api_client, monkeypatch, caplog):
    from backend.app.main import app

    def fail(*args):
        raise RuntimeError(UPSTREAM)

    monkeypatch.setattr(app.state.pipeline_executor, "submit", fail)
    response = api_client.post("/api/v1/ingest", json={"url": "https://youtu.be/dQw4w9WgXcQ"})
    assert response.status_code == 503
    assert response.json()["error"]["message"] == PUBLIC_MESSAGES["ingestion"]
    with session_factory()() as session:
        project = session.query(Project).one()
        assert project.status == "FAILED"
        assert project.error_message == PUBLIC_MESSAGES["ingestion"]
        assert f"project={project.id}" in caplog.text
    assert "execution_id=exec-regression" in caplog.text


def test_single_clip_failure_is_public_and_project_remains_usable(tmp_path, monkeypatch, caplog):
    settings = Settings(database_url=f"sqlite:///{tmp_path / 'clip-error.db'}", storage_dir=tmp_path / "storage")
    init_db(settings.database_url)
    sessions = session_factory(settings.database_url)
    with sessions() as session:
        project = Project(source_url="https://youtu.be/dQw4w9WgXcQ", status="READY")
        session.add(project)
        session.flush()
        source = settings.project_storage(project.id) / "source" / "video.mp4"
        source.write_bytes(b"source")
        project.video = Video(source_path=str(source), duration=30)
        moment = Moment(project_id=project.id, title="Moment", description="", reason="", start=0, end=5, score=8)
        session.add(moment)
        session.flush()
        clip = Clip(project_id=project.id, moment_id=moment.id, start=0, end=5)
        session.add(clip)
        session.commit()
        project_id, clip_id = project.id, clip.id
    pipeline = Pipeline(settings, make_session=sessions)

    def fail(*args):
        raise RuntimeError(UPSTREAM)

    monkeypatch.setattr(pipeline, "_render_clip_media", fail)
    pipeline.render_clip(project_id, clip_id)
    with sessions() as session:
        assert session.get(Project, project_id).status == "READY"
        clip = session.get(Clip, clip_id)
        assert clip.status == "FAILED"
        assert clip.error_message == PUBLIC_MESSAGES["rendering"]
    assert f"clip={clip_id}" in caplog.text
    assert "execution_id=exec-regression" in caplog.text


def test_logs_redact_credentials_nested_multiline_payloads_and_validation_chains(monkeypatch, caplog):
    from pydantic import BaseModel

    configure_logging()
    monkeypatch.setenv("LLM_API_KEY", "configured-key-secret")
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:db-password-secret@localhost/database")
    reset_settings_cache()
    try:
        try:
            raise RuntimeError(
                UPSTREAM + "\nconfigured-key-secret db-password-secret\n"
                "Authorization: Bearer auth-secret\nCookie: SID=cookie-secret; other=other-secret\n"
                "prompt='private first\nprivate second'\nbody={\"nested\": [\"private-body\"]}\n"
                "execution_id=exec-after-body"
            )
        except RuntimeError as exc:
            log_failure(exc, project_id="p", stage="analysis")

        class Payload(BaseModel):
            count: int

        try:
            try:
                Payload(count="private-validation")
            except ValueError as exc:
                raise RuntimeError("schema failed") from exc
        except RuntimeError as exc:
            # Uvicorn's duplicate diagnostic must apply the same chain redaction.
            logging.getLogger("uvicorn.error").exception("Unhandled failure", exc_info=exc)
        for secret in ["configured-key-secret", "db-password-secret", "test-private-key", "auth-secret",
                       "cookie-secret", "other-secret", "private first", "private second", "private-body", "private-validation"]:
            assert secret not in caplog.text
        assert "execution_id=exec-after-body" in caplog.text
        assert "validation types=['int_parsing']" in caplog.text
    finally:
        reset_settings_cache()
