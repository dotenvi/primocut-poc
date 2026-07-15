"""Orchestration for registering / detaching competitor channels within a
project. Shared by the API and the CLI.

Registration resolves the reference with a single fast API call (in-request),
then decides:
- channel NEW to the system  → insert + link + enqueue the async backfill job;
- channel ALREADY known       → only link (global dedup, no new collection),
                                reactivating it if it was an orphan — unless its
                                backfill never completed, which re-enqueues it.

An unreachable queue raises `QueueUnavailableError` and leaves the channel in
`backfill_state="error"`: the row is committed before the enqueue, so the
failure has to be recorded on it rather than left as a collection that silently
never starts.

Detaching removes the link and deactivates the channel if it became orphaned;
all channel/video data is preserved.
"""

from __future__ import annotations

from ..config import Settings, get_settings
from ..logging_conf import get_logger
from ..youtube.client import YouTubeClient
from ..youtube.resolve import parse_channel_reference
from .channel_jobs import backfill_channel
from .queue import QueueUnavailableError, get_queue

logger = get_logger(__name__)

# Backfill states of a channel that holds no collection worth deduping against:
# "pending" never reached the queue, "error" never finished. Re-registering such
# a channel collects it instead of treating it as already known.
_NEVER_COLLECTED = ("pending", "error")


def register_channel(
    project_id: int,
    reference: str,
    *,
    settings: Settings | None = None,
    database=None,
    queue=None,
    client: YouTubeClient | None = None,
) -> dict:
    """Register (resolve + link) a channel in a project. Enqueues a backfill only
    when the channel is new to the system. Raises KeyError if the project is
    missing; YouTubeError/ValueError propagate from resolution."""
    settings = settings or get_settings()
    if database is None:
        from ..db import get_database

        database = get_database(settings)
    if client is None:
        client = YouTubeClient.from_settings(settings)

    info = client.get_channel(**parse_channel_reference(reference))

    from ..db import competitors as crepo

    with database.session() as s:
        if crepo.get_project(s, project_id) is None:
            raise KeyError(project_id)
        existing = crepo.get_channel(s, info.channel_id)
        is_new = existing is None
        if is_new:
            crepo.upsert_channel(
                s,
                channel_id=info.channel_id,
                handle=info.handle,
                title=info.title,
                uploads_playlist_id=info.uploads_playlist_id,
                active=True,
                backfill_state="pending",
            )
            reactivated, retry = False, True
        else:
            reactivated = not existing.active
            if reactivated:
                existing.active = True
            # Dedup skips re-collection, but only a channel that actually
            # collected has anything to dedup against: one whose backfill never
            # ran would be stranded for good, since registering is the only path
            # that enqueues it.
            retry = existing.backfill_state in _NEVER_COLLECTED
        linked = crepo.link_channel(s, project_id, info.channel_id)

    result = {
        "channel_id": info.channel_id,
        "title": info.title,
        "existing": not is_new,
        "linked": linked,
        "reactivated": reactivated,
    }
    if not retry:
        logger.info(
            "Linked existing channel %s to project %s (no backfill).",
            info.channel_id,
            project_id,
        )
        return {**result, "backfill": "skipped"}

    enq = _enqueue_backfill(database, queue, info.channel_id, settings)
    return {**result, "backfill": "queued", **enq}


def detach_channel(
    project_id: int,
    channel_id: str,
    *,
    settings: Settings | None = None,
    database=None,
) -> dict:
    """Detach a channel from a project, deactivating it if orphaned. Keeps all
    data. Raises KeyError if the project is missing."""
    settings = settings or get_settings()
    if database is None:
        from ..db import get_database

        database = get_database(settings)

    from ..db import competitors as crepo

    with database.session() as s:
        if crepo.get_project(s, project_id) is None:
            raise KeyError(project_id)
        detached = crepo.unlink_channel(s, project_id, channel_id)
        deactivated = crepo.deactivate_if_orphan(s, channel_id)

    logger.info(
        "Detached channel %s from project %s (deactivated=%s).",
        channel_id,
        project_id,
        deactivated,
    )
    return {
        "channel_id": channel_id,
        "detached": detached,
        "deactivated": deactivated,
    }


def _enqueue_backfill(database, queue, channel_id: str, settings: Settings) -> dict:
    """Enqueue the backfill and record the job on the channel.

    The channel row is already committed by the time we get here, so a broker
    failure would otherwise strand it in its initial `pending` forever: nothing
    retries it and only this job ever moves the state. Record the failure on the
    channel instead, and let the caller answer for the failed request.
    """
    from ..db import competitors as crepo

    try:
        q = queue if queue is not None else get_queue(settings)
        rq_job = q.enqueue(
            backfill_channel, channel_id, job_timeout=settings.rq_job_timeout
        )
    except Exception as exc:
        logger.error("Could not enqueue the backfill for %s: %s", channel_id, exc)
        with database.session() as s:
            crepo.set_backfill(
                s, channel_id, backfill_state="error", backfill_error=str(exc)
            )
        raise QueueUnavailableError(str(exc)) from exc

    with database.session() as s:
        crepo.set_backfill(
            s,
            channel_id,
            backfill_state="queued",
            backfill_error=None,
            backfill_rq_job_id=rq_job.id,
        )
    logger.info("Enqueued backfill for %s (rq %s)", channel_id, rq_job.id)
    return {"rq_job_id": rq_job.id}
