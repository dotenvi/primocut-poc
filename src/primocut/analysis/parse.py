"""Tolerant parsing of the LLM responses.

The LLM sometimes returns the array in a less-than-ideal shape (an indexed
dict, items that are not objects, missing fields). Instead of raising and
tearing down the whole analysis — losing work already paid for — we normalize
and drop only the bad items, logging a warning.
"""

from __future__ import annotations

from typing import Any

from ..logging_conf import get_logger

logger = get_logger(__name__)


def coerce_items(payload: Any, key: str) -> list[dict[str, Any]]:
    """Extract `payload[key]` as a list of dicts, tolerating variations."""
    items = payload.get(key) if isinstance(payload, dict) else None
    if isinstance(items, dict):
        # some models return {"0": {...}, "1": {...}} instead of a list
        items = list(items.values())
    if items is None:
        return []
    if not isinstance(items, list):
        logger.warning("Analysis: field %r is not a list (%s); ignoring.", key, type(items).__name__)
        return []
    good = [it for it in items if isinstance(it, dict)]
    if len(good) != len(items):
        logger.warning("Analysis: %d item(s) in %r were not objects and were ignored.",
                       len(items) - len(good), key)
    return good


def span_indices(item: dict[str, Any]) -> tuple[int, int]:
    """Read start_index/end_index as integers. Raises KeyError/ValueError/TypeError."""
    return int(item["start_index"]), int(item["end_index"])
