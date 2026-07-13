"""Pipeline stage progress reporting (injectable).

The CLI uses `LogReporter` (only logs; Phase 0 behaviour). The worker uses
`DbReporter`, which persists stage/progress/status in the database for the UI to
poll.
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
    """No-op persistence (`step()` already logs the start/end of each stage)."""

    def update(
        self, *, stage: str, progress: int, video_status: VideoStatus | None = None
    ) -> None:
        logger.debug("progress: %s (%d%%) status=%s", stage, progress, video_status)


class DbReporter:
    """Persists stage/progress/status in the database (job + video)."""

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
