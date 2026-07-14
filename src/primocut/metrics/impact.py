"""Packaging-impact backfill: fill `packaging_changes.vph_before/vph_after`.

This is the one derived figure the data model asks us to persist. It is computed
from the video's snapshot deltas (never totals) and written back only once the
"after" window has fully elapsed, so the operation is idempotent and safe to run
repeatedly (from the snapshot cycle or `primocut metrics refresh`).
"""

from __future__ import annotations

from datetime import datetime, timezone

from ..config import Settings, get_settings
from ..db import competitors as crepo
from ..logging_conf import get_logger
from .core import Snap, packaging_impact

logger = get_logger(__name__)


def _snaps(session, video_id: str) -> list[Snap]:
    return [
        Snap(captured_at=r.captured_at, views=r.views)
        for r in crepo.list_video_snapshots(session, video_id)
    ]


def backfill_packaging_impact(
    session, *, now: datetime | None = None, settings: Settings | None = None
) -> int:
    """Fill vph_before/vph_after for events whose window has elapsed and that are
    still pending. Returns the number of events filled."""
    settings = settings or get_settings()
    now = now or datetime.now(timezone.utc)
    # SQLite drops tzinfo on round-trip; compare against a naive UTC `now`.
    if now.tzinfo is not None:
        now = now.replace(tzinfo=None)
    window_days = settings.packaging_impact_window_days

    filled = 0
    for change in crepo.list_pending_packaging_changes(session):
        impact = packaging_impact(
            _snaps(session, change.video_id),
            detected_at=change.detected_at,
            now=now,
            window_days=window_days,
        )
        if impact.vph_after is None:
            continue  # window not elapsed yet — leave it pending
        crepo.set_packaging_impact(
            session, change.id, vph_before=impact.vph_before, vph_after=impact.vph_after
        )
        filled += 1

    if filled:
        logger.info("packaging impact: filled %d event(s)", filled)
    return filled
