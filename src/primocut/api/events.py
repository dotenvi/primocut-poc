"""API router for the Radar event log: the niche feed, the ops alerts, the full
activity log and the seen-state write.

Factory-built like the other Phase 2 routers so it receives the app's dependency
callables without a circular import. Every read is bounded and index-backed; the
niche feed resolves each channel's project badges in ONE extra query, so a channel
in two projects yields one event carrying two badges — never a duplicate row.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from ..db import events as erepo


class SeenIn(BaseModel):
    # Mark a specific set, or everything up to a timestamp (or both). Ops events
    # are never marked seen — they resolve via resolved_at.
    ids: list[int] | None = None
    until: datetime | None = None


def _naive(ts: datetime | None) -> datetime | None:
    """SQLite drops tzinfo on round-trip, so every stored datetime is naive; a
    caller-supplied timestamp is normalised to naive UTC before it is compared."""
    if ts is None:
        return None
    return ts.astimezone(timezone.utc).replace(tzinfo=None) if ts.tzinfo else ts


def _event_dict(row, badges: dict[str, list[dict]]) -> dict[str, Any]:
    return {
        "id": row.id,
        "category": row.category,
        "type": row.type,
        "severity": row.severity,
        "project_id": row.project_id,
        "channel_id": row.channel_id,
        "video_id": row.video_id,
        "live_id": row.live_id,
        "occurred_at": row.occurred_at.isoformat(),
        "payload": row.payload,
        "seen_at": row.seen_at.isoformat() if row.seen_at else None,
        "resolved_at": row.resolved_at.isoformat() if row.resolved_at else None,
        # Project badges resolved on read (empty for a channel no project tracks).
        "projects": badges.get(row.channel_id, []),
    }


def _serialise(s, rows) -> list[dict]:
    badges = erepo.projects_for_channels(s, [r.channel_id for r in rows])
    return [_event_dict(r, badges) for r in rows]


def build_events_router(*, get_db: Callable) -> APIRouter:
    router = APIRouter(prefix="/api")

    @router.get("/events")
    def list_events(
        db=Depends(get_db),
        category: str = "niche",
        project_id: int | None = None,
        since: datetime | None = None,
        limit: int = Query(default=50, ge=1, le=200),
    ) -> list[dict]:
        """The Radar feed: niche events newest-first, with project badges."""
        if category != "niche":
            raise HTTPException(
                status_code=400, detail="only category=niche is served here; use /api/alerts or /api/activity"
            )
        with db.session() as s:
            rows = erepo.list_niche_events(
                s, project_id=project_id, since=_naive(since), limit=limit
            )
            return _serialise(s, rows)

    @router.get("/alerts")
    def list_alerts(
        db=Depends(get_db), limit: int = Query(default=50, ge=1, le=200)
    ) -> list[dict]:
        """Open operational failures (category='ops' AND resolved_at IS NULL)."""
        with db.session() as s:
            rows = erepo.list_ops_alerts(s, limit=limit)
            return _serialise(s, rows)

    @router.get("/activity")
    def list_activity(
        db=Depends(get_db),
        limit: int = Query(default=50, ge=1, le=200),
        before_id: int | None = None,
    ) -> dict:
        """The full paginated log plus the cycle quota accounting."""
        with db.session() as s:
            rows = erepo.list_activity(s, limit=limit, before_id=before_id)
            events = _serialise(s, rows)
            return {
                "events": events,
                "next_before_id": rows[-1].id if rows else None,
                "quota_units": erepo.quota_units_used(s),
            }

    @router.post("/events/seen")
    def mark_seen(body: SeenIn, db=Depends(get_db)) -> dict:
        """Mark niche events seen — by id set and/or up to a timestamp."""
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        with db.session() as s:
            marked = erepo.mark_seen(
                s, now=now, ids=body.ids, until=_naive(body.until)
            )
        return {"marked": marked}

    return router
