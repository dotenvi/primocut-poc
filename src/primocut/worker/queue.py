"""Enqueueing of videos onto the RQ queue."""

from __future__ import annotations

from pathlib import Path

from ..config import Settings, get_settings
from ..download.audio import extract_video_id, validate_youtube_url
from ..logging_conf import get_logger
from ..models import Video, VideoStatus
from .jobs import process_video

logger = get_logger(__name__)


class QueueUnavailableError(RuntimeError):
    """The queue broker could not be reached, so the job was not enqueued.

    `redis-py` connects lazily: building the `Queue` never fails, only the
    `enqueue` call does. Callers that already persisted state for the job must
    catch this and reconcile that state, or it outlives the failed request.
    """


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


def enqueue_render(
    video_id: str,
    cut_id: int,
    *,
    item_ids: list[int] | None = None,
    regenerate: bool = False,
    fast_copy: bool = False,
    settings: Settings | None = None,
    database=None,
    queue=None,
) -> dict:
    """Enqueue a BACKGROUND render of a cut's items (all, or the given subset).

    The worker owns the clip rows (per-item isolation + the pay-once cache): this
    only validates the cut, resolves the item list and enqueues one job. Rendering
    runs off the request thread so the user can keep triaging. Raises `KeyError`
    if the cut does not exist in the live (the route maps it to 404).
    """
    settings = settings or get_settings()
    if database is None:
        from ..db import get_database

        database = get_database(settings)

    from ..db import cuts as cuts_repo

    with database.session() as s:
        cut = cuts_repo.get_cut(s, video_id, cut_id)
        if cut is None:
            raise KeyError((video_id, cut_id))
        items = cuts_repo.list_items(s, cut_id)
        if item_ids is not None:
            wanted = set(item_ids)
            items = [it for it in items if it.id in wanted]
        ids = [it.id for it in items]

    if not ids:
        return {"cut_id": cut_id, "queued": 0, "rq_job_id": None}

    from .render_jobs import render_clips

    q = queue if queue is not None else get_queue(settings)
    rq_job = q.enqueue(
        render_clips, video_id, cut_id, ids,
        regenerate=regenerate, fast_copy=fast_copy,
        job_timeout=settings.rq_job_timeout,
    )
    logger.info("Enqueued render of %d clip(s) for cut %s (rq %s)",
                len(ids), cut_id, rq_job.id)
    return {"cut_id": cut_id, "queued": len(ids), "rq_job_id": rq_job.id}


def enqueue_export(
    video_id: str,
    cut_id: int,
    *,
    render_missing: bool = False,
    skip_master: bool = False,
    upscale_1440p: bool = False,
    settings: Settings | None = None,
    database=None,
    queue=None,
) -> dict:
    """Enqueue a BACKGROUND export of a cut into a deliverable bundle.

    Sets the export row to `queued` right away so the UI shows a live state, then
    enqueues the packaging job. Raises `KeyError` if the cut does not exist in the
    live (the route maps it to 404). The zip is always produced for the UI download.
    """
    settings = settings or get_settings()
    if database is None:
        from ..db import get_database

        database = get_database(settings)

    from ..db import cuts as cuts_repo
    from ..db import exports as exports_repo

    with database.session() as s:
        cut = cuts_repo.get_cut(s, video_id, cut_id)
        if cut is None:
            raise KeyError((video_id, cut_id))
        item_count = len(cuts_repo.list_items(s, cut_id))
        exports_repo.reset_to_queued(
            s, video_id=video_id, cut_id=cut_id, item_count=item_count,
            skip_master=skip_master, upscaled=upscale_1440p,
        )

    from .export_jobs import export_cut

    q = queue if queue is not None else get_queue(settings)
    rq_job = q.enqueue(
        export_cut, video_id, cut_id,
        render_missing=render_missing, skip_master=skip_master,
        upscale_1440p=upscale_1440p, make_zip=True,
        job_timeout=settings.rq_job_timeout,
    )
    logger.info("Enqueued export of cut %s (rq %s)", cut_id, rq_job.id)
    return {"cut_id": cut_id, "queued": item_count, "rq_job_id": rq_job.id}


def _cut_or_raise(database, video_id: str, cut_id: int) -> int:
    """Resolve the cut in the live (raises KeyError → the route maps it to 404)."""
    from ..db import cuts as cuts_repo

    with database.session() as s:
        cut = cuts_repo.get_cut(s, video_id, cut_id)
        if cut is None:
            raise KeyError((video_id, cut_id))
        return len(cuts_repo.list_items(s, cut_id))


def enqueue_packaging(
    video_id: str,
    cut_id: int,
    kind: str,
    *,
    item_ids: list[int] | None = None,
    k: int | None = None,
    regenerate: bool = False,
    settings: Settings | None = None,
    database=None,
    queue=None,
) -> dict:
    """Enqueue a BACKGROUND per-clip packaging generation (`title`/`thumb_brief`).

    Opt-in and costs an LLM call (guideline 9). Runs off the request thread so the
    user keeps triaging; each clip is polled via the bench."""
    settings = settings or get_settings()
    if database is None:
        from ..db import get_database

        database = get_database(settings)
    if kind not in ("title", "thumb_brief"):
        raise ValueError(f"unknown packaging kind: {kind!r} (use 'title' or 'thumb_brief')")
    _cut_or_raise(database, video_id, cut_id)

    from .packaging_jobs import generate_cut_packaging

    q = queue if queue is not None else get_queue(settings)
    rq_job = q.enqueue(
        generate_cut_packaging, video_id, cut_id, kind,
        item_ids=item_ids, k=k, regenerate=regenerate,
        job_timeout=settings.rq_job_timeout,
    )
    logger.info("Enqueued packaging %s for cut %s (rq %s)", kind, cut_id, rq_job.id)
    return {"cut_id": cut_id, "kind": kind, "rq_job_id": rq_job.id}


def enqueue_frame_extraction(
    video_id: str,
    cut_id: int,
    *,
    item_ids: list[int] | None = None,
    count: int | None = None,
    regenerate: bool = False,
    settings: Settings | None = None,
    database=None,
    queue=None,
) -> dict:
    """Enqueue DETERMINISTIC candidate-frame extraction (FFmpeg only, NO LLM)."""
    settings = settings or get_settings()
    if database is None:
        from ..db import get_database

        database = get_database(settings)
    _cut_or_raise(database, video_id, cut_id)

    from .packaging_jobs import extract_cut_frames

    q = queue if queue is not None else get_queue(settings)
    rq_job = q.enqueue(
        extract_cut_frames, video_id, cut_id,
        item_ids=item_ids, count=count, regenerate=regenerate,
        job_timeout=settings.rq_job_timeout,
    )
    logger.info("Enqueued frame extraction for cut %s (rq %s)", cut_id, rq_job.id)
    return {"cut_id": cut_id, "rq_job_id": rq_job.id}


def enqueue_frame_ranking(
    video_id: str,
    cut_id: int,
    *,
    item_ids: list[int] | None = None,
    regenerate: bool = False,
    settings: Settings | None = None,
    database=None,
    queue=None,
) -> dict:
    """Enqueue the opt-in VISION ranking of the extracted frames (costs an LLM call)."""
    settings = settings or get_settings()
    if database is None:
        from ..db import get_database

        database = get_database(settings)
    _cut_or_raise(database, video_id, cut_id)

    from .packaging_jobs import rank_cut_frames

    q = queue if queue is not None else get_queue(settings)
    rq_job = q.enqueue(
        rank_cut_frames, video_id, cut_id,
        item_ids=item_ids, regenerate=regenerate,
        job_timeout=settings.rq_job_timeout,
    )
    logger.info("Enqueued frame ranking for cut %s (rq %s)", cut_id, rq_job.id)
    return {"cut_id": cut_id, "rq_job_id": rq_job.id}


def enqueue_audience_match(
    video_id: str,
    *,
    regenerate: bool = False,
    settings: Settings | None = None,
    database=None,
    queue=None,
) -> dict:
    """Enqueue the cheap, cached-per-live audience→topic match (costs an LLM call)."""
    settings = settings or get_settings()
    if database is None:
        from ..db import get_database

        database = get_database(settings)

    from ..db import repo

    with database.session() as s:
        if repo.get_video(s, video_id) is None:
            raise KeyError(video_id)

    from .packaging_jobs import match_live_audience

    q = queue if queue is not None else get_queue(settings)
    rq_job = q.enqueue(
        match_live_audience, video_id, regenerate=regenerate,
        job_timeout=settings.rq_job_timeout,
    )
    logger.info("Enqueued audience match for live %s (rq %s)", video_id, rq_job.id)
    return {"video_id": video_id, "rq_job_id": rq_job.id}


def enqueue_video(
    url: str,
    *,
    project_id: int,
    settings: Settings | None = None,
    database=None,
    queue=None,
) -> dict:
    """Validate the URL, (re)create the Video (queued) + Job and enqueue processing.

    Scoped to a niche: the live is contained in `project_id`. Raises `KeyError`
    if the project does not exist (the route maps it to 404)."""
    settings = settings or get_settings()
    if database is None:
        from ..db import get_database

        database = get_database(settings)

    from ..db import competitors as crepo
    from ..db import repo

    url = validate_youtube_url(url)
    video_id = extract_video_id(url)

    with database.session() as s:
        if crepo.get_project(s, project_id) is None:
            raise KeyError(project_id)
        existing = repo.get_video(s, video_id)
        repo.upsert_video(s, Video(
            id=video_id, url=url, project_id=project_id,
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
    """Delete EVERYTHING about the video (database + files). Refuses if active (stop it first).

    The audio lives in a throwaway temp dir (already gone) and transcripts/topics/
    chat are database rows; the one on-disk artifact is the rendered clips
    (`<clips_dir>/<video_id>/…`), removed here so a deleted live leaves nothing.
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

    # Remove the on-disk per-video artifacts (clips and export bundles). This is
    # an explicit deletion — neither is ever swept by any automatic cleanup.
    import shutil

    for base in (settings.clips_dir, settings.exports_dir):
        video_dir = Path(base) / video_id
        if video_dir.exists():
            shutil.rmtree(video_dir, ignore_errors=True)

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
