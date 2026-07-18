"""Automatic collection of an outlier's top comments (deterministic, no LLM).

Runs inside the snapshot cycle. Comments cost YouTube quota but no LLM call, so
guideline 9 puts them on the schedule — only the synthesis over them is manual.

**A consumer of `outlier_crossed`, not its own detector.** Crossing detection lives
in ONE place now — the event emission step (`worker/event_emission.py`) that runs
just before this. This step no longer re-scores anything: it reads the videos that
have an `outlier_crossed` event and collects the ones still owed comments. One
source of truth removes the old risk of the two detectors disagreeing (collecting
twice, or never).

Collect-once by design: an outlier's comment set is fetched a single time (the
`comments_state` terminal guard below stops a re-cross up the band ladder from
re-collecting) and only an explicit regeneration fetches it again. An `"error"` is
NOT terminal, so the video stays in the due set and is retried on a later cycle —
even though no new event fires for it. Resilience (DEVELOPMENT.md §4): the
selection and every video are isolated — a disabled or failing video is recorded
and the cycle carries on.
"""

from __future__ import annotations

from datetime import datetime

from ..config import Settings
from ..logging_conf import get_logger
from ..youtube.client import CommentsDisabledError, YouTubeClient

logger = get_logger(__name__)

STATE_DONE = "done"
STATE_DISABLED = "disabled"
STATE_ERROR = "error"

# States that stop the cycle asking again. "error" is absent on purpose: a failed
# fetch is worth one more try next cycle, a disabled thread never is.
TERMINAL_STATES = (STATE_DONE, STATE_DISABLED)


def collect_outlier_comments(
    channel_ids,
    *,
    settings: Settings,
    database,
    client: YouTubeClient,
    now: datetime,
) -> int:
    """Collect the top comments of every definitive outlier that still needs them.

    Returns how many videos were collected. Never raises: a failure here must not
    cost the cycle its snapshots.
    """
    from ..db import competitors as crepo
    from ..db import events as erepo

    try:
        with database.session() as s:
            due: list[str] = []
            for video_id in erepo.video_ids_with_outlier_event(s, list(channel_ids)):
                video = crepo.get_video(s, video_id)
                if video is not None and video.comments_state not in TERMINAL_STATES:
                    due.append(video_id)
    except Exception as exc:  # never let selection break the collection cycle
        logger.error("comment collection: could not select outliers: %s", exc)
        return 0

    collected = 0
    for video_id in due:
        try:
            n = collect_video_comments(
                video_id, settings=settings, database=database, client=client, now=now
            )
            collected += 1
            logger.info("comments: collected %d for outlier %s", n, video_id)
        except CommentsDisabledError as exc:
            logger.info("comments: disabled on %s", video_id)
            _record_state(database, video_id, STATE_DISABLED, str(exc))
        except Exception as exc:  # per-video isolation: log, record, keep going
            logger.error("comments: video %s failed: %s", video_id, exc)
            _record_state(database, video_id, STATE_ERROR, str(exc))

    return collected


def collect_video_comments(
    video_id: str,
    *,
    settings: Settings,
    database,
    client: YouTubeClient | None = None,
    now: datetime,
) -> int:
    """Fetch and store one video's top comments. Returns how many were stored.

    Also used by an explicit regeneration, which is why it is public and takes the
    client: the fetch is the same, only the trigger differs.
    """
    from ..db import competitors as crepo

    if client is None:
        client = YouTubeClient.from_settings(settings)

    # Network read, deliberately outside the write transaction.
    comments = client.list_top_comments(video_id, pages=settings.youtube_comment_pages)

    with database.session() as s:
        stored = crepo.replace_video_comments(s, video_id, comments, fetched_at=now)
        crepo.set_video_comments_state(
            s, video_id, state=STATE_DONE, error="", fetched_at=now
        )
    return stored


def _record_state(database, video_id: str, state: str, message: str) -> None:
    from ..db import competitors as crepo

    try:
        with database.session() as s:
            crepo.set_video_comments_state(s, video_id, state=state, error=message)
    except Exception as exc:  # a video we cannot even flag must not abort the cycle
        logger.error("comments: could not record the state of %s: %s", video_id, exc)
