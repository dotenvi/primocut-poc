"""Enfileiramento de vídeos na fila RQ."""

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


def enqueue_video(
    url: str,
    *,
    settings: Settings | None = None,
    database=None,
    queue=None,
) -> dict:
    """Valida a URL, (re)cria o Video (queued) + Job e enfileira o processamento."""
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
    """Reprocessa um vídeo já existente (sem revalidar URL; reseta status→queued).

    Idempotente: `process_video` pula etapas com artefato já no banco (cache).
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


class VideoBusyError(RuntimeError):
    """Tentativa de deletar um vídeo que ainda está em processamento ativo."""


def _default_stopper(rq_job_id: str | None, settings: Settings, connection=None) -> None:
    """Cancela o job RQ: `cancel` se enfileirado, SIGKILL no horse se rodando."""
    if not rq_job_id:
        return
    from redis import Redis
    from rq.job import Job

    conn = connection or Redis.from_url(settings.redis_url)
    try:
        job = Job.fetch(rq_job_id, connection=conn)
    except Exception:
        return  # job já não existe
    try:
        status = job.get_status(refresh=True)
        if status == "started":
            from rq.command import send_stop_job_command

            send_stop_job_command(conn, rq_job_id)
        else:
            job.cancel()
    except Exception as exc:  # best-effort
        logger.warning("Falha ao parar o job %s: %s", rq_job_id, exc)


def stop_video(
    video_id: str,
    *,
    settings: Settings | None = None,
    database=None,
    connection=None,
    stopper=None,
) -> dict:
    """Interrompe o processamento de um vídeo (cancela o job) e marca `stopped`."""
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
            repo.update_job(s, job_id, status="stopped")
        repo.set_video_status(s, video_id, VideoStatus.STOPPED)

    logger.info("Vídeo %s interrompido.", video_id)
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
    """Apaga TUDO sobre o vídeo (banco + arquivos). Recusa se estiver ativo (use stop antes)."""
    settings = settings or get_settings()
    if database is None:
        from ..db import get_database

        database = get_database(settings)

    from ..db import repo
    from ..models import ACTIVE_STATUSES
    from .cleanup import purge_video_files

    with database.session() as s:
        v = repo.get_video(s, video_id)
        if v is None:
            raise KeyError(video_id)
        status = v.status
        job = repo.latest_job(s, video_id)
        rq_id = job.rq_job_id if job else None

    if status in ACTIVE_STATUSES and not force:
        raise VideoBusyError(video_id)

    (stopper or _default_stopper)(rq_id, settings, connection)  # garante que não roda mais

    with database.session() as s:
        repo.delete_video(s, video_id)
    files = purge_video_files(settings.data_dir, video_id)

    logger.info("Vídeo %s deletado (%d arquivo(s)).", video_id, len(files))
    return {"video_id": video_id, "deleted_files": len(files)}


def _enqueue_job(database, queue, video_id: str, settings: Settings) -> dict:
    from ..db import repo

    with database.session() as s:
        job_id = repo.create_job(s, video_id).id

    q = queue if queue is not None else get_queue(settings)
    rq_job = q.enqueue(process_video, video_id, job_timeout=settings.rq_job_timeout)

    with database.session() as s:
        repo.update_job(s, job_id, rq_job_id=rq_job.id)

    logger.info("Enfileirado %s (job %s, rq %s)", video_id, job_id, rq_job.id)
    return {"video_id": video_id, "job_id": job_id, "rq_job_id": rq_job.id}
