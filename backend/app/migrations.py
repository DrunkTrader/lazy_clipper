"""Explicit versioned schema upgrades; startup never alters existing tables.

Run with the API stopped, a verified metadata/media backup, and the table owner's
database credentials: python -m backend.app.migrations upgrade
"""
import argparse

from sqlalchemy import Column, Integer, MetaData, Table, inspect, select, text
from sqlalchemy.engine import Connection, Engine


CURRENT_REVISION = 1
APPLICATION_TABLES = {"projects", "videos", "transcript_segments", "moments", "clips"}
_versions = Table("schema_migrations", MetaData(), Column("revision", Integer, primary_key=True))


class SchemaUpgradeRequired(RuntimeError):
    pass


def _revision(connection: Connection) -> int:
    if not inspect(connection).has_table(_versions.name):
        return 0
    revisions = list(connection.scalars(select(_versions.c.revision).order_by(_versions.c.revision)))
    if not revisions or revisions != list(range(1, revisions[-1] + 1)):
        raise SchemaUpgradeRequired("Schema revision history is incomplete; restore or repair before upgrade")
    return revisions[-1]


def _create_latest(connection: Connection) -> None:
    from . import models  # noqa: F401
    from .db import Base

    Base.metadata.create_all(connection)
    if not inspect(connection).has_table(_versions.name):
        _versions.create(connection)
        connection.execute(_versions.insert().values(revision=CURRENT_REVISION))


def _check_current(connection: Connection) -> None:
    columns = {item["name"] for item in inspect(connection).get_columns("projects")}
    indexes = inspect(connection).get_indexes("projects")
    if "analysis_completed" not in columns or not any(
        item["name"] == "uq_projects_source_url" and item["unique"] for item in indexes
    ):
        raise SchemaUpgradeRequired("Schema does not match its revision; restore or repair before upgrade")


def ensure_schema(engine: Engine) -> None:
    """Create a fresh schema, or verify an existing one without migrating it."""
    with engine.begin() as connection:
        tables = set(inspect(connection).get_table_names())
        revision = _revision(connection)
        if revision not in {0, CURRENT_REVISION}:
            raise SchemaUpgradeRequired("Database schema is from a different application version")
        if not tables.intersection(APPLICATION_TABLES):
            _create_latest(connection)
        elif not APPLICATION_TABLES.issubset(tables) or revision != CURRENT_REVISION:
            raise SchemaUpgradeRequired("Existing database requires an explicit backed-up schema upgrade; run python -m backend.app.migrations upgrade with the API stopped")
        _check_current(connection)


def _upgrade_legacy(connection: Connection) -> None:
    from .services.youtube import InvalidYouTubeURL, canonical_youtube_url, validate_youtube_url

    rows = connection.execute(text(
        "SELECT p.id, p.source_url, p.status, v.youtube_id, "
        "EXISTS(SELECT 1 FROM moments m WHERE m.project_id = p.id) AS has_moments "
        "FROM projects p LEFT JOIN videos v ON v.project_id = p.id"
    )).mappings().all()
    updates, identities = [], set()
    for row in rows:
        try:
            canonical = canonical_youtube_url(row["source_url"])
            identity = validate_youtube_url(row["source_url"])
        except InvalidYouTubeURL as exc:
            raise SchemaUpgradeRequired(f"Project {row['id']} has an invalid source identity; resolve explicitly before upgrade") from exc
        if row["youtube_id"] and row["youtube_id"] != identity:
            raise SchemaUpgradeRequired(f"Project {row['id']} has conflicting source/metadata identities; resolve explicitly before upgrade")
        if identity in identities:
            raise SchemaUpgradeRequired("Duplicate canonical source identities exist; explicitly reconcile projects before upgrade")
        identities.add(identity)
        updates.append({
            "id": row["id"], "url": canonical,
            "completed": row["status"] in {"READY", "RENDERING"} or bool(row["has_moments"]),
        })
    # All ambiguity checks precede DDL. Existing stages, child rows, IDs, paths,
    # and activity timestamps are retained; no automatic merging or deletion.
    connection.execute(text("ALTER TABLE projects ADD COLUMN analysis_completed BOOLEAN NOT NULL DEFAULT FALSE"))
    if updates:
        connection.execute(text("UPDATE projects SET source_url=:url, analysis_completed=:completed WHERE id=:id"), updates)
    connection.execute(text("CREATE UNIQUE INDEX uq_projects_source_url ON projects (source_url)"))
    connection.execute(text("CREATE INDEX ix_videos_youtube_id ON videos (youtube_id)"))
    _versions.create(connection)
    connection.execute(_versions.insert().values(revision=1))


def upgrade_schema(engine: Engine) -> None:
    """Upgrade in one transaction; refuse ambiguous identities or unknown schema."""
    with engine.begin() as connection:
        if connection.dialect.name == "sqlite":
            # sqlite3 legacy transaction mode otherwise autocommits DDL.
            connection.exec_driver_sql("BEGIN IMMEDIATE")
        elif connection.dialect.name != "postgresql":
            raise SchemaUpgradeRequired("Only PostgreSQL and SQLite upgrades are supported")
        tables = set(inspect(connection).get_table_names())
        if tables.intersection(APPLICATION_TABLES) and not APPLICATION_TABLES.issubset(tables):
            raise SchemaUpgradeRequired("Partial application schema; restore or repair before upgrade")
        if connection.dialect.name == "postgresql" and APPLICATION_TABLES.issubset(tables):
            connection.execute(text("LOCK TABLE projects, videos, transcript_segments, moments, clips IN ACCESS EXCLUSIVE MODE"))
        revision = _revision(connection)
        if revision > CURRENT_REVISION:
            raise SchemaUpgradeRequired("Database is newer than this application; refusing downgrade")
        if not tables.intersection(APPLICATION_TABLES):
            _create_latest(connection)
        elif revision == 0:
            _upgrade_legacy(connection)
        _check_current(connection)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=["status", "upgrade"])
    args = parser.parse_args()
    from .db import get_engine
    from .logging import configure_logging, log_failure

    configure_logging()
    try:
        engine = get_engine()
        if args.operation == "upgrade":
            upgrade_schema(engine)
        with engine.connect() as connection:
            revision = _revision(connection)
        print(f"Schema revision: {revision}; application revision: {CURRENT_REVISION}")
        return 0
    except Exception as exc:
        log_failure(exc, project_id=None, stage="database", context="MIGRATION")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
