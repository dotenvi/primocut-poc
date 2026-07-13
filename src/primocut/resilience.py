"""Cross-cutting resilience: retry policy, backoff and error classification.

Reused by every pipeline stage. `retry` takes an injectable `sleep` so it can
be tested without real waits.
"""

from __future__ import annotations

import time
import urllib.error
from dataclasses import dataclass
from typing import Callable, TypeVar

from .logging_conf import get_logger

logger = get_logger(__name__)

T = TypeVar("T")

# Class names (without importing the SDKs) that indicate a transient provider error.
_TRANSIENT_NAMES = {
    "APIConnectionError",
    "APITimeoutError",
    "APIConnectionTimeoutError",
    "RateLimitError",
    "InternalServerError",
    "OverloadedError",
}


@dataclass(frozen=True)
class RetryPolicy:
    attempts: int = 3
    base_delay: float = 1.0
    max_delay: float = 30.0


def is_transient(exc: BaseException) -> bool:
    """Heuristic: is the error likely to succeed if retried?"""
    status = getattr(exc, "status_code", None)
    if isinstance(status, int) and (status in {408, 409, 429} or status >= 500):
        return True
    # ConnectionError and URLError are subclasses of OSError; FileNotFoundError does NOT count.
    if isinstance(exc, (ConnectionError, TimeoutError, urllib.error.URLError)):
        return True
    return type(exc).__name__ in _TRANSIENT_NAMES


def retry(
    fn: Callable[[], T],
    *,
    policy: RetryPolicy | None = None,
    is_retryable: Callable[[BaseException], bool] = is_transient,
    sleep: Callable[[float], None] = time.sleep,
    label: str = "",
) -> T:
    """Run `fn` with retry+backoff while `is_retryable` holds true."""
    policy = policy or RetryPolicy()
    last: BaseException | None = None
    for attempt in range(policy.attempts):
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001 — reclassified by is_retryable
            last = exc
            if not is_retryable(exc) or attempt == policy.attempts - 1:
                raise
            delay = min(policy.base_delay * (2 ** attempt), policy.max_delay)
            logger.warning(
                "retry%s: attempt %d/%d failed (%s); waiting %.1fs",
                f" [{label}]" if label else "",
                attempt + 1,
                policy.attempts,
                exc,
                delay,
            )
            sleep(delay)
    assert last is not None  # pragma: no cover — loop always returns or raises
    raise last
