"""Consolidation of spans across overlapping windows.

Neighbouring windows share segments (the overlap), so the same topic may show
up twice with nearly identical spans. We merge only items that overlap
**substantially** — a distinct subject that merely touches its neighbour at a
window boundary is kept separate, so we never over-deduplicate distinct topics.
"""

from __future__ import annotations

from typing import TypeVar

from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)

# Minimum temporal overlap, as a fraction of the shorter item's span, for two
# items to be treated as the same subject (a window-overlap duplicate). Window
# duplicates overlap almost entirely (~1.0); distinct adjacent subjects barely
# overlap, so 0.5 cleanly separates the two cases.
DEFAULT_MIN_OVERLAP_RATIO = 0.5


def merge_overlapping(items: list[T], *, min_overlap_ratio: float = DEFAULT_MIN_OVERLAP_RATIO) -> list[T]:
    """Merge items (with .start/.end/.score) that overlap substantially.

    Assumes each item has the fields `start`, `end`, `score`. Two items are
    merged when their temporal overlap is at least `min_overlap_ratio` of the
    shorter item's span; the merged item keeps the metadata of the
    higher-scoring one and spans the union. Returns the consolidated list in
    chronological order.
    """
    if not items:
        return []
    ordered = sorted(items, key=lambda x: (x.start, x.end))
    merged: list[T] = [ordered[0]]
    for it in ordered[1:]:
        prev = merged[-1]
        overlap = min(prev.end, it.end) - max(prev.start, it.start)
        shorter = min(prev.end - prev.start, it.end - it.start)
        is_duplicate = overlap > 0 and (shorter <= 0 or overlap / shorter >= min_overlap_ratio)
        if is_duplicate:
            winner = it if it.score > prev.score else prev
            merged[-1] = winner.model_copy(
                update={"start": min(prev.start, it.start), "end": max(prev.end, it.end)}
            )
        else:
            merged.append(it)
    return merged
