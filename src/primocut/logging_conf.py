"""Structured logging + pipeline stage progress helper."""

from __future__ import annotations

import logging
from contextlib import contextmanager
from time import perf_counter
from typing import Iterator

from rich.logging import RichHandler

_CONFIGURED = False


def setup_logging(level: int = logging.INFO) -> None:
    """Configure logging with RichHandler (idempotent)."""
    global _CONFIGURED
    if _CONFIGURED:
        return
    logging.basicConfig(
        level=level,
        format="%(message)s",
        datefmt="[%X]",
        handlers=[RichHandler(rich_tracebacks=True, show_path=False)],
    )
    _CONFIGURED = True


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)


@contextmanager
def step(logger: logging.Logger, name: str) -> Iterator[None]:
    """Mark the start/end of a pipeline stage with its duration."""
    logger.info("▶  %s", name)
    start = perf_counter()
    try:
        yield
    except Exception:
        logger.exception("✖  %s (failed)", name)
        raise
    else:
        logger.info("✔  %s (%.1fs)", name, perf_counter() - start)
