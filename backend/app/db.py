"""Synchronous SQLAlchemy setup.

The engine is created lazily so importing the API does not require a database
server (or a PostgreSQL driver) to be available.
"""
from collections.abc import Generator
from functools import lru_cache

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from .config import get_settings


class Base(DeclarativeBase):
    pass


@lru_cache
def get_engine(database_url: str | None = None) -> Engine:
    settings = get_settings()
    url = database_url or settings.database_url
    kwargs = {"pool_pre_ping": True, "hide_parameters": True}
    if url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False, "timeout": settings.database_lock_timeout_seconds}
    else:
        kwargs["pool_timeout"] = settings.database_connect_timeout_seconds
        kwargs["connect_args"] = {
            "connect_timeout": settings.database_connect_timeout_seconds,
            "options": (
                f"-c statement_timeout={settings.database_statement_timeout_seconds * 1000} "
                f"-c lock_timeout={settings.database_lock_timeout_seconds * 1000}"
            ),
            # Bound a dead peer as well as server-side query/lock execution.
            "keepalives": 1, "keepalives_idle": 5, "keepalives_interval": 2, "keepalives_count": 3,
            "tcp_user_timeout": 10000,
        }
    return create_engine(url, **kwargs)


def session_factory(database_url: str | None = None) -> sessionmaker[Session]:
    return sessionmaker(bind=get_engine(database_url), autoflush=False, expire_on_commit=False)


def get_db() -> Generator[Session, None, None]:
    session = session_factory()()
    try:
        yield session
    finally:
        session.close()


def init_db(database_url: str | None = None) -> None:
    from .migrations import ensure_schema

    ensure_schema(get_engine(database_url))
