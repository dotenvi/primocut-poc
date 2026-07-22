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
    UniqueConstraint,
    desc,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class VideoRow(Base):
    """A live the user submits to the pipeline. It exists to become clips for a
    niche, so it is CONTAINED in exactly one project (1 live -> 1 project); the
    clip (Phase 3) is what fans out to several own channels of that niche."""

    __tablename__ = "videos"

    id: Mapped[str] = mapped_column(String, primary_key=True)  # YouTube video id
    # Containment (not a filter): a live belongs to a single niche. NOT NULL —
    # fresh DBs get it from create_all, legacy rows are backfilled at init().
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
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


# ------------------------- Phase 3: assisted cutting (decision bench) -------------------------
#
# Two DELIBERATELY separate concepts (they were one `topics.status` field before,
# dropped 2026-07-13 with the retired "export cuts" feature and now reinstated as
# two — see the 2026-07-19 decision log). Both tables are created by create_all
# (whole new tables), never by an NNN_*.sql migration.
#
# 1. TopicTriageRow = the user's OPINION about a topic, scoped to the LIVE and
#    shared across its cuts. Absence of a row means `open`; the only stored states
#    are `maybe`/`discarded`. `in` is NEVER written here.
# 2. CutRow + CutItemRow = an ORDERED composition. Membership (`in`) is derived
#    exclusively from cut_items of the ACTIVE cut, so a column can never again
#    conflate an opinion with a membership.


class TopicTriageRow(Base):
    """Per-live triage of a topic (the user's opinion, persisted across sessions).

    Keyed uniquely per (video_id, topic_id). `topic_id` CASCADEs: regenerating a
    live's topics replaces every topic row, and an opinion about a topic that no
    longer exists is moot, so it goes with it.
    """

    __tablename__ = "topic_triage"
    __table_args__ = (
        UniqueConstraint("video_id", "topic_id", name="uq_topic_triage_video_topic"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    video_id: Mapped[str] = mapped_column(
        ForeignKey("videos.id", ondelete="CASCADE"), index=True
    )
    topic_id: Mapped[int] = mapped_column(ForeignKey("topics.id", ondelete="CASCADE"))
    state: Mapped[str] = mapped_column(String)  # "maybe" | "discarded"
    updated_at: Mapped[datetime] = mapped_column(default=_now, onupdate=_now)


class CutRow(Base):
    """An ordered composition over one live. A live has N cuts; each live gets a
    default `Cut 1` created lazily. `status` leaves room for later steps
    (`rendering`/`rendered`) but stays `draft` in the decision-bench step."""

    __tablename__ = "cuts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    video_id: Mapped[str] = mapped_column(
        ForeignKey("videos.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String, default="Cut 1")
    status: Mapped[str] = mapped_column(String, default="draft")
    created_at: Mapped[datetime] = mapped_column(default=_now)
    updated_at: Mapped[datetime] = mapped_column(default=_now, onupdate=_now)


class CutItemRow(Base):
    """One ordered slot of a cut: a topic OR a manual time range.

    Every item stores its own `start_s`/`end_s` — snapshotted from the topic for a
    topic item. `topic_id` is `ON DELETE SET NULL` (not CASCADE): regenerating the
    live's topics must not silently drop items from a composition, so a topic item
    degrades into a preserved manual range instead of disappearing.
    """

    __tablename__ = "cut_items"
    __table_args__ = (Index("ix_cut_items_cut_position", "cut_id", "position"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    cut_id: Mapped[int] = mapped_column(ForeignKey("cuts.id", ondelete="CASCADE"), index=True)
    position: Mapped[int] = mapped_column(Integer, default=0)  # 0..n-1, contiguous
    topic_id: Mapped[int | None] = mapped_column(
        ForeignKey("topics.id", ondelete="SET NULL"), default=None
    )
    start_s: Mapped[float] = mapped_column(Float)
    end_s: Mapped[float] = mapped_column(Float)


class ClipRow(Base):
    """A rendered clip: one MP4 deliverable per cut item (H.264 + AAC, original
    resolution), produced by the section download + FFmpeg re-encode.

    Keyed 1:1 to its `cut_item` (`ON DELETE CASCADE`): removing the item from the
    composition — an explicit user action — takes its clip row with it (the file
    is unlinked in the same route). There is NO automatic/scheduled cleanup of
    clips (contrast the ephemeral audio): they are deliverables, deleted only by
    an explicit action.

    `params_hash` is the pay-once cache key over (range + padding + encode
    profile): the same item re-rendered with the same parameters is skipped;
    `regenerate` forces it. `encode_profile` is what was REQUESTED
    (`nvenc`/`libx264`/`copy`); `encoder_used` is what actually produced the file
    (records `libx264` when NVENC fell back).
    """

    __tablename__ = "clips"
    __table_args__ = (Index("ix_clips_cut_item", "cut_item_id", unique=True),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    cut_item_id: Mapped[int] = mapped_column(
        ForeignKey("cut_items.id", ondelete="CASCADE"), index=True
    )
    # Denormalized so the file path/URL can be built without joins.
    video_id: Mapped[str] = mapped_column(String, index=True)
    cut_id: Mapped[int] = mapped_column(Integer, index=True)
    position: Mapped[int] = mapped_column(Integer, default=0)  # NNN at render time
    filename: Mapped[str] = mapped_column(String, default="")  # "001-slug.mp4"
    rel_path: Mapped[str] = mapped_column(String, default="")  # "<vid>/<cut>/001-slug.mp4"
    # queued -> downloading_section -> [aligning] -> cutting -> done | error
    # (aligning is the surgical WhisperX boundary pass; present only when an align
    # venv is configured, otherwise the item goes straight to cutting.)
    state: Mapped[str] = mapped_column(String, default="queued")
    error: Mapped[str] = mapped_column(Text, default="")
    encode_profile: Mapped[str] = mapped_column(String, default="")  # requested
    encoder_used: Mapped[str] = mapped_column(String, default="")  # effective
    params_hash: Mapped[str] = mapped_column(String, default="")  # pay-once cache key
    padding_s: Mapped[float] = mapped_column(Float, default=0.0)
    # `start_s`/`end_s` are the REQUESTED bounds (the cut item's range). The
    # surgical WhisperX pass can snap the ACTUAL cut points to a word edge, so the
    # effective encoded window is recorded separately — the export SRT offset and
    # the manifest timestamps must reflect what the MP4 really contains. With
    # alignment off they equal the requested bounds.
    start_s: Mapped[float] = mapped_column(Float, default=0.0)
    end_s: Mapped[float] = mapped_column(Float, default=0.0)
    render_start_s: Mapped[float] = mapped_column(Float, default=0.0)
    render_end_s: Mapped[float] = mapped_column(Float, default=0.0)
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    duration_s: Mapped[float] = mapped_column(Float, default=0.0)
    created_at: Mapped[datetime] = mapped_column(default=_now)
    updated_at: Mapped[datetime] = mapped_column(default=_now, onupdate=_now)


class ExportRow(Base):
    """The state of a cut's latest export (one row per cut, upserted).

    An export packages the cut's rendered clips into a deliverable bundle on disk
    (see `render/package.py`); this row is what the UI polls to learn when the zip
    is ready. History is kept on disk (timestamped package dirs), but only the
    LATEST export's paths/state live here. `package_rel_path`/`zip_rel_path` are
    relative to `exports_dir`.
    """

    __tablename__ = "exports"
    __table_args__ = (Index("ix_exports_cut", "cut_id", unique=True),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    video_id: Mapped[str] = mapped_column(String, index=True)
    cut_id: Mapped[int] = mapped_column(Integer, index=True)
    # queued -> packaging -> done | error
    state: Mapped[str] = mapped_column(String, default="queued")
    error: Mapped[str] = mapped_column(Text, default="")
    package_rel_path: Mapped[str] = mapped_column(String, default="")
    zip_rel_path: Mapped[str] = mapped_column(String, default="")
    item_count: Mapped[int] = mapped_column(Integer, default=0)
    skip_master: Mapped[bool] = mapped_column(Boolean, default=False)
    upscaled: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(default=_now)
    updated_at: Mapped[datetime] = mapped_column(default=_now, onupdate=_now)


class CutPackagingRow(Base):
    """An opt-in AI packaging artifact for ONE cut item (guideline 9).

    Keyed per (cut_item_id, kind): a clip is a video of its own, so its titles /
    thumbnail brief / frame ranking are generated and cached per clip, and the
    export manifest reads one row per item. `ON DELETE CASCADE` with the item:
    removing the item from the composition drops its packaging with it.

    `kind` ∈ `title | thumb_brief | frame_ranking`. `title`/`thumb_brief` are LLM
    passes; `frame_ranking` is the opt-in vision pass over the deterministically
    extracted frames (extraction itself writes JPEGs to disk, no row). Paid for once
    by `content_hash` (over the clip content + the niche patterns that shaped it);
    `state`/`error` let the UI poll an in-flight generation, mirroring
    `video_attributes`.
    """

    __tablename__ = "cut_packaging"
    __table_args__ = (
        Index("ix_cut_packaging_item_kind", "cut_item_id", "kind", unique=True),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    cut_item_id: Mapped[int] = mapped_column(
        ForeignKey("cut_items.id", ondelete="CASCADE"), index=True
    )
    # Denormalized so the export/bench can group without a join back to the item.
    cut_id: Mapped[int] = mapped_column(Integer, index=True)
    kind: Mapped[str] = mapped_column(String)  # "title" | "thumb_brief" | "frame_ranking"
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    content_hash: Mapped[str] = mapped_column(String, default="")
    state: Mapped[str] = mapped_column(String, default="done")  # "running" | "done" | "error"
    error: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(default=_now)
    updated_at: Mapped[datetime] = mapped_column(default=_now, onupdate=_now)


class LiveAudienceMatchRow(Base):
    """The cached audience→topic match for a live (opt-in — costs one cheap LLM).

    One row per live: the semantic match of the niche's synthesised audience
    requests to the live's topics (the deferred `audience_asked` bench signal). The
    payload maps topic_id → the requests it delivers. `content_hash` folds the
    live's topics AND the niche's request set, so it is recomputed only when either
    changes; degrades silently to an empty match when the niche has no syntheses.
    """

    __tablename__ = "live_audience_match"

    video_id: Mapped[str] = mapped_column(
        ForeignKey("videos.id", ondelete="CASCADE"), primary_key=True
    )
    content_hash: Mapped[str] = mapped_column(String, default="")
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    state: Mapped[str] = mapped_column(String, default="done")
    error: Mapped[str] = mapped_column(Text, default="")
    updated_at: Mapped[datetime] = mapped_column(default=_now, onupdate=_now)


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
    """A project is a NICHE. It contains the user's own channels (destinations
    for the clips) and the competitor channels watched for that niche. It is not
    a single channel: one niche holds several own channels."""

    __tablename__ = "projects"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String, unique=True)
    description: Mapped[str] = mapped_column(Text, default="")
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(default=_now)
    updated_at: Mapped[datetime] = mapped_column(default=_now, onupdate=_now)


class OwnChannelRow(Base):
    """A channel the USER owns inside a niche — where the clips get posted. Owned
    by exactly one project (direct FK, not the M:N of competitor_channels). Not
    to be confused with CompetitorChannelRow; receives no snapshots in this phase."""

    __tablename__ = "own_channels"
    __table_args__ = (
        UniqueConstraint("project_id", "channel_url", name="uq_own_channel_url_per_project"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    channel_url: Mapped[str] = mapped_column(String)
    handle: Mapped[str] = mapped_column(String, default="")
    title: Mapped[str] = mapped_column(String, default="")
    role: Mapped[str | None] = mapped_column(String, default=None)  # 'main' | 'shorts' | free
    added_at: Mapped[datetime] = mapped_column(default=_now)


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
