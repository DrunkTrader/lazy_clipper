"""Persistent application entities."""
from datetime import datetime, timezone
import uuid

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Index, Integer, JSON, String, Text, UniqueConstraint, false
from sqlalchemy.orm import Mapped, mapped_column, relationship, validates

from .db import Base


def new_id() -> str:
    return str(uuid.uuid4())


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Project(Base):
    __tablename__ = "projects"
    __table_args__ = (Index("uq_projects_source_url", "source_url", unique=True),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    source_url: Mapped[str] = mapped_column(Text, nullable=False)
    analysis_completed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=false())
    title: Mapped[str | None] = mapped_column(String(500), nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="QUEUED", index=True)
    status_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    # New failures store only a public stage message; diagnostics belong in logs.
    # API serialization also sanitizes legacy rows containing raw exceptions.
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)

    video: Mapped["Video | None"] = relationship(back_populates="project", uselist=False, cascade="all, delete-orphan")
    transcript_segments: Mapped[list["TranscriptSegment"]] = relationship(
        back_populates="project", cascade="all, delete-orphan", order_by="TranscriptSegment.segment_index"
    )
    moments: Mapped[list["Moment"]] = relationship(
        back_populates="project", cascade="all, delete-orphan", order_by="Moment.rank"
    )
    clips: Mapped[list["Clip"]] = relationship(back_populates="project", cascade="all, delete-orphan")

    @validates("source_url")
    def canonical_source_url(self, key: str, value: str) -> str:
        from .services.youtube import canonical_youtube_url

        return canonical_youtube_url(value)


class Video(Base):
    __tablename__ = "videos"
    __table_args__ = (UniqueConstraint("project_id"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)
    youtube_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    title: Mapped[str | None] = mapped_column(String(500), nullable=True)
    duration: Mapped[float | None] = mapped_column(Float, nullable=True)
    thumbnail_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    audio_path: Mapped[str | None] = mapped_column(Text, nullable=True)

    project: Mapped[Project] = relationship(back_populates="video")


class TranscriptSegment(Base):
    __tablename__ = "transcript_segments"
    __table_args__ = (UniqueConstraint("project_id", "segment_index"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)
    start: Mapped[float] = mapped_column(Float, nullable=False)
    end: Mapped[float] = mapped_column(Float, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    speaker: Mapped[str | None] = mapped_column(String(255), nullable=True)
    segment_index: Mapped[int] = mapped_column(Integer, nullable=False)
    words: Mapped[list | None] = mapped_column(JSON, nullable=True)

    project: Mapped[Project] = relationship(back_populates="transcript_segments")


class Moment(Base):
    __tablename__ = "moments"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    start: Mapped[float] = mapped_column(Float, nullable=False)
    end: Mapped[float] = mapped_column(Float, nullable=False)
    score: Mapped[float] = mapped_column(Float, nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    rank: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    source_segments: Mapped[list | None] = mapped_column(JSON, nullable=True)
    dimensions: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    project: Mapped[Project] = relationship(back_populates="moments")


class Clip(Base):
    __tablename__ = "clips"
    __table_args__ = (UniqueConstraint("moment_id"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)
    moment_id: Mapped[str] = mapped_column(ForeignKey("moments.id", ondelete="CASCADE"), nullable=False)
    start: Mapped[float] = mapped_column(Float, nullable=False)
    end: Mapped[float] = mapped_column(Float, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="QUEUED")
    output_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Public-only message, using the same contract as project failures.
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    project: Mapped[Project] = relationship(back_populates="clips")
