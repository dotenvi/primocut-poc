"""Per-project conditional distributions of title/thumbnail attributes.

For each boolean attribute we compare how often it appears among a project's
outliers versus a matched CONTROL group, reporting the lift and a verdict. The
control is the non-outlier, mature, sufficient-baseline videos — never the whole
population: a population that contains the outliers themselves puts the numerator
inside the denominator and crushes every lift toward 1.0. Provisional (< 7 days)
videos are censored from BOTH groups: a young video is an unobserved outcome, not
a "non-outlier", and a future outlier already carrying outlier attributes would
bias the control in one direction only.

An attribute is reported only when the 95% Wilson score intervals of its two
frequencies do not overlap, in either direction (positive pattern or anti-pattern
— an attribute rarer in outliers is as actionable as one commoner). Wilson, not
Wald, because Wald's width collapses to zero at p=0/p=1 (frequent with rare
attributes and small n). There is no minimum-n floor: a small group yields a wide
interval that never separates, so the guard self-regulates.

Callers pass ONE source's attributes at a time (syntactic, title_llm,
thumbnail_llm) with each group restricted to the videos that have a row for it. A
video nobody enriched lacks the AI keys, so merging sources would count it as
"attribute absent" rather than "unknown", deflating a rate and fabricating lift.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from .core import wilson_interval


@dataclass(frozen=True)
class Pattern:
    attribute: str
    freq_outlier: float
    ci_outlier: tuple[float, float]
    freq_control: float
    ci_control: tuple[float, float]
    lift: float | None
    n_outlier: int
    n_control: int
    verdict: str  # "positive" | "negative"
    source: str = ""


def _boolean_attributes(rows: Sequence[dict]) -> set[str]:
    keys: set[str] = set()
    for attrs in rows:
        for key, value in attrs.items():
            if isinstance(value, bool):
                keys.add(key)
    return keys


def conditional_patterns(
    outliers: Sequence[dict],
    control: Sequence[dict],
    *,
    z: float,
    source: str = "",
) -> list[Pattern]:
    """Frequency of each boolean attribute among `outliers` vs `control`.

    Both sequences must come from the SAME attribute source, each restricted to
    the videos that have a row for it. `source` tags the results so a dashboard
    can merge several sources without losing where each came from.

    An attribute is returned only when its two 95% (`z`) Wilson intervals do not
    overlap: lower(outlier) > upper(control) is a positive pattern, upper(outlier)
    < lower(control) an anti-pattern; anything else is inconclusive and dropped.
    Results are sorted by descending effect size (lift), not by significance.
    """
    if not outliers or not control:
        return []

    n_out = len(outliers)
    n_ctl = len(control)
    results: list[Pattern] = []

    for attr in sorted(_boolean_attributes(outliers) | _boolean_attributes(control)):
        out_hits = sum(1 for a in outliers if a.get(attr) is True)
        ctl_hits = sum(1 for a in control if a.get(attr) is True)
        freq_out = out_hits / n_out
        freq_ctl = ctl_hits / n_ctl
        lo_out, hi_out = wilson_interval(out_hits, n_out, z)
        lo_ctl, hi_ctl = wilson_interval(ctl_hits, n_ctl, z)

        if lo_out > hi_ctl:
            verdict = "positive"
        elif hi_out < lo_ctl:
            verdict = "negative"
        else:
            continue  # intervals overlap — inconclusive, not reported

        lift = (freq_out / freq_ctl) if freq_ctl > 0 else None
        results.append(Pattern(
            attribute=attr, freq_outlier=freq_out, ci_outlier=(lo_out, hi_out),
            freq_control=freq_ctl, ci_control=(lo_ctl, hi_ctl), lift=lift,
            n_outlier=n_out, n_control=n_ctl, verdict=verdict, source=source,
        ))

    results.sort(key=lambda p: (p.lift if p.lift is not None else float("inf")), reverse=True)
    return results
