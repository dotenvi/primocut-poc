"""Execução resiliente das janelas de análise.

Cada janela chama o LLM com retry (erros transitórios). Se uma janela falhar de
forma permanente (crédito, auth, sem tool use) ou esgotar os retries, a etapa é
**encerrada mantendo o que já foi coletado** (parcial) e registra um aviso — o
pipeline então degrada o relatório em vez de abortar.
"""

from __future__ import annotations

from typing import Any, Iterator

from ..config import Settings
from ..logging_conf import get_logger
from ..resilience import RetryPolicy, is_transient, retry
from .llm import SelectionLLM
from .windowing import Window

logger = get_logger(__name__)


def select_windows(
    windows: list[Window],
    llm: SelectionLLM,
    *,
    system: str,
    tool: dict[str, Any],
    tool_name: str,
    settings: Settings,
    label: str,
    warnings: list[str] | None = None,
) -> Iterator[dict[str, Any]]:
    """Gera o payload de cada janela, com retry; encerra (parcial) em falha permanente."""
    policy = RetryPolicy(
        attempts=settings.retry_attempts,
        base_delay=settings.retry_base_delay,
        max_delay=settings.retry_max_delay,
    )
    total = len(windows)
    for i, window in enumerate(windows):
        logger.info("%s: janela %d/%d", label, i + 1, total)
        try:
            payload = retry(
                lambda w=window: llm.select(
                    system=system, user=w.indexed_text(), tool=tool, tool_name=tool_name
                ),
                policy=policy,
                is_retryable=is_transient,
                label=f"{label} janela {i + 1}",
            )
        except Exception as exc:  # noqa: BLE001 — degradação controlada
            msg = f"{label}: interrompido na janela {i + 1}/{total} — {type(exc).__name__}: {exc}"
            logger.warning(msg)
            if warnings is not None:
                warnings.append(msg)
            return  # mantém o parcial já coletado
        yield payload
