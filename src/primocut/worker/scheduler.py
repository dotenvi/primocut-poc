"""rq-scheduler wiring for the competitor-snapshot cycles.

Two independent cron registrations of the same `snapshot_cycle` function with a
different scope: a daily "all" pass and a 6-hourly "recent" pass. Registration is
idempotent (existing jobs with our ids are cancelled first), so re-running the
scheduler never duplicates the schedule. The scheduler process only moves due
jobs onto the queue; they execute in `primocut worker`.
"""

from __future__ import annotations

from ..config import Settings, get_settings
from ..logging_conf import get_logger
from .snapshot_jobs import snapshot_cycle

logger = get_logger(__name__)

# Stable ids make registration idempotent across restarts. RQ only allows
# letters, numbers, underscores and dashes in job ids.
JOB_ID_ALL = "primocut-snapshot-all"
JOB_ID_RECENT = "primocut-snapshot-recent"


def get_scheduler(settings: Settings | None = None):
    from redis import Redis
    from rq_scheduler import Scheduler

    settings = settings or get_settings()
    return Scheduler(
        queue_name=settings.rq_queue,
        connection=Redis.from_url(settings.redis_url),
    )


def register_snapshot_schedules(scheduler, settings: Settings | None = None) -> None:
    """(Re)register the daily + 6-hourly snapshot crons, idempotently."""
    settings = settings or get_settings()

    for job in list(scheduler.get_jobs()):
        if job.id in {JOB_ID_ALL, JOB_ID_RECENT}:
            scheduler.cancel(job)

    scheduler.cron(
        settings.snapshot_daily_cron,
        func=snapshot_cycle,
        args=["all"],
        id=JOB_ID_ALL,
        queue_name=settings.rq_queue,
        timeout=settings.rq_job_timeout,
    )
    scheduler.cron(
        settings.snapshot_recent_cron,
        func=snapshot_cycle,
        args=["recent"],
        id=JOB_ID_RECENT,
        queue_name=settings.rq_queue,
        timeout=settings.rq_job_timeout,
    )
    logger.info(
        "Registered snapshot schedules: all=%r recent=%r",
        settings.snapshot_daily_cron,
        settings.snapshot_recent_cron,
    )


def run_scheduler(settings: Settings | None = None) -> None:
    """Register the schedules and run the (blocking) rq-scheduler loop."""
    settings = settings or get_settings()
    scheduler = get_scheduler(settings)
    register_snapshot_schedules(scheduler, settings)
    scheduler.run()
