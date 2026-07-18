"""Emit the niche events that the Radar feed reads, from the snapshot cycle.

The one non-trivial producer is `outlier_crossed`, whose anti-flapping guard is
the whole point: an outlier score is computed on read and does not survive a
recompute, so the moment a video crosses into a band has to be recorded as a fact
or it is lost. But a video oscillating around the p90 cut would record that fact
every snapshot, so emission fires ONLY on a transition to a strictly higher band
than the one last emitted (persisted in `competitor_videos.last_band_emitted`):

- a video crossing p90 → p97 → p99 emits three events, one per rung of the ladder;
- a video flickering across the p90 cut emits exactly one.

A provisional (< 7 days) crossing is emitted with `provisional=true` in the
payload; because the guard tracks the highest band emitted, a provisional
`relevant` that later matures still-`relevant` does NOT re-emit — only a genuine
step up does. Comment collection consumes these events (single detection point).

Isolation (guideline 12): a channel that fails to score is logged and skipped; a
metrics/emit error never costs the cycle its snapshots.
"""

from __future__ import annotations

from datetime import datetime

from ..config import Settings
from ..db import competitors as crepo
from ..db import events as erepo
from ..logging_conf import get_logger
from ..metrics import core, service

logger = get_logger(__name__)

# "" (never emitted) ranks below every band, so the first crossing always fires.
_ORDINAL = {"": -1, **{name: i for i, name in enumerate(core.BAND_NAMES)}}


def _ordinal(band: str | None) -> int:
    return _ORDINAL.get(band or "", -1)


def emit_outlier_events(
    channel_ids, *, settings: Settings, database, now: datetime
) -> int:
    """Emit `outlier_crossed` for every video of the given channels that stepped
    up a band since its last emission. Returns how many events were emitted."""
    emitted = 0
    for channel_id in channel_ids:
        try:
            emitted += _emit_channel(channel_id, settings=settings, database=database, now=now)
        except Exception as exc:  # per-channel isolation: log and keep going
            logger.error("outlier emission: channel %s failed: %s", channel_id, exc)
            continue
    return emitted


def _emit_channel(channel_id: str, *, settings: Settings, database, now: datetime) -> int:
    emitted = 0
    with database.session() as s:
        scored, _ = service._channel_scores(
            s, channel_id, settings=settings, now=service._now(now)
        )
        for sc in scored.values():
            band = sc.score.band
            if band is None:
                continue
            video = crepo.get_video(s, sc.video_id)
            if video is None or _ordinal(band) <= _ordinal(video.last_band_emitted):
                continue
            erepo.add_event(
                s,
                category=erepo.CATEGORY_NICHE,
                type="outlier_crossed",
                severity=erepo.SEVERITY_INFO,
                channel_id=channel_id,
                video_id=sc.video_id,
                occurred_at=now,
                payload={
                    "band": band,
                    "median_multiple": sc.score.median_multiple,
                    "views": sc.views,
                    "baseline": sc.score.baseline,
                    "provisional": sc.score.provisional,
                },
            )
            video.last_band_emitted = band
            emitted += 1
    return emitted
