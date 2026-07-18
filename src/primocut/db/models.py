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
    Index,
    Integer,
    String,
    Text,
    desc,
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
    # Top-comment collection state. Automatic and deterministic: a snapshot cycle
    # collects a video's comments once it is a definitive outlier. "" means never
    # collected; "done"/"disabled" are TERMINAL (what stops the cycle re-asking
    # every day); "error" is retried on the next cycle.
    comments_state: Mapped[str] = mapped_column(String, default="")
    comments_error: Mapped[str] = mapped_column(Text, default="")
    comments_fetched_at: Mapped[datetime | None] = mapped_column(default=None)
    # Highest outlier band ever EMITTED as an `outlier_crossed` event, for the
    # anti-flapping guard (see `worker/event_emission.py`). "" means none emitted
    # yet (below every band); an event fires only on a transition to a strictly
    # higher band, so a video oscillating around the p90 cut cannot re-emit.
    last_band_emitted: Mapped[str] = mapped_column(String, default="")
    created_at: Mapped[datetime] = mapped_column(default=_now)


# ------------------------- Phase 2: continuous collection (snapshots) -------------------------
#
# Deterministic, quota-accounted time series collected on a schedule. Outlier
# scores and views-per-hour are computed on read (over these snapshots), never
# stored. Same create_all mechanism as the tables above (no migration file).


class ChannelSnapshotRow(Base):
    """Point-in-time channel stats captured by a collection cycle."""

    __tablename__ = "channel_snapshots"
    __table_args__ = (Index("ix_channel_snapshots_channel_captured", "channel_id", "captured_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    channel_id: Mapped[str] = mapped_column(
        ForeignKey("competitor_channels.channel_id", ondelete="CASCADE")
    )
    captured_at: Mapped[datetime] = mapped_column(default=_now)
    subscribers: Mapped[int] = mapped_column(Integer, default=0)
    total_views: Mapped[int] = mapped_column(Integer, default=0)
    video_count: Mapped[int] = mapped_column(Integer, default=0)


class VideoSnapshotRow(Base):
    """Point-in-time video stats. The (video_id, captured_at) index powers the
    per-video time-series reads (VPH, outlier baselines) added in a later step."""

    __tablename__ = "video_snapshots"
    __table_args__ = (Index("ix_video_snapshots_video_captured", "video_id", "captured_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    video_id: Mapped[str] = mapped_column(
        ForeignKey("competitor_videos.video_id", ondelete="CASCADE")
    )
    captured_at: Mapped[datetime] = mapped_column(default=_now)
    views: Mapped[int] = mapped_column(Integer, default=0)
    likes: Mapped[int] = mapped_column(Integer, default=0)
    comments: Mapped[int] = mapped_column(Integer, default=0)


class PackagingChangeRow(Base):
    """A detected title/thumbnail change (packaging). vph_before/vph_after are
    filled by the metrics step (Phase 2 step 3); NULL at collection time."""

    __tablename__ = "packaging_changes"
    __table_args__ = (Index("ix_packaging_changes_video_detected", "video_id", "detected_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    video_id: Mapped[str] = mapped_column(
        ForeignKey("competitor_videos.video_id", ondelete="CASCADE")
    )
    field: Mapped[str] = mapped_column(String)  # "title" | "thumbnail"
    old_value: Mapped[str] = mapped_column(Text, default="")
    new_value: Mapped[str] = mapped_column(Text, default="")
    detected_at: Mapped[datetime] = mapped_column(default=_now)
    vph_before: Mapped[float | None] = mapped_column(Float, default=None)
    vph_after: Mapped[float | None] = mapped_column(Float, default=None)


class VideoAttributesRow(Base):
    """Structured attributes derived from a video's packaging, keyed by source.

    `source="syntactic"` holds the deterministic title features (regex/heuristic,
    no LLM); `title_llm`/`thumbnail_llm` hold the opt-in AI enrichment.
    `content_hash` is the hash of the analysed content — the title for the
    syntactic/title sources, the thumbnail's IMAGE BYTES for the thumbnail one —
    so a row is recomputed only when the content changes and can be shared across
    projects. Scores are never stored here — only raw attributes (plus the boolean
    facets derived from them, which the pattern distributions read).

    `state`/`error` exist for the enrichment, whose LLM call is asynchronous: they
    let the UI poll a row the way it polls a channel's backfill. The `"done"`
    default keeps every deterministic (syntactic) row correct with no backfill.
    """

    __tablename__ = "video_attributes"
    __table_args__ = (
        Index("ix_video_attributes_video_source", "video_id", "source", unique=True),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    video_id: Mapped[str] = mapped_column(
        ForeignKey("competitor_videos.video_id", ondelete="CASCADE")
    )
    source: Mapped[str] = mapped_column(String)  # "syntactic" | "title_llm" | "thumbnail_llm"
    attributes: Mapped[dict] = mapped_column(JSON, default=dict)
    content_hash: Mapped[str] = mapped_column(String, default="")
    state: Mapped[str] = mapped_column(String, default="done")  # "running" | "done" | "error"
    error: Mapped[str] = mapped_column(Text, default="")
    updated_at: Mapped[datetime] = mapped_column(default=_now, onupdate=_now)


# ------------------------- Phase 2: comments -------------------------
#
# Split by cost, per guideline 9: the raw comments are COLLECTED automatically by
# the snapshot cycle (deterministic, quota-accounted), while the synthesis over
# them costs an LLM call and therefore only ever runs from an explicit action.


class VideoCommentRow(Base):
    """A raw top comment of an outlier video, as collected from the Data API.

    Stored verbatim and never interpreted here: the synthesis reads these rows,
    and every quote it reports is resolved back to one of them, so the model can
    never invent audience words (the transcript golden rule, applied to comments).
    """

    __tablename__ = "video_comments"
    __table_args__ = (
        Index("ix_video_comments_video_comment", "video_id", "comment_id", unique=True),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    video_id: Mapped[str] = mapped_column(
        ForeignKey("competitor_videos.video_id", ondelete="CASCADE")
    )
    comment_id: Mapped[str] = mapped_column(String)
    text: Mapped[str] = mapped_column(Text, default="")
    like_count: Mapped[int] = mapped_column(Integer, default=0)
    author: Mapped[str] = mapped_column(String, default="")
    fetched_at: Mapped[datetime] = mapped_column(default=_now)


class VideoEnrichmentRow(Base):
    """An opt-in LLM synthesis over a video's collected data, keyed by kind.

    Only `kind="comment_synthesis"` exists today. It lives here rather than in
    `video_attributes` on purpose: `metrics.patterns` turns EVERY boolean in an
    attributes row into a reported packaging pattern, and a comment synthesis is
    narrative — it must not reach the conditional distributions.

    `content_hash` is the hash of the analysed comment set, so the pass is paid
    for once and shared across projects; `state`/`error` mirror the attributes row
    so the UI polls an in-flight call the same way.
    """

    __tablename__ = "video_enrichments"
    __table_args__ = (
        Index("ix_video_enrichments_video_kind", "video_id", "kind", unique=True),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    video_id: Mapped[str] = mapped_column(
        ForeignKey("competitor_videos.video_id", ondelete="CASCADE")
    )
    kind: Mapped[str] = mapped_column(String)  # "comment_synthesis"
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    content_hash: Mapped[str] = mapped_column(String, default="")
    state: Mapped[str] = mapped_column(String, default="done")  # "running" | "done" | "error"
    error: Mapped[str] = mapped_column(Text, default="")
    updated_at: Mapped[datetime] = mapped_column(default=_now, onupdate=_now)


# ------------------------- Phase 2: events (append-only) + seen state -------------------------
#
# The append-only fact log behind the Radar home. An outlier score / VPH is
# computed on read and does not survive a recompute, so "crossed the p90 cut
# yesterday at 14h" is a fact that must be RECORDED when it happens or it is lost.
# Rows are never mutated except for `seen_at` (niche, dismissible) and
# `resolved_at` (ops, self-resolving) — the payload is frozen at emission so a
# later config change cannot rewrite the meaning of a recorded band.
#
# A niche event is a fact of the CHANNEL (project_id NULL): it is written ONCE and
# attributed to projects on read by joining `project_channels`, so a channel in
# two projects yields one row rendered with two badges, never a duplicate. An ops
# event is a project-scoped failure (project_id filled, severity="error").


class EventRow(Base):
    __tablename__ = "events"
    # (category, occurred_at DESC): the Radar feed is `WHERE category=? ORDER BY
    # occurred_at DESC LIMIT n`, so this index makes it O(limit) — it never scans
    # the snapshot series to reconstruct when a threshold was crossed.
    __table_args__ = (Index("ix_events_category_occurred", "category", desc("occurred_at")),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    category: Mapped[str] = mapped_column(String)  # "niche" | "ops"
    type: Mapped[str] = mapped_column(String)
    severity: Mapped[str] = mapped_column(String, default="info")  # "info" | "error"
    # All four subjects are nullable and intentionally NOT foreign keys: the log is
    # an append-only historical record and must outlive the deletion of any subject
    # it references. project_id is NULL for niche facts (attributed on read) and
    # filled for ops failures.
    project_id: Mapped[int | None] = mapped_column(Integer, default=None)
    channel_id: Mapped[str | None] = mapped_column(String, default=None)
    video_id: Mapped[str | None] = mapped_column(String, default=None)
    live_id: Mapped[str | None] = mapped_column(String, default=None)
    occurred_at: Mapped[datetime] = mapped_column(default=_now)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    # seen_at is the event's, not the (event, project) pair's: dismissing a niche
    # event dismisses it everywhere. Ops events are never marked seen — they clear
    # via resolved_at, because a failure does not resolve by being looked at.
    seen_at: Mapped[datetime | None] = mapped_column(default=None)
    resolved_at: Mapped[datetime | None] = mapped_column(default=None)
    created_at: Mapped[datetime] = mapped_column(default=_now)
