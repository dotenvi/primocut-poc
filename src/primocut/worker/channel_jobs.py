"""RQ job: backfill the recent uploads of a channel that is new to the system.

Runs only for a channel seen for the first time (global dedup): it lists the
last N uploads, fetches their metadata in batches (quota-accounted) and stores
them in `competitor_videos` with an initial `is_short` and `thumbnail_hash`.

Resilience (DEVELOPMENT.md §4): any API/thumbnail failure degrades — the channel
is marked `backfill_state="error"` with the message logged, and the worker is
never crashed. Progress is written incrementally so the existing polling can
follow it.
"""

from __future__ import annotations

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
) -> dict:
    """Collect the last `limit` uploads of `channel_id` into competitor_videos."""
    settings = settings or get_settings()
    if database is None:
        from ..db import get_database

        database = get_database(settings)
    if client is None:
        client = YouTubeClient.from_settings(settings)
    limit = limit if limit is not None else settings.youtube_backfill_limit

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
                done += 1
                crepo.set_backfill(s, channel_id, backfill_done=done)

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
