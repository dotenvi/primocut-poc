"""DAO for Phase 2 competitor modeling: projects, global channels, the M:N link
and competitor videos.

Multi-project semantics enforced here:
- Channels are GLOBAL (deduplicated by channel_id); videos are deduplicated by
  video_id.
- Detaching a channel (or deleting a project) NEVER deletes channel/video data;
  a channel orphaned from every project is deactivated (`active=False`).
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from .models import (
    ChannelSnapshotRow,
    CompetitorChannelRow,
    CompetitorVideoRow,
    OwnChannelRow,
    PackagingChangeRow,
    ProjectChannelRow,
    ProjectRow,
    VideoAttributesRow,
    VideoCommentRow,
    VideoEnrichmentRow,
    VideoSnapshotRow,
)


class ProjectExistsError(Exception):
    """A project with the same name already exists."""


class OwnChannelExistsError(Exception):
    """The same channel_url is already registered in this project."""


# ------------------------- Projects -------------------------


def create_project(s: Session, name: str, description: str = "") -> ProjectRow:
    name = name.strip()
    if not name:
        raise ValueError("Project name must not be empty.")
    existing = s.execute(
        select(ProjectRow).where(ProjectRow.name == name)
    ).scalars().first()
    if existing is not None:
        raise ProjectExistsError(name)
    row = ProjectRow(name=name, description=description)
    s.add(row)
    s.flush()
    return row


def get_project(s: Session, project_id: int) -> ProjectRow | None:
    return s.get(ProjectRow, project_id)


def list_projects(s: Session) -> list[ProjectRow]:
    return list(
        s.execute(select(ProjectRow).order_by(ProjectRow.created_at.desc())).scalars()
    )


def update_project(s: Session, project_id: int, **fields) -> ProjectRow | None:
    row = s.get(ProjectRow, project_id)
    if row is None:
        return None
    for key, value in fields.items():
        if value is not None:
            setattr(row, key, value)
    s.flush()
    return row


def delete_project(s: Session, project_id: int) -> bool:
    """Delete the project, detaching (never deleting) its channels.

    The FK cascade removes the project's link rows; afterwards, any channel left
    orphaned from every project is deactivated. Channel/video data is preserved.
    """
    row = s.get(ProjectRow, project_id)
    if row is None:
        return False
    channel_ids = [c.channel_id for c in list_project_channels(s, project_id)]
    s.delete(row)  # cascade removes the project_channels link rows
    s.flush()
    for channel_id in channel_ids:
        deactivate_if_orphan(s, channel_id)
    return True


# ------------------------- Own channels (the user's, per project) -------------------------


def create_own_channel(
    s: Session,
    project_id: int,
    *,
    channel_url: str,
    handle: str = "",
    title: str = "",
    role: str | None = None,
) -> OwnChannelRow:
    """Register one of the user's own channels inside a niche (the clip
    destination). Rejects a blank URL and a duplicate URL within the project."""
    channel_url = channel_url.strip()
    if not channel_url:
        raise ValueError("channel_url must not be empty.")
    existing = s.execute(
        select(OwnChannelRow).where(
            OwnChannelRow.project_id == project_id,
            OwnChannelRow.channel_url == channel_url,
        )
    ).scalars().first()
    if existing is not None:
        raise OwnChannelExistsError(channel_url)
    row = OwnChannelRow(
        project_id=project_id, channel_url=channel_url,
        handle=handle, title=title, role=role,
    )
    s.add(row)
    s.flush()
    return row


def list_own_channels(s: Session, project_id: int) -> list[OwnChannelRow]:
    return list(
        s.execute(
            select(OwnChannelRow)
            .where(OwnChannelRow.project_id == project_id)
            .order_by(OwnChannelRow.added_at.desc(), OwnChannelRow.id.desc())
        ).scalars()
    )


def delete_own_channel(s: Session, project_id: int, own_channel_id: int) -> bool:
    """Delete one own channel, scoped to its project so a project never deletes
    another niche's channel. Returns False if there is no such row here."""
    row = s.get(OwnChannelRow, own_channel_id)
    if row is None or row.project_id != project_id:
        return False
    s.delete(row)
    s.flush()
    return True


# ------------------------- Channels (global) -------------------------


def get_channel(s: Session, channel_id: str) -> CompetitorChannelRow | None:
    return s.get(CompetitorChannelRow, channel_id)


def upsert_channel(
    s: Session,
    *,
    channel_id: str,
    handle: str = "",
    title: str = "",
    uploads_playlist_id: str = "",
    active: bool = True,
    backfill_state: str | None = None,
) -> CompetitorChannelRow:
    row = s.get(CompetitorChannelRow, channel_id)
    if row is None:
        row = CompetitorChannelRow(channel_id=channel_id)
        s.add(row)
    row.handle = handle or row.handle
    row.title = title or row.title
    row.uploads_playlist_id = uploads_playlist_id or row.uploads_playlist_id
    row.active = active
    if backfill_state is not None:
        row.backfill_state = backfill_state
    s.flush()
    return row


def set_channel_active(s: Session, channel_id: str, active: bool) -> None:
    row = s.get(CompetitorChannelRow, channel_id)
    if row is not None:
        row.active = active


def set_backfill(s: Session, channel_id: str, **fields) -> CompetitorChannelRow | None:
    row = s.get(CompetitorChannelRow, channel_id)
    if row is not None:
        for key, value in fields.items():
            setattr(row, key, value)
    return row


# ------------------------- Project ⇄ Channel link -------------------------


def list_project_channels(s: Session, project_id: int) -> list[CompetitorChannelRow]:
    return list(
        s.execute(
            select(CompetitorChannelRow)
            .join(
                ProjectChannelRow,
                ProjectChannelRow.channel_id == CompetitorChannelRow.channel_id,
            )
            .where(ProjectChannelRow.project_id == project_id)
            .order_by(CompetitorChannelRow.created_at.desc())
        ).scalars()
    )


def link_channel(s: Session, project_id: int, channel_id: str) -> bool:
    """Link a channel to a project. Idempotent: returns False if already linked."""
    existing = s.get(
        ProjectChannelRow, {"project_id": project_id, "channel_id": channel_id}
    )
    if existing is not None:
        return False
    s.add(ProjectChannelRow(project_id=project_id, channel_id=channel_id))
    s.flush()
    return True


def unlink_channel(s: Session, project_id: int, channel_id: str) -> bool:
    """Remove a project↔channel link. Returns False if the link did not exist."""
    existing = s.get(
        ProjectChannelRow, {"project_id": project_id, "channel_id": channel_id}
    )
    if existing is None:
        return False
    s.delete(existing)
    s.flush()
    return True


def count_channel_projects(s: Session, channel_id: str) -> int:
    return s.execute(
        select(func.count())
        .select_from(ProjectChannelRow)
        .where(ProjectChannelRow.channel_id == channel_id)
    ).scalar_one()


def deactivate_if_orphan(s: Session, channel_id: str) -> bool:
    """Deactivate a channel that is no longer linked to any project. Returns
    True if it was deactivated. Data is always preserved."""
    if count_channel_projects(s, channel_id) > 0:
        return False
    row = s.get(CompetitorChannelRow, channel_id)
    if row is not None and row.active:
        row.active = False
        return True
    return False


# ------------------------- Competitor videos -------------------------


def upsert_video(
    s: Session,
    *,
    video_id: str,
    channel_id: str,
    title: str = "",
    published_at: datetime | None = None,
    duration_s: int = 0,
    is_short: bool = False,
    thumbnail_url: str = "",
    thumbnail_hash: str = "",
) -> CompetitorVideoRow:
    row = s.get(CompetitorVideoRow, video_id)
    if row is None:
        row = CompetitorVideoRow(video_id=video_id, channel_id=channel_id)
        s.add(row)
    row.title = title
    row.published_at = published_at
    row.duration_s = duration_s
    row.is_short = is_short
    row.thumbnail_url = thumbnail_url
    row.thumbnail_hash = thumbnail_hash
    s.flush()
    return row


def count_channel_videos(s: Session, channel_id: str) -> int:
    return s.execute(
        select(func.count())
        .select_from(CompetitorVideoRow)
        .where(CompetitorVideoRow.channel_id == channel_id)
    ).scalar_one()


def list_channel_videos(s: Session, channel_id: str) -> list[CompetitorVideoRow]:
    return list(
        s.execute(
            select(CompetitorVideoRow)
            .where(CompetitorVideoRow.channel_id == channel_id)
            .order_by(CompetitorVideoRow.published_at.desc())
        ).scalars()
    )


# ------------------------- Continuous collection (snapshots) -------------------------


def list_active_channels(s: Session) -> list[CompetitorChannelRow]:
    """Channels currently collected: linked to at least one project. Orphans
    (active=false) are excluded, so they fall out of every snapshot cycle."""
    return list(
        s.execute(
            select(CompetitorChannelRow)
            .where(CompetitorChannelRow.active.is_(True))
            .order_by(CompetitorChannelRow.channel_id)
        ).scalars()
    )


def list_channel_video_ids(
    s: Session, channel_id: str, *, since: datetime | None = None
) -> list[str]:
    """Video IDs of a channel. With `since`, only videos published at/after it
    (a NULL published_at is excluded from the recent window)."""
    stmt = select(CompetitorVideoRow.video_id).where(
        CompetitorVideoRow.channel_id == channel_id
    )
    if since is not None:
        stmt = stmt.where(CompetitorVideoRow.published_at.is_not(None)).where(
            CompetitorVideoRow.published_at >= since
        )
    return list(s.execute(stmt).scalars())


def add_channel_snapshot(
    s: Session,
    *,
    channel_id: str,
    captured_at: datetime,
    subscribers: int,
    total_views: int,
    video_count: int,
) -> ChannelSnapshotRow:
    row = ChannelSnapshotRow(
        channel_id=channel_id,
        captured_at=captured_at,
        subscribers=subscribers,
        total_views=total_views,
        video_count=video_count,
    )
    s.add(row)
    return row


def add_video_snapshot(
    s: Session,
    *,
    video_id: str,
    captured_at: datetime,
    views: int,
    likes: int,
    comments: int,
) -> VideoSnapshotRow:
    row = VideoSnapshotRow(
        video_id=video_id,
        captured_at=captured_at,
        views=views,
        likes=likes,
        comments=comments,
    )
    s.add(row)
    return row


def add_packaging_change(
    s: Session,
    *,
    video_id: str,
    field: str,
    old_value: str,
    new_value: str,
    detected_at: datetime,
) -> PackagingChangeRow:
    row = PackagingChangeRow(
        video_id=video_id,
        field=field,
        old_value=old_value,
        new_value=new_value,
        detected_at=detected_at,
    )
    s.add(row)
    return row


def update_video_packaging(
    s: Session,
    video_id: str,
    *,
    title: str | None = None,
    thumbnail_url: str | None = None,
    thumbnail_hash: str | None = None,
) -> None:
    """Update the last-known packaging state stored on the video row."""
    row = s.get(CompetitorVideoRow, video_id)
    if row is None:
        return
    if title is not None:
        row.title = title
    if thumbnail_url is not None:
        row.thumbnail_url = thumbnail_url
    if thumbnail_hash is not None:
        row.thumbnail_hash = thumbnail_hash


# ------------------------- Metrics reads (computed on read) -------------------------


def get_video(s: Session, video_id: str) -> CompetitorVideoRow | None:
    return s.get(CompetitorVideoRow, video_id)


def list_video_snapshots(s: Session, video_id: str) -> list[VideoSnapshotRow]:
    """A video's snapshots ordered by capture time (ascending) — the raw series
    from which VPH and outlier scores are derived on read."""
    return list(
        s.execute(
            select(VideoSnapshotRow)
            .where(VideoSnapshotRow.video_id == video_id)
            .order_by(VideoSnapshotRow.captured_at)
        ).scalars()
    )


def latest_channel_snapshot(s: Session, channel_id: str) -> ChannelSnapshotRow | None:
    return s.execute(
        select(ChannelSnapshotRow)
        .where(ChannelSnapshotRow.channel_id == channel_id)
        .order_by(ChannelSnapshotRow.captured_at.desc())
    ).scalars().first()


def latest_views(s: Session, video_ids: list[str]) -> dict[str, int]:
    """Map each video id to the views of its most recent snapshot (videos with no
    snapshot are absent from the result)."""
    if not video_ids:
        return {}
    latest = (
        select(
            VideoSnapshotRow.video_id.label("vid"),
            func.max(VideoSnapshotRow.captured_at).label("mx"),
        )
        .where(VideoSnapshotRow.video_id.in_(video_ids))
        .group_by(VideoSnapshotRow.video_id)
        .subquery()
    )
    rows = s.execute(
        select(VideoSnapshotRow.video_id, VideoSnapshotRow.views).join(
            latest,
            (VideoSnapshotRow.video_id == latest.c.vid)
            & (VideoSnapshotRow.captured_at == latest.c.mx),
        )
    ).all()
    return {vid: views for vid, views in rows}


def latest_video_snapshot(s: Session, video_id: str) -> VideoSnapshotRow | None:
    return s.execute(
        select(VideoSnapshotRow)
        .where(VideoSnapshotRow.video_id == video_id)
        .order_by(VideoSnapshotRow.captured_at.desc())
    ).scalars().first()


def list_packaging_changes(
    s: Session, *, video_id: str | None = None, channel_id: str | None = None
) -> list[PackagingChangeRow]:
    """Packaging-change events, by video or by channel (newest first)."""
    stmt = select(PackagingChangeRow)
    if video_id is not None:
        stmt = stmt.where(PackagingChangeRow.video_id == video_id)
    if channel_id is not None:
        stmt = stmt.join(
            CompetitorVideoRow,
            CompetitorVideoRow.video_id == PackagingChangeRow.video_id,
        ).where(CompetitorVideoRow.channel_id == channel_id)
    return list(s.execute(stmt.order_by(PackagingChangeRow.detected_at.desc())).scalars())


def list_pending_packaging_changes(s: Session) -> list[PackagingChangeRow]:
    """Events still missing their impact figures (vph_after not yet filled)."""
    return list(
        s.execute(
            select(PackagingChangeRow).where(PackagingChangeRow.vph_after.is_(None))
        ).scalars()
    )


def set_packaging_impact(
    s: Session,
    change_id: int,
    *,
    vph_before: float | None,
    vph_after: float | None,
) -> None:
    row = s.get(PackagingChangeRow, change_id)
    if row is not None:
        row.vph_before = vph_before
        row.vph_after = vph_after


def list_project_videos(s: Session, project_id: int) -> list[CompetitorVideoRow]:
    """Every video of every channel linked to a project (deduplicated by video)."""
    return list(
        s.execute(
            select(CompetitorVideoRow)
            .join(
                ProjectChannelRow,
                ProjectChannelRow.channel_id == CompetitorVideoRow.channel_id,
            )
            .where(ProjectChannelRow.project_id == project_id)
            .order_by(CompetitorVideoRow.published_at.desc())
        ).scalars()
    )


# ------------------------- Video attributes (persisted) -------------------------


def get_video_attributes(
    s: Session, video_id: str, source: str
) -> VideoAttributesRow | None:
    return s.execute(
        select(VideoAttributesRow)
        .where(VideoAttributesRow.video_id == video_id)
        .where(VideoAttributesRow.source == source)
    ).scalars().first()


def upsert_video_attributes(
    s: Session,
    *,
    video_id: str,
    source: str,
    attributes: dict,
    content_hash: str,
    state: str = "done",
    error: str = "",
) -> VideoAttributesRow:
    row = get_video_attributes(s, video_id, source)
    if row is None:
        row = VideoAttributesRow(video_id=video_id, source=source)
        s.add(row)
    row.attributes = attributes
    row.content_hash = content_hash
    row.state = state
    row.error = error
    s.flush()
    return row


def set_video_attributes_state(
    s: Session,
    video_id: str,
    source: str,
    *,
    state: str,
    error: str = "",
    clear_hash: bool = False,
) -> VideoAttributesRow:
    """Move a row's enrichment state without touching its attributes.

    Creating the row when it is missing is what lets the UI see a first-ever
    enrichment go "running". `clear_hash` drops the cache key so a failed video is
    retried on the next explicit action instead of the failure being cached.
    """
    row = get_video_attributes(s, video_id, source)
    if row is None:
        row = VideoAttributesRow(video_id=video_id, source=source, attributes={})
        s.add(row)
    row.state = state
    row.error = error
    if clear_hash:
        row.content_hash = ""
    s.flush()
    return row


def list_video_attributes(
    s: Session, video_ids: list[str], source: str
) -> dict[str, VideoAttributesRow]:
    """Attribute rows for a set of videos, keyed by video id."""
    if not video_ids:
        return {}
    rows = s.execute(
        select(VideoAttributesRow)
        .where(VideoAttributesRow.source == source)
        .where(VideoAttributesRow.video_id.in_(video_ids))
    ).scalars()
    return {r.video_id: r for r in rows}


# ------------------------- Video comments (collected automatically) -------------------------


def list_video_comments(s: Session, video_id: str) -> list[VideoCommentRow]:
    """A video's collected comments, most-liked first — the order the synthesis
    indexes them in, so a resolved quote is stable across runs."""
    return list(
        s.execute(
            select(VideoCommentRow)
            .where(VideoCommentRow.video_id == video_id)
            .order_by(VideoCommentRow.like_count.desc(), VideoCommentRow.comment_id)
        ).scalars()
    )


def count_video_comments(s: Session, video_id: str) -> int:
    return int(
        s.execute(
            select(func.count())
            .select_from(VideoCommentRow)
            .where(VideoCommentRow.video_id == video_id)
        ).scalar_one()
    )


def replace_video_comments(s: Session, video_id: str, comments, *, fetched_at: datetime) -> int:
    """Store a freshly collected comment set, dropping whatever was there.

    A replace (rather than a merge) is what makes an explicit regeneration honest:
    the stored set is always exactly what the API returned on the last collection,
    which is also what the synthesis's content hash is computed over.
    """
    s.execute(delete(VideoCommentRow).where(VideoCommentRow.video_id == video_id))
    stored = 0
    seen: set[str] = set()
    for c in comments:
        # Paging is not a snapshot: a comment posted mid-fetch can shift the
        # relevance order and re-emit one we already have. Dropping the repeat
        # beats letting it trip the (video_id, comment_id) unique index.
        if c.comment_id in seen:
            continue
        seen.add(c.comment_id)
        s.add(
            VideoCommentRow(
                video_id=video_id,
                comment_id=c.comment_id,
                text=c.text,
                like_count=c.like_count,
                author=c.author,
                fetched_at=fetched_at,
            )
        )
        stored += 1
    s.flush()
    return stored


def set_video_comments_state(
    s: Session,
    video_id: str,
    *,
    state: str,
    error: str = "",
    fetched_at: datetime | None = None,
) -> None:
    """Record the outcome of a collection attempt on the video row."""
    row = s.get(CompetitorVideoRow, video_id)
    if row is None:
        return
    row.comments_state = state
    row.comments_error = error
    if fetched_at is not None:
        row.comments_fetched_at = fetched_at


# ------------------------- Video enrichments (opt-in LLM syntheses) -------------------------


def get_video_enrichment(s: Session, video_id: str, kind: str) -> VideoEnrichmentRow | None:
    return s.execute(
        select(VideoEnrichmentRow)
        .where(VideoEnrichmentRow.video_id == video_id)
        .where(VideoEnrichmentRow.kind == kind)
    ).scalars().first()


def upsert_video_enrichment(
    s: Session,
    *,
    video_id: str,
    kind: str,
    payload: dict,
    content_hash: str,
    state: str = "done",
    error: str = "",
) -> VideoEnrichmentRow:
    row = get_video_enrichment(s, video_id, kind)
    if row is None:
        row = VideoEnrichmentRow(video_id=video_id, kind=kind)
        s.add(row)
    row.payload = payload
    row.content_hash = content_hash
    row.state = state
    row.error = error
    s.flush()
    return row


def set_video_enrichment_state(
    s: Session,
    video_id: str,
    kind: str,
    *,
    state: str,
    error: str = "",
    clear_hash: bool = False,
) -> VideoEnrichmentRow:
    """Move a synthesis row's state without touching its payload (see
    `set_video_attributes_state` — same contract, same reasons)."""
    row = get_video_enrichment(s, video_id, kind)
    if row is None:
        row = VideoEnrichmentRow(video_id=video_id, kind=kind, payload={})
        s.add(row)
    row.state = state
    row.error = error
    if clear_hash:
        row.content_hash = ""
    s.flush()
    return row


def list_project_enrichments(
    s: Session, project_id: int, kind: str
) -> list[VideoEnrichmentRow]:
    """Every synthesis of the given kind across a project's channels — the input
    to the project-level aggregation (scoped, so one niche never sees another's)."""
    return list(
        s.execute(
            select(VideoEnrichmentRow)
            .join(
                CompetitorVideoRow,
                CompetitorVideoRow.video_id == VideoEnrichmentRow.video_id,
            )
            .join(
                ProjectChannelRow,
                ProjectChannelRow.channel_id == CompetitorVideoRow.channel_id,
            )
            .where(ProjectChannelRow.project_id == project_id)
            .where(VideoEnrichmentRow.kind == kind)
            .where(VideoEnrichmentRow.state == "done")
        ).scalars()
    )
