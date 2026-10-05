"""Opt-in PostgreSQL transaction/schema checks used by CI and deployment tests."""
import os
from uuid import uuid4

import pytest
from sqlalchemy import text


DATABASE_URL = os.getenv("POSTGRES_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DATABASE_URL, reason="POSTGRES_TEST_DATABASE_URL not configured")


def test_postgresql_fresh_schema_marker_uniqueness_and_transaction_settings():
    from backend.app.db import get_engine, init_db, session_factory
    from backend.app.models import Project

    init_db(DATABASE_URL)
    engine = get_engine(DATABASE_URL)
    with engine.connect() as connection:
        assert connection.scalar(text("SHOW statement_timeout")) == "10s"
        assert connection.scalar(text("SHOW lock_timeout")) == "3s"
        assert connection.scalar(text("SELECT revision FROM schema_migrations")) == 1
    project_id = str(uuid4())
    url = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
    with session_factory(DATABASE_URL)() as session:
        session.add(Project(id=project_id, source_url=url, status="READY", analysis_completed=True))
        session.commit()
    with session_factory(DATABASE_URL)() as session:
        project = session.get(Project, project_id)
        assert project.analysis_completed is True
        session.delete(project)
        session.commit()
    engine.dispose()
