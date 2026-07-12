"""Relato de progresso das etapas do pipeline (injetável).

O CLI usa `LogReporter` (só loga; comportamento da Fase 0). O worker usa
`DbReporter`, que persiste stage/progresso/status no banco para a UI consultar.
"""

from __future__ import annotations

from typing import Protocol

from .logging_conf import get_logger
from .models import VideoStatus

logger = get_logger(__name__)


class ProgressReporter(Protocol):
    def update(
        self, *, stage: str, progress: int, video_status: VideoStatus | None = None
    ) -> None:
        ...


class LogReporter:
    """No-op de persistência (o `step()` já loga início/fim de cada etapa)."""

    def update(
        self, *, stage: str, progress: int, video_status: VideoStatus | None = None
    ) -> None:
        logger.debug("progresso: %s (%d%%) status=%s", stage, progress, video_status)


class DbReporter:
    """Persiste stage/progresso/status no banco (job + video)."""

    def __init__(self, database, job_id: int, video_id: str) -> None:
        self.db = database
        self.job_id = job_id
        self.video_id = video_id

    def update(
        self, *, stage: str, progress: int, video_status: VideoStatus | None = None
    ) -> None:
        from .db import repo

        with self.db.session() as s:
            repo.update_job(s, self.job_id, stage=stage, progress=progress, status="started")
            if video_status is not None:
                repo.set_video_status(s, self.video_id, video_status)
