"""Enqueueing of videos onto the RQ queue."""

from __future__ import annotations

from ..config import Settings, get_settings
from ..download.audio import extract_video_id, validate_youtube_url
from ..logging_conf import get_logger
from ..models import Video, VideoStatus
from .jobs import process_video

logger = get_logger(__name__)


def get_queue(settings: Settings | None = None):
    from redis import Redis
    from rq import Queue

    settings = settings or get_settings()
    return Queue(settings.rq_queue, connection=Redis.from_url(settings.redis_url))


def enqueue_enrichment(
    video_ids: list[str],
    kind: str,
    *,
    regenerate: bool = False,
    settings: Settings | None = None,
    queue=None,
) -> dict:
    """Enqueue the opt-in AI enrichment of a set of videos (guideline 9).

    The request is validated here so a caller mistake surfaces as a rejected
    request rather than as a job that fails once per video.
    """
    from ..metrics.enrichment import validate_kind

    settings = settings or get_settings()
    validate_kind(kind, regenerate=regenerate)  # raises ValueError on a bad request

    from .enrich_jobs import enrich_videos

    q = queue if queue is not None else get_queue(settings)
    rq_job = q.enqueue(
        enrich_videos,
        list(video_ids),
        kind,
        regenerate=regenerate,
        job_timeout=settings.rq_job_timeout,
    )
    logger.info("Enqueued %s enrichment of %d video(s) (rq %s)",
                kind, len(video_ids), rq_job.id)
    return {"kind": kind, "videos": len(video_ids), "rq_job_id": rq_job.id}


def enqueue_video(
    url: str,
    *,
    settings: Settings | None = None,
    database=None,
    queue=None,
) -> dict:
    """Validate the URL, (re)create the Video (queued) + Job and enqueue processing."""
    settings = settings or get_settings()
    if database is None:
        from ..db import get_database

        database = get_database(settings)

    from ..db import repo

    url = validate_youtube_url(url)
    video_id = extract_video_id(url)

    with database.session() as s:
        existing = repo.get_video(s, video_id)
        repo.upsert_video(s, Video(
            id=video_id, url=url,
            title=existing.title if existing else "",
            channel=existing.channel if existing else "",
            duration=existing.duration if existing else 0.0,
            status=VideoStatus.QUEUED,
        ))

    return _enqueue_job(database, queue, video_id, settings)


def reenqueue_video(
    video_id: str,
    *,
    settings: Settings | None = None,
    database=None,
    queue=None,
) -> dict:
    """Reprocess an existing video (without re-validating the URL; resets status→queued).

    Idempotent: `process_video` skips stages whose artifact is already in the database (cache).
    """
    settings = settings or get_settings()
    if database is None:
        from ..db import get_database

        database = get_database(settings)

    from ..db import repo

    with database.session() as s:
        if repo.get_video(s, video_id) is None:
            raise KeyError(video_id)
        repo.set_video_status(s, video_id, VideoStatus.QUEUED)

    return _enqueue_job(database, queue, video_id, settings)


def generate_topics(
    video_id: str,
    *,
    settings: Settings | None = None,
    database=None,
    queue=None,
) -> dict:
    """Enqueue a topics-only run (LLM) for an existing video.

    Clears any previously cached topics so the analysis always (re)runs, then
    enqueues `process_video` with `create_topics=True`. Everything else
    (audio/transcription/chat) is served from the cache, so only the LLM step
    actually executes.
    """
    settings = settings or get_settings()
    if database is None:
        from ..db import get_database

        database = get_database(settings)

    from ..db import repo

    with database.session() as s:
        if repo.get_video(s, video_id) is None:
            raise KeyError(video_id)
        repo.save_topics(s, video_id, [])  # drop cache → force (re)generation
        repo.set_video_status(s, video_id, VideoStatus.QUEUED)

    return _enqueue_job(database, queue, video_id, settings, create_topics=True)


class VideoBusyError(RuntimeError):
    """Attempt to delete a video that is still being actively processed."""


def _default_stopper(rq_job_id: str | None, settings: Settings, connection=None) -> None:
    """Cancel the RQ job: `cancel` if queued, SIGKILL the horse if running."""
    if not rq_job_id:
        return
    from redis import Redis
    from rq.job import Job

    conn = connection or Redis.from_url(settings.redis_url)
    try:
        job = Job.fetch(rq_job_id, connection=conn)
    except Exception:
        return  # job no longer exists
    try:
        status = job.get_status(refresh=True)
        if status == "started":
            from rq.command import send_stop_job_command

            send_stop_job_command(conn, rq_job_id)
        else:
            job.cancel()
    except Exception as exc:  # best-effort
        logger.warning("Failed to stop job %s: %s", rq_job_id, exc)


def stop_video(
    video_id: str,
    *,
    settings: Settings | None = None,
    database=None,
    connection=None,
    stopper=None,
) -> dict:
    """Interrupt a video's processing (cancel the job) and mark it `stopped`."""
    settings = settings or get_settings()
    if database is None:
        from ..db import get_database

        database = get_database(settings)

    from ..db import repo

    with database.session() as s:
        if repo.get_video(s, video_id) is None:
            raise KeyError(video_id)
        job = repo.latest_job(s, video_id)
        job_id = job.id if job else None
        rq_id = job.rq_job_id if job else None

    (stopper or _default_stopper)(rq_id, settings, connection)

    with database.session() as s:
        if job_id is not None:
            repo.append_stage(s, job_id, "__end__")  # close the interrupted stage
            repo.update_job(s, job_id, status="stopped")
        repo.set_video_status(s, video_id, VideoStatus.STOPPED)

    logger.info("Video %s interrupted.", video_id)
    return {"video_id": video_id, "status": VideoStatus.STOPPED.value}


def delete_video(
    video_id: str,
    *,
    settings: Settings | None = None,
    database=None,
    connection=None,
    stopper=None,
    force: bool = False,
) -> dict:
    """Delete EVERYTHING about the video from the database. Refuses if active (stop it first).

    There are no per-video files to remove: the audio lives in a throwaway temp
    dir (already gone) and transcripts/topics/chat are database rows.
    """
    settings = settings or get_settings()
    if database is None:
        from ..db import get_database

        database = get_database(settings)

    from ..db import repo
    from ..models import ACTIVE_STATUSES

    with database.session() as s:
        v = repo.get_video(s, video_id)
        if v is None:
            raise KeyError(video_id)
        status = v.status
        job = repo.latest_job(s, video_id)
        rq_id = job.rq_job_id if job else None

    if status in ACTIVE_STATUSES and not force:
        raise VideoBusyError(video_id)

    (stopper or _default_stopper)(rq_id, settings, connection)  # make sure it no longer runs

    with database.session() as s:
        repo.delete_video(s, video_id)

    logger.info("Video %s deleted.", video_id)
    return {"video_id": video_id}


def _enqueue_job(
    database, queue, video_id: str, settings: Settings, *, create_topics: bool = False
) -> dict:
    from ..db import repo

    with database.session() as s:
        job_id = repo.create_job(s, video_id).id

    q = queue if queue is not None else get_queue(settings)
    rq_job = q.enqueue(
        process_video, video_id, create_topics=create_topics,
        job_timeout=settings.rq_job_timeout,
    )

    with database.session() as s:
        repo.update_job(s, job_id, rq_job_id=rq_job.id)

    logger.info("Enqueued %s (job %s, rq %s)", video_id, job_id, rq_job.id)
    return {"video_id": video_id, "job_id": job_id, "rq_job_id": rq_job.id}
