"""Limpeza de áudios antigos (retenção configurável).

Apaga SÓ arquivos de áudio em disco (`*.wav/*.m4a/*.mp3`) mais antigos que N dias.
Transcrições, tópicos, momentos e sinais do chat vivem no banco e são
**permanentes** — nunca são tocados aqui.
"""

from __future__ import annotations

import time
from pathlib import Path

from ..config import Settings, get_settings
from ..logging_conf import get_logger

logger = get_logger(__name__)

_AUDIO_GLOBS = ("*.wav", "*.m4a", "*.mp3")


def cleanup_audio(
    data_dir: Path | str,
    retention_days: int,
    *,
    now: float | None = None,
) -> list[Path]:
    """Remove áudios com mtime além de `retention_days`. Retorna os apagados."""
    data_dir = Path(data_dir)
    if not data_dir.exists():
        return []
    now = time.time() if now is None else now
    cutoff = now - retention_days * 86400
    deleted: list[Path] = []
    for pattern in _AUDIO_GLOBS:
        for p in data_dir.glob(pattern):
            if p.is_file() and p.stat().st_mtime < cutoff:
                p.unlink()
                deleted.append(p)
    if deleted:
        logger.info("Limpeza: %d áudio(s) removido(s) (retenção %dd).",
                    len(deleted), retention_days)
    return deleted


def purge_video_files(data_dir: Path | str, video_id: str) -> list[Path]:
    """Apaga TODOS os arquivos de um vídeo (áudio + caches JSON): `data/<id>.*`."""
    data_dir = Path(data_dir)
    if not data_dir.exists():
        return []
    deleted: list[Path] = []
    for p in data_dir.glob(f"{video_id}.*"):
        if p.is_file():
            p.unlink()
            deleted.append(p)
    if deleted:
        logger.info("Purga: %d arquivo(s) de %s removido(s).", len(deleted), video_id)
    return deleted


def run_cleanup_job(settings: Settings | None = None) -> int:
    """Entry point sem args para a fila/agendador."""
    settings = settings or get_settings()
    return len(cleanup_audio(settings.data_dir, settings.audio_retention_days))


def schedule_daily_cleanup(settings: Settings | None = None, cron: str = "0 4 * * *") -> None:
    """Registra (idempotente) a limpeza diária no rq-scheduler."""
    from redis import Redis
    from rq_scheduler import Scheduler

    settings = settings or get_settings()
    scheduler = Scheduler(queue_name=settings.rq_queue,
                          connection=Redis.from_url(settings.redis_url))
    for job in scheduler.get_jobs():
        if job.func_name and job.func_name.endswith("run_cleanup_job"):
            scheduler.cancel(job)
    scheduler.cron(cron, func=run_cleanup_job, queue_name=settings.rq_queue)
    logger.info("Limpeza diária agendada (%s).", cron)
