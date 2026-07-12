"""Lock distribuído de GPU (via Redis).

Garante que **apenas uma transcrição** use a GPU por vez — não importa quantos
workers estejam rodando. É a "regra": subir mais workers é seguro por construção,
porque a etapa de transcrição só entra quando obtém este lock.

CLI e testes usam um no-op (`contextlib.nullcontext`) — só o worker precisa do lock.
"""

from __future__ import annotations

from .config import Settings, get_settings

GPU_LOCK_NAME = "primocut:gpu"


def redis_gpu_lock(settings: Settings | None = None, connection=None):
    """Context manager que serializa o uso da GPU entre processos/workers.

    `with redis_gpu_lock(...):` bloqueia até a GPU ficar livre, executa, e libera.
    """
    settings = settings or get_settings()
    from redis import Redis

    conn = connection or Redis.from_url(settings.redis_url)
    # timeout = lease: se o detentor morrer, o lock é liberado depois desse tempo.
    # blocking (default) = espera a vez; RQ job_timeout é o teto final da espera.
    return conn.lock(GPU_LOCK_NAME, timeout=settings.gpu_lock_timeout)
