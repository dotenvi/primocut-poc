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

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .models import (
    ChannelSnapshotRow,
    CompetitorChannelRow,
    CompetitorVideoRow,
    PackagingChangeRow,
    ProjectChannelRow,
    ProjectRow,
    VideoAttributesRow,
    VideoSnapshotRow,
)


class ProjectExistsError(Exception):
    """A project with the same name already exists."""


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
) -> VideoAttributesRow:
    row = get_video_attributes(s, video_id, source)
    if row is None:
        row = VideoAttributesRow(video_id=video_id, source=source)
        s.add(row)
    row.attributes = attributes
    row.content_hash = content_hash
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
