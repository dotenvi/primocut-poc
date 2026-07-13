"""Distributed GPU lock (via Redis).

Guarantees that **only one transcription** uses the GPU at a time — no matter
how many workers are running. That is the rule: bringing up more workers is safe
by construction, because the transcription stage only enters once it obtains
this lock.

CLI and tests use a no-op (`contextlib.nullcontext`) — only the worker needs the lock.
"""

from __future__ import annotations

from .config import Settings, get_settings

GPU_LOCK_NAME = "primocut:gpu"


def redis_gpu_lock(settings: Settings | None = None, connection=None):
    """Context manager that serializes GPU use across processes/workers.

    `with redis_gpu_lock(...):` blocks until the GPU is free, runs, and releases.
    """
    settings = settings or get_settings()
    from redis import Redis

    conn = connection or Redis.from_url(settings.redis_url)
    # timeout = lease: if the holder dies, the lock is released after this time.
    # blocking (default) = wait your turn; RQ job_timeout is the final cap on the wait.
    return conn.lock(GPU_LOCK_NAME, timeout=settings.gpu_lock_timeout)
