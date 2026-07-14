"""ORM models (SQLAlchemy 2.0), aligned with the data model in PRIMOCUT.md.

Audio is ephemeral (written to a temp dir, discarded when the job ends);
transcript/topics/chat_signal are permanent (rows here).
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (
    JSON,
    Boolean,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class VideoRow(Base):
    __tablename__ = "videos"

    id: Mapped[str] = mapped_column(String, primary_key=True)  # YouTube video id
    url: Mapped[str] = mapped_column(String)
    title: Mapped[str] = mapped_column(String, default="")
    channel: Mapped[str] = mapped_column(String, default="")
    duration: Mapped[float] = mapped_column(Float, default=0.0)
    status: Mapped[str] = mapped_column(String, default="queued")
    created_at: Mapped[datetime] = mapped_column(default=_now)
    updated_at: Mapped[datetime] = mapped_column(default=_now, onupdate=_now)


class JobRow(Base):
    __tablename__ = "jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    video_id: Mapped[str] = mapped_column(ForeignKey("videos.id", ondelete="CASCADE"))
    rq_job_id: Mapped[str | None] = mapped_column(String, default=None)
    stage: Mapped[str] = mapped_column(String, default="")
    progress: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String, default="queued")
    error: Mapped[str | None] = mapped_column(Text, default=None)
    warnings: Mapped[list] = mapped_column(JSON, default=list)
    # Ordered transitions [{"stage": <label>, "at": <iso ts>}], one per stage
    # change; per-step durations are derived from consecutive `at` timestamps.
    stage_history: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(default=_now)
    updated_at: Mapped[datetime] = mapped_column(default=_now, onupdate=_now)


class TranscriptRow(Base):
    __tablename__ = "transcripts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    video_id: Mapped[str] = mapped_column(
        ForeignKey("videos.id", ondelete="CASCADE"), unique=True
    )
    language: Mapped[str] = mapped_column(String, default="pt")
    model: Mapped[str] = mapped_column(String, default="")
    segments: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(default=_now)


class ChatSignalRow(Base):
    __tablename__ = "chat_signals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    video_id: Mapped[str] = mapped_column(
        ForeignKey("videos.id", ondelete="CASCADE"), unique=True
    )
    per_minute: Mapped[list] = mapped_column(JSON, default=list)
    peaks: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(default=_now)


class TopicRow(Base):
    __tablename__ = "topics"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    video_id: Mapped[str] = mapped_column(ForeignKey("videos.id", ondelete="CASCADE"))
    ordinal: Mapped[int] = mapped_column(Integer, default=0)
    title: Mapped[str] = mapped_column(String)
    summary: Mapped[str] = mapped_column(Text, default="")
    start: Mapped[float] = mapped_column(Float)
    end: Mapped[float] = mapped_column(Float)
    score: Mapped[float] = mapped_column(Float, default=0.0)


class SchemaMigration(Base):
    __tablename__ = "schema_migrations"

    version: Mapped[int] = mapped_column(Integer, primary_key=True)
    applied_at: Mapped[datetime] = mapped_column(default=_now)


# ------------------------- Phase 2: competitor modeling -------------------------
#
# New tables are created by `Base.metadata.create_all()` (same mechanism that
# created the base v1 schema) — both on a fresh database and on an existing one,
# because create_all issues CREATE TABLE only for tables that do not yet exist.
# There is no `NNN_*.sql` migration for them: those deltas are reserved for
# ALTER/DROP on tables that already exist, which create_all cannot perform.


class ProjectRow(Base):
    """A project groups the competitor channels of one niche/clip channel."""

    __tablename__ = "projects"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String, unique=True)
    description: Mapped[str] = mapped_column(Text, default="")
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(default=_now)
    updated_at: Mapped[datetime] = mapped_column(default=_now, onupdate=_now)


class CompetitorChannelRow(Base):
    """A competitor channel is GLOBAL: collected once, deduplicated by channel_id
    and shared across projects via `project_channels` (M:N). A channel orphaned
    from every project is deactivated (`active=False`) but never deleted."""

    __tablename__ = "competitor_channels"

    channel_id: Mapped[str] = mapped_column(String, primary_key=True)  # "UC..."
    handle: Mapped[str] = mapped_column(String, default="")  # "@name"
    title: Mapped[str] = mapped_column(String, default="")
    uploads_playlist_id: Mapped[str] = mapped_column(String, default="")
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    # Backfill progress (polled like the video pipeline). Lives on the channel
    # because backfill runs once per channel, globally (not per project link).
    backfill_state: Mapped[str] = mapped_column(String, default="pending")
    backfill_done: Mapped[int] = mapped_column(Integer, default=0)
    backfill_total: Mapped[int] = mapped_column(Integer, default=0)
    backfill_error: Mapped[str | None] = mapped_column(Text, default=None)
    backfill_rq_job_id: Mapped[str | None] = mapped_column(String, default=None)
    created_at: Mapped[datetime] = mapped_column(default=_now)
    updated_at: Mapped[datetime] = mapped_column(default=_now, onupdate=_now)


class ProjectChannelRow(Base):
    """M:N link between projects and global competitor channels.

    The ondelete=CASCADE lives ONLY here: deleting a project removes its link
    rows (detaches its channels) and NEVER touches channel/video data.
    """

    __tablename__ = "project_channels"

    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), primary_key=True
    )
    channel_id: Mapped[str] = mapped_column(
        ForeignKey("competitor_channels.channel_id", ondelete="CASCADE"),
        primary_key=True,
    )
    created_at: Mapped[datetime] = mapped_column(default=_now)


class CompetitorVideoRow(Base):
    """A competitor video, deduplicated globally by video_id (a video shared by
    channels in several projects is stored once)."""

    __tablename__ = "competitor_videos"

    video_id: Mapped[str] = mapped_column(String, primary_key=True)
    channel_id: Mapped[str] = mapped_column(
        ForeignKey("competitor_channels.channel_id", ondelete="CASCADE"), index=True
    )
    title: Mapped[str] = mapped_column(String, default="")
    published_at: Mapped[datetime | None] = mapped_column(default=None)
    duration_s: Mapped[int] = mapped_column(Integer, default=0)
    is_short: Mapped[bool] = mapped_column(Boolean, default=False)
    thumbnail_url: Mapped[str] = mapped_column(String, default="")
    thumbnail_hash: Mapped[str] = mapped_column(String, default="")
    created_at: Mapped[datetime] = mapped_column(default=_now)
