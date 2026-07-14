"""RQ job: one competitor-snapshot collection cycle.

A single reusable function drives both cadences (see `worker/scheduler.py`):
- scope="all"    → snapshot every video of every active channel (daily);
- scope="recent" → snapshot only videos younger than `youtube_recent_days` (6h).

Each cycle also detects newly published videos (top of the uploads playlist) and
packaging changes (title string; thumbnail by BYTE hash — the URL is not trusted)
against the last-known state. Everything is deterministic and quota-accounted.

Resilience (PRIMOCUT.md guideline 12): channels are processed in isolation — a
failure on one is logged and the cycle moves on to the next; it never aborts the
whole cycle nor lets an exception crash the scheduler. Orphan channels
(active=false) are excluded, so re-linking a channel resumes its collection.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Callable

from ..config import Settings, get_settings
from ..logging_conf import get_logger
from ..youtube.client import YouTubeClient
from ..youtube.thumbnails import fetch_thumbnail_bytes, thumbnail_hash

logger = get_logger(__name__)

VALID_SCOPES = ("all", "recent")


def snapshot_cycle(
    scope: str = "all",
    *,
    channel_id: str | None = None,
    settings: Settings | None = None,
    database=None,
    client: YouTubeClient | None = None,
    thumbnail_fetcher: Callable[[str], bytes] = fetch_thumbnail_bytes,
    now: datetime | None = None,
) -> dict:
    """Run one collection cycle. `channel_id` limits it to a single (active)
    channel — used by the manual CLI one-shot."""
    if scope not in VALID_SCOPES:
        raise ValueError(f"scope must be one of {VALID_SCOPES}, got {scope!r}")
    settings = settings or get_settings()
    if database is None:
        from ..db import get_database

        database = get_database(settings)
    if client is None:
        client = YouTubeClient.from_settings(settings)
    now = now or datetime.now(timezone.utc)
    since = now - timedelta(days=settings.youtube_recent_days) if scope == "recent" else None

    from ..db import competitors as crepo

    with database.session() as s:
        channels = crepo.list_active_channels(s)
        if channel_id is not None:
            channels = [c for c in channels if c.channel_id == channel_id]
        targets = [(c.channel_id, c.uploads_playlist_id) for c in channels]

    channels_done = 0
    videos_done = 0
    packaging_changes = 0
    for cid, uploads_playlist in targets:
        try:
            n_videos, n_changes = _snapshot_channel(
                cid,
                uploads_playlist,
                scope=scope,
                since=since,
                now=now,
                settings=settings,
                database=database,
                client=client,
                thumbnail_fetcher=thumbnail_fetcher,
            )
            channels_done += 1
            videos_done += n_videos
            packaging_changes += n_changes
        except Exception as exc:  # per-channel isolation: log and keep going
            logger.error("snapshot(%s): channel %s failed: %s", scope, cid, exc)
            continue

    # Persistence step (mandated by the data model): refresh the syntactic title
    # attributes for the processed channels and fill packaging impact for events
    # whose window has elapsed. Content-hash gated + idempotent; isolated so a
    # failure here never affects the collected snapshots.
    _persist_metrics_artifacts(
        [cid for cid, _ in targets], settings=settings, database=database, now=now
    )

    logger.info(
        "snapshot(%s) done: %d channel(s), %d video(s), %d packaging change(s), "
        "%d quota unit(s)",
        scope,
        channels_done,
        videos_done,
        packaging_changes,
        client.units_used,
    )
    return {
        "scope": scope,
        "channels": channels_done,
        "videos": videos_done,
        "packaging_changes": packaging_changes,
        "quota_units": client.units_used,
    }


def _persist_metrics_artifacts(channel_ids, *, settings, database, now) -> None:
    """Refresh syntactic title attributes + backfill packaging impact for the
    processed channels. Deterministic, no network; isolated from the collection."""
    from ..db import competitors as crepo
    from ..metrics.attributes import refresh_syntactic_attributes
    from ..metrics.impact import backfill_packaging_impact

    try:
        with database.session() as s:
            video_ids: list[str] = []
            for cid in channel_ids:
                video_ids.extend(crepo.list_channel_video_ids(s, cid))
            attrs = refresh_syntactic_attributes(s, video_ids)
            filled = backfill_packaging_impact(s, now=now, settings=settings)
        logger.info(
            "metrics artifacts: %d attribute row(s), %d packaging impact(s)", attrs, filled
        )
    except Exception as exc:  # never let persistence break the collection cycle
        logger.error("metrics artifact persistence failed: %s", exc)


def _snapshot_channel(
    channel_id: str,
    uploads_playlist: str,
    *,
    scope: str,
    since: datetime | None,
    now: datetime,
    settings: Settings,
    database,
    client: YouTubeClient,
    thumbnail_fetcher: Callable[[str], bytes],
) -> tuple[int, int]:
    """Collect one channel. Returns (videos_snapshotted, packaging_changes)."""
    from ..db import competitors as crepo

    # -- 1. network reads (kept outside the DB transaction) --
    stats = client.get_channels_stats([channel_id])
    top_ids = client.list_uploads_video_ids(
        uploads_playlist, limit=settings.youtube_uploads_scan_limit
    )

    with database.session() as s:
        existing_ids = set(crepo.list_channel_video_ids(s, channel_id))
        scoped_ids = crepo.list_channel_video_ids(s, channel_id, since=since)
        stored = {
            v.video_id: (v.title, v.thumbnail_hash)
            for v in crepo.list_channel_videos(s, channel_id)
        }

    new_ids = [vid for vid in top_ids if vid not in existing_ids]
    # dedup while preserving order: scoped videos first, then newly detected ones
    target_ids = list(dict.fromkeys(list(scoped_ids) + new_ids))
    videos = client.list_videos(target_ids) if target_ids else []
    # thumbnail bytes are fetched (network) before opening the write transaction
    hashes = {v.video_id: thumbnail_hash(v.thumbnail_url, fetcher=thumbnail_fetcher) for v in videos}

    short_max = settings.youtube_short_max_seconds
    changes = 0

    # -- 2. single write transaction for this channel --
    with database.session() as s:
        if stats:
            st = stats[0]
            crepo.add_channel_snapshot(
                s,
                channel_id=channel_id,
                captured_at=now,
                subscribers=st.subscribers,
                total_views=st.total_views,
                video_count=st.video_count,
            )

        for v in videos:
            is_new = v.video_id not in existing_ids
            if is_new:
                crepo.upsert_video(
                    s,
                    video_id=v.video_id,
                    channel_id=channel_id,
                    title=v.title,
                    published_at=v.published_at,
                    duration_s=v.duration_s,
                    is_short=v.duration_s <= short_max,
                    thumbnail_url=v.thumbnail_url,
                    thumbnail_hash=hashes[v.video_id],
                )
            else:
                changes += _detect_packaging(
                    s, v, hashes[v.video_id], stored.get(v.video_id, ("", "")), now
                )
            crepo.add_video_snapshot(
                s,
                video_id=v.video_id,
                captured_at=now,
                views=v.view_count,
                likes=v.like_count,
                comments=v.comment_count,
            )

    return len(videos), changes


def _detect_packaging(s, video, new_hash: str, stored: tuple[str, str], now: datetime) -> int:
    """Compare title + thumbnail hash against the last-known state; record any
    change and update the stored state. Returns the number of changes recorded."""
    from ..db import competitors as crepo

    old_title, old_hash = stored
    changes = 0

    if old_title and video.title and old_title != video.title:
        crepo.add_packaging_change(
            s, video_id=video.video_id, field="title",
            old_value=old_title, new_value=video.title, detected_at=now,
        )
        crepo.update_video_packaging(s, video.video_id, title=video.title)
        changes += 1

    if old_hash and new_hash and old_hash != new_hash:
        # Same URL can serve different bytes — the hash is the source of truth.
        crepo.add_packaging_change(
            s, video_id=video.video_id, field="thumbnail",
            old_value=old_hash, new_value=new_hash, detected_at=now,
        )
        crepo.update_video_packaging(
            s, video.video_id, thumbnail_url=video.thumbnail_url, thumbnail_hash=new_hash,
        )
        changes += 1
    elif not old_hash and new_hash:  # first successful hash — fill silently
        crepo.update_video_packaging(
            s, video.video_id, thumbnail_url=video.thumbnail_url, thumbnail_hash=new_hash,
        )

    return changes
