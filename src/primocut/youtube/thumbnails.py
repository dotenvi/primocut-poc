"""Thumbnail content hashing.

The initial `thumbnail_hash` stored per video is the SHA-256 of the thumbnail
image BYTES (not the URL), so a later snapshot can detect a packaging change
even when YouTube keeps the same thumbnail URL. The byte fetcher is injectable
so tests run without network. Hashing degrades to an empty string on any error
(never crashes the backfill).
"""

from __future__ import annotations

import hashlib
import urllib.request
from typing import Callable

from ..logging_conf import get_logger

logger = get_logger(__name__)

ThumbnailFetcher = Callable[[str], bytes]


def fetch_thumbnail_bytes(url: str, *, timeout: float = 30.0) -> bytes:
    with urllib.request.urlopen(url, timeout=timeout) as resp:  # noqa: S310 (trusted host)
        return resp.read()


def thumbnail_hash(url: str, *, fetcher: ThumbnailFetcher = fetch_thumbnail_bytes) -> str:
    """SHA-256 hex of the thumbnail image bytes; "" when absent or on error."""
    if not url:
        return ""
    try:
        return hashlib.sha256(fetcher(url)).hexdigest()
    except Exception as exc:  # degrade: a missing hash must not fail the backfill
        logger.warning("Thumbnail hashing failed for %s: %s", url, exc)
        return ""
