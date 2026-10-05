"""Explicit, transactional upgrades of a pre-migration database."""
import pytest
from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.exc import IntegrityError

from backend.tests.test_api import api_client  # noqa: F401


@pytest.fixture
def legacy(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'legacy.db'}")
    with engine.begin() as connection:
        for ddl in (
            "CREATE TABLE projects (id TEXT PRIMARY KEY, source_url TEXT NOT NULL, title TEXT, status TEXT NOT NULL, status_message TEXT, error_message TEXT, created_at DATETIME, updated_at DATETIME)",
            "CREATE TABLE videos (id TEXT PRIMARY KEY, project_id TEXT UNIQUE REFERENCES projects(id), youtube_id TEXT, title TEXT, duration FLOAT, thumbnail_url TEXT, source_path TEXT, audio_path TEXT)",
            "CREATE TABLE transcript_segments (id TEXT PRIMARY KEY, project_id TEXT REFERENCES projects(id), start FLOAT, end FLOAT, text TEXT, speaker TEXT, segment_index INTEGER, words JSON)",
            "CREATE TABLE moments (id TEXT PRIMARY KEY, project_id TEXT REFERENCES projects(id), title TEXT, description TEXT, start FLOAT, end FLOAT, score FLOAT, reason TEXT, rank INTEGER, source_segments JSON, dimensions JSON)",
            "CREATE TABLE clips (id TEXT PRIMARY KEY, project_id TEXT REFERENCES projects(id), moment_id TEXT UNIQUE REFERENCES moments(id), start FLOAT, end FLOAT, status TEXT, output_path TEXT, error_message TEXT)",
        ):
            connection.execute(text(ddl))
        connection.execute(text("INSERT INTO projects (id, source_url, status) VALUES ('p', 'https://youtu.be/dQw4w9WgXcQ?t=5', 'READY')"))
        connection.execute(text("INSERT INTO videos (id, project_id, youtube_id, source_path) VALUES ('v', 'p', 'dQw4w9WgXcQ', '/saved/source.mp4')"))
        connection.execute(text("INSERT INTO transcript_segments (id, project_id, text, words) VALUES ('t', 'p', 'Saved speech', :words)"),
                           {"words": '[{"word":"Saved","start":0,"end":1}]'})
        connection.execute(text("INSERT INTO moments (id, project_id, title) VALUES ('m', 'p', 'Saved moment')"))
        connection.execute(text("INSERT INTO clips (id, project_id, moment_id, status, output_path) VALUES ('c', 'p', 'm', 'READY', '/saved/clip.mp4')"))
    yield engine
    engine.dispose()


def test_legacy_startup_requires_explicit_upgrade_without_mutation(legacy):
    from backend.app.migrations import SchemaUpgradeRequired, ensure_schema

    before = set(inspect(legacy).get_table_names())
    with pytest.raises(SchemaUpgradeRequired, match="upgrade"):
        ensure_schema(legacy)
    assert set(inspect(legacy).get_table_names()) == before
    assert "analysis_completed" not in {item["name"] for item in inspect(legacy).get_columns("projects")}


def test_upgrade_preserves_data_canonicalizes_identity_and_is_idempotent(legacy):
    from backend.app.migrations import ensure_schema, upgrade_schema

    with legacy.begin() as connection:
        connection.execute(text("INSERT INTO projects (id, source_url, status) VALUES ('empty', 'https://youtu.be/abcdefghijk', 'READY'), ('failed', 'https://youtu.be/12345678901', 'FAILED')"))
        saved = {name: connection.execute(text(f"SELECT * FROM {name}")).all()
                 for name in ("videos", "transcript_segments", "moments", "clips")}
    upgrade_schema(legacy)
    ensure_schema(legacy)
    upgrade_schema(legacy)
    with legacy.connect() as connection:
        projects = connection.execute(text("SELECT id, source_url, analysis_completed FROM projects ORDER BY id")).all()
        assert [(row[0], bool(row[2])) for row in projects] == [("empty", True), ("failed", False), ("p", True)]
        assert all(row[1].startswith("https://www.youtube.com/watch?v=") for row in projects)
        for name, before in saved.items():
            assert connection.execute(text(f"SELECT * FROM {name}")).all() == before
        assert connection.scalar(text("SELECT count(*) FROM schema_migrations")) == 1
    with pytest.raises(IntegrityError):
        with legacy.begin() as connection:
            connection.execute(text("INSERT INTO projects (id, source_url, status) VALUES ('duplicate', 'https://www.youtube.com/watch?v=dQw4w9WgXcQ', 'QUEUED')"))
    assert any(item["name"] == "ix_videos_youtube_id" for item in inspect(legacy).get_indexes("videos"))


@pytest.mark.parametrize("url", ["https://www.youtube.com/watch?v=dQw4w9WgXcQ", "invalid-source"])
def test_ambiguous_or_invalid_legacy_identity_aborts_before_schema_change(legacy, url):
    from backend.app.migrations import SchemaUpgradeRequired, upgrade_schema

    with legacy.begin() as connection:
        connection.execute(text("INSERT INTO projects (id, source_url, status) VALUES ('conflict', :url, 'FAILED')"), {"url": url})
    with pytest.raises(SchemaUpgradeRequired):
        upgrade_schema(legacy)
    assert "analysis_completed" not in {item["name"] for item in inspect(legacy).get_columns("projects")}
    with legacy.connect() as connection:
        assert connection.scalar(text("SELECT source_url FROM projects WHERE id='p'")) == "https://youtu.be/dQw4w9WgXcQ?t=5"


def test_upgrade_rolls_back_ddl_and_data_together(legacy):
    from backend.app.migrations import upgrade_schema

    def fail(connection, cursor, statement, parameters, context, executemany):
        if statement.startswith("CREATE UNIQUE INDEX"):
            raise RuntimeError("synthetic index creation failure")

    event.listen(legacy, "before_cursor_execute", fail)
    try:
        with pytest.raises(RuntimeError, match="synthetic"):
            upgrade_schema(legacy)
    finally:
        event.remove(legacy, "before_cursor_execute", fail)
    assert "analysis_completed" not in {item["name"] for item in inspect(legacy).get_columns("projects")}
    with legacy.connect() as connection:
        assert connection.scalar(text("SELECT source_url FROM projects WHERE id='p'")) == "https://youtu.be/dQw4w9WgXcQ?t=5"
    assert "schema_migrations" not in inspect(legacy).get_table_names()


def test_fresh_schema_enforces_canonical_identity_for_orm_writers(api_client):  # noqa: F811
    from backend.app.db import session_factory
    from backend.app.models import Project

    with session_factory()() as session:
        first = Project(source_url="https://youtu.be/dQw4w9WgXcQ?t=10")
        session.add(first)
        session.commit()
        assert first.source_url == "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
    with pytest.raises(IntegrityError):
        with session_factory()() as session:
            session.add(Project(source_url="https://www.youtube.com/watch?v=dQw4w9WgXcQ&feature=shared"))
            session.commit()


def test_insert_race_returns_winner_without_dispatch_or_capacity_leak(api_client, monkeypatch):  # noqa: F811
    from sqlalchemy.orm import Session
    from backend.app.db import session_factory
    from backend.app.main import app
    from backend.app.models import Project

    winner = {}

    def competing_insert(session):
        if winner or not any(isinstance(row, Project) for row in session.new):
            return
        winner["started"] = True
        with session_factory()() as other:
            project = Project(source_url="https://youtu.be/dQw4w9WgXcQ")
            other.add(project)
            other.commit()
            winner["id"] = project.id

    monkeypatch.setattr(app.state.pipeline_executor, "submit", lambda *args: pytest.fail("Duplicate identity must not be dispatched"))
    event.listen(Session, "before_commit", competing_insert)
    try:
        response = api_client.post("/api/v1/ingest", json={"url": "https://youtu.be/dQw4w9WgXcQ"})
    finally:
        event.remove(Session, "before_commit", competing_insert)
    assert response.status_code == 202
    assert response.json() == {"project_id": winner["id"], "status": "queued"}
    assert app.state.admission.snapshot()["available"] == 6
    with session_factory()() as session:
        assert session.query(Project).count() == 1
