"""Resilient execution of the analysis windows.

Each window calls the LLM with retry (transient errors). If a window fails
permanently (credit, auth, no tool use) or exhausts its retries, the stage is
**ended keeping whatever was already collected** (partial) and a warning is
recorded — the pipeline then degrades the report instead of aborting.
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
    """Yield each window's payload, with retry; end (partial) on permanent failure."""
    policy = RetryPolicy(
        attempts=settings.retry_attempts,
        base_delay=settings.retry_base_delay,
        max_delay=settings.retry_max_delay,
    )
    total = len(windows)
    for i, window in enumerate(windows):
        logger.info("%s: window %d/%d", label, i + 1, total)
        try:
            payload = retry(
                lambda w=window: llm.select(
                    system=system, user=w.indexed_text(), tool=tool, tool_name=tool_name
                ),
                policy=policy,
                is_retryable=is_transient,
                label=f"{label} window {i + 1}",
            )
        except Exception as exc:  # noqa: BLE001 — controlled degradation
            msg = f"{label}: stopped at window {i + 1}/{total} — {type(exc).__name__}: {exc}"
            logger.warning(msg)
            if warnings is not None:
                warnings.append(msg)
            return  # keep the partial already collected
        yield payload
