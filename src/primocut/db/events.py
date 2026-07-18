"""DAO for the append-only event log and its seen/resolved state.

The reads here are the Radar feed's source of truth. Every query is bounded by a
`limit` and hits the `(category, occurred_at DESC)` index — none of them scans the
snapshot series, which is the whole reason the log exists (see `EventRow`).

Niche events carry a `channel_id` but no `project_id`: a channel can belong to
several projects, so attribution is done on read by joining `project_channels`
(`projects_for_channels`), which turns one row into one or more project badges.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime

from sqlalchemy import or_, select, update
from sqlalchemy.orm import Session

from .models import (
    CompetitorChannelRow,
    CompetitorVideoRow,
    EventRow,
    ProjectChannelRow,
    ProjectRow,
    VideoRow,
)

CATEGORY_NICHE = "niche"
CATEGORY_OPS = "ops"

SEVERITY_INFO = "info"
SEVERITY_ERROR = "error"


def add_event(
    s: Session,
    *,
    category: str,
    type: str,
    occurred_at: datetime,
    severity: str = SEVERITY_INFO,
    project_id: int | None = None,
    channel_id: str | None = None,
    video_id: str | None = None,
    live_id: str | None = None,
    payload: dict | None = None,
    resolved_at: datetime | None = None,
) -> EventRow:
    """Append one event. Never updates an existing row — the log is append-only.

    `resolved_at` is for a born-resolved ops record (e.g. `cycle_done`) that must
    stay out of the open-alerts view."""
    row = EventRow(
        category=category,
        type=type,
        severity=severity,
        project_id=project_id,
        channel_id=channel_id,
        video_id=video_id,
        live_id=live_id,
        occurred_at=occurred_at,
        payload=payload or {},
        resolved_at=resolved_at,
    )
    s.add(row)
    s.flush()
    return row


# ------------------------- project attribution (badges) -------------------------


def channel_ids_in_project(s: Session, project_id: int) -> list[str]:
    return list(
        s.execute(
            select(ProjectChannelRow.channel_id).where(
                ProjectChannelRow.project_id == project_id
            )
        ).scalars()
    )


def projects_for_channels(
    s: Session, channel_ids: list[str]
) -> dict[str, list[dict]]:
    """Map each channel to the projects it is linked to (its Radar badges).

    One query for the whole page of events, so badge resolution stays O(events)
    and never turns into a per-event lookup."""
    ids = [c for c in {cid for cid in channel_ids if cid}]
    if not ids:
        return {}
    rows = s.execute(
        select(ProjectChannelRow.channel_id, ProjectRow.id, ProjectRow.name)
        .join(ProjectRow, ProjectRow.id == ProjectChannelRow.project_id)
        .where(ProjectChannelRow.channel_id.in_(ids))
        .order_by(ProjectRow.name)
    ).all()
    out: dict[str, list[dict]] = defaultdict(list)
    for channel_id, project_id, name in rows:
        out[channel_id].append({"id": project_id, "name": name})
    return dict(out)


def _titles(s, id_col, title_col, ids) -> dict[str, str]:
    keys = [i for i in {x for x in ids if x}]
    if not keys:
        return {}
    result = s.execute(select(id_col, title_col).where(id_col.in_(keys))).all()
    return {k: t for k, t in result}


def video_titles(s: Session, video_ids: list[str]) -> dict[str, str]:
    """Competitor-video titles for a page of niche events (one batched query), so
    a `outlier_crossed` card can name the video its payload only referenced by id."""
    return _titles(s, CompetitorVideoRow.video_id, CompetitorVideoRow.title, video_ids)


def video_thumbnails(s: Session, video_ids: list[str]) -> dict[str, str]:
    """Competitor-video thumbnail urls for a page of niche events (one batched
    query), so a Radar card can show the video's packaging. Empty urls are
    dropped so the card falls back to its placeholder rather than a broken image."""
    got = _titles(s, CompetitorVideoRow.video_id, CompetitorVideoRow.thumbnail_url, video_ids)
    return {k: v for k, v in got.items() if v}


def channel_titles(s: Session, channel_ids: list[str]) -> dict[str, str]:
    """Competitor-channel titles for a page of events (one batched query)."""
    return _titles(s, CompetitorChannelRow.channel_id, CompetitorChannelRow.title, channel_ids)


def live_titles(s: Session, live_ids: list[str]) -> dict[str, str]:
    """Live titles for the ops alerts (one batched query) — a `pipeline_failed`
    card names the live, which lives in `videos`, not `competitor_videos`."""
    return _titles(s, VideoRow.id, VideoRow.title, live_ids)


# ------------------------- feed reads (bounded, index-backed) -------------------------


def list_niche_events(
    s: Session,
    *,
    project_id: int | None = None,
    since: datetime | None = None,
    limit: int = 50,
) -> list[EventRow]:
    """The Radar feed: niche events, newest first, capped at `limit`.

    `since` is a lower bound (events strictly after it — "what happened since
    yesterday"). `project_id` scopes to the channels of that project; an unknown
    or empty project yields no events rather than the whole feed."""
    stmt = select(EventRow).where(EventRow.category == CATEGORY_NICHE)
    if since is not None:
        stmt = stmt.where(EventRow.occurred_at > since)
    if project_id is not None:
        cids = channel_ids_in_project(s, project_id)
        if not cids:
            return []
        stmt = stmt.where(EventRow.channel_id.in_(cids))
    stmt = stmt.order_by(EventRow.occurred_at.desc(), EventRow.id.desc()).limit(limit)
    return list(s.execute(stmt).scalars())


def list_ops_alerts(s: Session, *, limit: int = 50) -> list[EventRow]:
    """Open operational failures: category='ops' AND resolved_at IS NULL."""
    return list(
        s.execute(
            select(EventRow)
            .where(EventRow.category == CATEGORY_OPS)
            .where(EventRow.resolved_at.is_(None))
            .order_by(EventRow.occurred_at.desc(), EventRow.id.desc())
            .limit(limit)
        ).scalars()
    )


def list_activity(
    s: Session, *, limit: int = 50, before_id: int | None = None
) -> list[EventRow]:
    """The full log, paginated by descending id (a stable cursor even when two
    events share an occurred_at). Pass the last id of a page as `before_id`."""
    stmt = select(EventRow)
    if before_id is not None:
        stmt = stmt.where(EventRow.id < before_id)
    return list(
        s.execute(stmt.order_by(EventRow.id.desc()).limit(limit)).scalars()
    )


CYCLE_DONE = "cycle_done"


def quota_units_used(s: Session, *, since: datetime | None = None) -> int:
    """Total YouTube quota units accounted across the `cycle_done` events (the
    activity log's quota accounting). Summed in Python — cycle events are a few
    per day, so this stays cheap; 0 when nothing has been recorded yet."""
    stmt = select(EventRow.payload).where(EventRow.type == CYCLE_DONE)
    if since is not None:
        stmt = stmt.where(EventRow.occurred_at > since)
    return sum(int((p or {}).get("quota_units", 0)) for p in s.execute(stmt).scalars())


# ------------------------- seen / resolved state -------------------------


def mark_seen(
    s: Session,
    *,
    now: datetime,
    ids: list[int] | None = None,
    until: datetime | None = None,
) -> int:
    """Mark niche events seen — by id set and/or up to a timestamp. Ops events are
    never touched (they resolve via resolved_at, not by being looked at). Returns
    how many rows were newly marked."""
    stmt = (
        update(EventRow)
        .where(EventRow.category == CATEGORY_NICHE)
        .where(EventRow.seen_at.is_(None))
    )
    conds = []
    if ids:
        conds.append(EventRow.id.in_(ids))
    if until is not None:
        conds.append(EventRow.occurred_at <= until)
    if not conds:
        return 0
    stmt = stmt.where(or_(*conds)).values(seen_at=now)
    result = s.execute(stmt)
    return int(result.rowcount or 0)


def resolve_ops(s: Session, *, now: datetime, ids: list[int]) -> int:
    """Mark ops events resolved (resolved_at). Returns how many were newly closed."""
    if not ids:
        return 0
    result = s.execute(
        update(EventRow)
        .where(EventRow.category == CATEGORY_OPS)
        .where(EventRow.resolved_at.is_(None))
        .where(EventRow.id.in_(ids))
        .values(resolved_at=now)
    )
    return int(result.rowcount or 0)


# ------------------------- consumer support (comment collection) -------------------------


def video_ids_with_outlier_event(
    s: Session, channel_ids: list[str]
) -> list[str]:
    """Distinct videos of the given channels that have ever crossed into a band
    (an `outlier_crossed` event). This is the event-sourced replacement for the
    old score re-derivation: comment collection reads THIS instead of rescanning
    snapshots, so `outlier_crossed` emission is the single detection point."""
    if not channel_ids:
        return []
    return list(
        s.execute(
            select(EventRow.video_id)
            .where(EventRow.type == "outlier_crossed")
            .where(EventRow.channel_id.in_(channel_ids))
            .where(EventRow.video_id.is_not(None))
            .distinct()
        ).scalars()
    )
