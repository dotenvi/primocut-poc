"""Per-project conditional distributions of title/thumbnail attributes.

For each boolean attribute we compare how often it appears among a project's
outliers versus its whole population, reporting the lift. A pattern is reported
only when enough outliers exhibit it (the minimum-occurrence guard) — below that
threshold the sample is too small to claim a pattern (guideline 10).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence


@dataclass(frozen=True)
class Pattern:
    attribute: str
    outlier_rate: float
    population_rate: float
    lift: float | None
    outlier_occurrences: int


def _boolean_attributes(rows: Sequence[dict]) -> set[str]:
    keys: set[str] = set()
    for attrs in rows:
        for key, value in attrs.items():
            if isinstance(value, bool):
                keys.add(key)
    return keys


def conditional_patterns(
    population: Sequence[dict],
    outliers: Sequence[dict],
    *,
    min_occurrence: int,
) -> list[Pattern]:
    """Frequency of each boolean attribute among `outliers` vs `population`.

    Only patterns whose outlier occurrence count is at least `min_occurrence` are
    returned; the rest are suppressed as small-sample noise. Results are sorted by
    descending lift, then descending occurrence.
    """
    if not population or not outliers:
        return []

    pop_n = len(population)
    out_n = len(outliers)
    results: list[Pattern] = []

    for attr in sorted(_boolean_attributes(population) | _boolean_attributes(outliers)):
        out_hits = sum(1 for a in outliers if a.get(attr) is True)
        if out_hits < min_occurrence:
            continue  # minimum-occurrence guard: too few outliers to claim a pattern
        pop_hits = sum(1 for a in population if a.get(attr) is True)
        outlier_rate = out_hits / out_n
        population_rate = pop_hits / pop_n
        lift = (outlier_rate / population_rate) if population_rate > 0 else None
        results.append(Pattern(attr, outlier_rate, population_rate, lift, out_hits))

    results.sort(key=lambda p: (p.lift if p.lift is not None else float("inf"), p.outlier_occurrences), reverse=True)
    return results
