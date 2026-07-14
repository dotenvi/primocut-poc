"""Tests for the snapshot scheduler wiring.

The wrapper logic (idempotent cancel-by-id + two cron registrations with the
right scope) is exercised against a stub scheduler — no Redis, instant. The job
ids are validated with RQ's own `validate_job_id` to guard the character-set
regression (RQ rejects colons).
"""

from __future__ import annotations

from types import SimpleNamespace

from rq.job import validate_job_id

from primocut.config import Settings
from primocut.worker.scheduler import (
    JOB_ID_ALL,
    JOB_ID_RECENT,
    register_snapshot_schedules,
)
from primocut.worker.snapshot_jobs import snapshot_cycle


class StubScheduler:
    """Records cron registrations; mimics get_jobs()/cancel()/cron()."""

    def __init__(self):
        self.jobs: dict[str, SimpleNamespace] = {}

    def get_jobs(self):
        return list(self.jobs.values())

    def cancel(self, job):
        self.jobs.pop(job.id, None)

    def cron(self, cron_string, *, func, args, id, queue_name, timeout=None):
        job = SimpleNamespace(id=id, func=func, args=args, cron=cron_string)
        self.jobs[id] = job
        return job


def test_job_ids_are_valid_for_rq():
    # RQ only allows letters/numbers/underscores/dashes (colons are rejected).
    validate_job_id(JOB_ID_ALL)
    validate_job_id(JOB_ID_RECENT)


def test_registers_two_cron_jobs():
    sch = StubScheduler()
    register_snapshot_schedules(sch, Settings())

    by_id = sch.jobs
    assert set(by_id) == {JOB_ID_ALL, JOB_ID_RECENT}
    assert by_id[JOB_ID_ALL].func is snapshot_cycle and by_id[JOB_ID_ALL].args == ["all"]
    assert by_id[JOB_ID_RECENT].func is snapshot_cycle and by_id[JOB_ID_RECENT].args == ["recent"]
    assert by_id[JOB_ID_ALL].cron == "0 3 * * *"       # daily, offset from the 6h grid
    assert by_id[JOB_ID_RECENT].cron == "0 */6 * * *"  # every 6 hours


def test_registration_is_idempotent():
    sch = StubScheduler()
    register_snapshot_schedules(sch, Settings())
    register_snapshot_schedules(sch, Settings())  # re-run must not duplicate
    assert sorted(sch.jobs) == sorted([JOB_ID_ALL, JOB_ID_RECENT])


def test_custom_crons_from_settings():
    sch = StubScheduler()
    register_snapshot_schedules(
        sch, Settings(snapshot_daily_cron="30 4 * * *", snapshot_recent_cron="0 */4 * * *")
    )
    assert sch.jobs[JOB_ID_ALL].cron == "30 4 * * *"
    assert sch.jobs[JOB_ID_RECENT].cron == "0 */4 * * *"
