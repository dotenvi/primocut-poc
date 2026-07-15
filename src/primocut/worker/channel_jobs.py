"""RQ job: backfill the recent uploads of a channel that is new to the system.

Runs only for a channel seen for the first time (global dedup): it lists the
last N uploads, fetches their metadata in batches (quota-accounted) and stores
them in `competitor_videos` with an initial `is_short` and `thumbnail_hash`.

It also records the channel's **first snapshot**, so the dashboards have data as
soon as a channel is added instead of waiting for the first scheduled cycle (up
to 24h away). The video counters cost nothing extra — they ride along in the same
`videos.list` response the backfill already makes; only the channel statistics
add a call (1 unit). Every later snapshot comes from the cycle in
`worker/snapshot_jobs.py`, which remains the sole continuous collector.

Resilience (DEVELOPMENT.md §4): any API/thumbnail failure degrades — the channel
is marked `backfill_state="error"` with the message logged, and the worker is
never crashed. The channel snapshot is isolated on top of that: losing it never
costs the uploads, which are the point of the backfill. Progress is written
incrementally so the existing polling can follow it.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Callable

from ..config import Settings, get_settings
from ..logging_conf import get_logger
from ..youtube.client import YouTubeClient
from ..youtube.thumbnails import fetch_thumbnail_bytes, thumbnail_hash

logger = get_logger(__name__)


def backfill_channel(
    channel_id: str,
    *,
    limit: int | None = None,
    settings: Settings | None = None,
    database=None,
    client: YouTubeClient | None = None,
    thumbnail_fetcher: Callable[[str], bytes] = fetch_thumbnail_bytes,
    now: datetime | None = None,
) -> dict:
    """Collect the last `limit` uploads of `channel_id` into competitor_videos,
    plus the channel's first snapshot."""
    settings = settings or get_settings()
    if database is None:
        from ..db import get_database

        database = get_database(settings)
    if client is None:
        client = YouTubeClient.from_settings(settings)
    limit = limit if limit is not None else settings.youtube_backfill_limit
    # One reading, one instant: every row this job writes shares this timestamp.
    now = now or datetime.now(timezone.utc)

    from ..db import competitors as crepo

    with database.session() as s:
        channel = crepo.get_channel(s, channel_id)
        if channel is None:
            raise ValueError(f"Channel {channel_id!r} not found in the database.")
        uploads_playlist = channel.uploads_playlist_id
        crepo.set_backfill(
            s, channel_id, backfill_state="running", backfill_error=None
        )

    try:
        video_ids = client.list_uploads_video_ids(uploads_playlist, limit=limit)
        with database.session() as s:
            crepo.set_backfill(s, channel_id, backfill_total=len(video_ids))

        videos = client.list_videos(video_ids)
        short_max = settings.youtube_short_max_seconds
        done = 0
        for video in videos:
            digest = thumbnail_hash(video.thumbnail_url, fetcher=thumbnail_fetcher)
            with database.session() as s:
                crepo.upsert_video(
                    s,
                    video_id=video.video_id,
                    channel_id=channel_id,
                    title=video.title,
                    published_at=video.published_at,
                    duration_s=video.duration_s,
                    is_short=video.duration_s <= short_max,
                    thumbnail_url=video.thumbnail_url,
                    thumbnail_hash=digest,
                )
                crepo.add_video_snapshot(
                    s,
                    video_id=video.video_id,
                    captured_at=now,
                    views=video.view_count,
                    likes=video.like_count,
                    comments=video.comment_count,
                )
                done += 1
                crepo.set_backfill(s, channel_id, backfill_done=done)

        _record_channel_snapshot(
            channel_id, now=now, database=database, client=client
        )

        with database.session() as s:
            crepo.set_backfill(s, channel_id, backfill_state="done")
        logger.info(
            "Backfill %s done: %d video(s), %d quota unit(s)",
            channel_id,
            done,
            client.units_used,
        )
        return {"channel_id": channel_id, "videos": done, "quota_units": client.units_used}

    except Exception as exc:  # degrade: mark error, log, never crash the worker
        logger.error("Backfill failed for %s: %s", channel_id, exc)
        with database.session() as s:
            crepo.set_backfill(
                s, channel_id, backfill_state="error", backfill_error=str(exc)
            )
        return {
            "channel_id": channel_id,
            "error": str(exc),
            "quota_units": client.units_used,
        }


def _record_channel_snapshot(channel_id: str, *, now, database, client) -> None:
    """Record the channel's first statistics snapshot (subscribers/views/videos).

    Isolated from the collection above: this is the only part of the backfill that
    spends a call of its own, and the uploads must survive it failing.
    """
    from ..db import competitors as crepo

    try:
        stats = client.get_channels_stats([channel_id])
        if not stats:
            logger.warning("No statistics returned for %s; snapshot skipped", channel_id)
            return
        st = stats[0]
        with database.session() as s:
            crepo.add_channel_snapshot(
                s,
                channel_id=channel_id,
                captured_at=now,
                subscribers=st.subscribers,
                total_views=st.total_views,
                video_count=st.video_count,
            )
    except Exception as exc:  # never let the snapshot break the backfill
        logger.error("First channel snapshot failed for %s: %s", channel_id, exc)
