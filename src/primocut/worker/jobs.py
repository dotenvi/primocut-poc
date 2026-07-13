"""RQ job: process a video by running the pipeline (with persistence and progress).

1 job per video. Reuses `run_stages` (same logic/resilience as the CLI) with
`DbStore` (pay-once cache in the database) and `DbReporter` (live status/progress).
Re-enqueueing is idempotent: stages whose artifact is already in the database are skipped.
"""

from __future__ import annotations

from typing import Callable

from ..config import Settings, get_settings
from ..download import audio as audio_dl
from ..chat import downloader as chat_downloader
from ..logging_conf import get_logger
from ..models import VideoStatus
from ..pipeline import PipelineError, run_stages
from ..progress import DbReporter
from ..store import DbStore

logger = get_logger(__name__)


def process_video(
    video_id: str,
    *,
    create_topics: bool = False,
    settings: Settings | None = None,
    database=None,
    engine=None,
    llm=None,
    gpu_lock=None,
    metadata_fn: Callable = audio_dl.fetch_metadata,
    download_audio_fn: Callable = audio_dl.download_audio,
    download_chat_fn: Callable = chat_downloader.download_chat,
) -> dict:
    settings = settings or get_settings()
    if database is None:
        from ..db import get_database

        database = get_database(settings)

    from ..db import repo

    with database.session() as s:
        video = repo.get_video(s, video_id)
        if video is None:
            raise ValueError(f"Video {video_id!r} not found in the database.")
        url = video.url
        job = repo.latest_job(s, video_id) or repo.create_job(s, video_id)
        job_id = job.id
        repo.update_job(s, job_id, status="started", error=None)

    if engine is None:
        from ..transcription.faster_whisper_engine import get_engine

        engine = get_engine(settings)
    if llm is None:
        from ..analysis.llm import get_llm

        llm = get_llm(settings)

    if gpu_lock is None:  # serialize the GPU across workers (1 transcription at a time)
        from ..gpu import redis_gpu_lock

        gpu_lock = redis_gpu_lock(settings)

    store = DbStore(database)
    reporter = DbReporter(database, job_id, video_id)

    try:
        result = run_stages(
            url, settings=settings, store=store, reporter=reporter,
            engine=engine, llm=llm, create_topics=create_topics, gpu_lock=gpu_lock,
            metadata_fn=metadata_fn, download_audio_fn=download_audio_fn,
            download_chat_fn=download_chat_fn,
        )
    except PipelineError as exc:  # an essential stage failed → error
        logger.error("Job %s failed: %s", job_id, exc)
        with database.session() as s:
            repo.update_job(s, job_id, status="failed", error=str(exc))
            repo.set_video_status(s, video_id, VideoStatus.ERROR)
        raise

    with database.session() as s:
        repo.update_job(s, job_id, status="finished", stage="Done",
                        progress=100, warnings=result.warnings)
        repo.set_video_status(s, video_id, VideoStatus.DONE)

    return {
        "video_id": video_id,
        "topics": len(result.topics),
        "warnings": result.warnings,
    }
