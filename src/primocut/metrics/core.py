"""Pure numeric core of the metrics engine (no DB, no I/O).

Every function here operates on plain values / small dataclasses so the
guideline-10 methodology is unit-tested in isolation:

- baselines use the **median**, never the mean, so a single viral in the window
  does not distort the channel's typical performance;
- Shorts and long-form are scored against **separate** baselines (the caller
  passes the same-format peer set);
- views-per-hour comes **exclusively from snapshot deltas** — when no delta exists
  the value is `None`, never `total_views / age`;
- a young video's score is flagged **provisional**, and a channel with too few
  same-format videos yields **insufficient_data** rather than a misleading number.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from statistics import median as _median
from typing import Sequence


# ------------------------- small value types -------------------------


@dataclass(frozen=True)
class Snap:
    """One point of a video's view time series."""

    captured_at: datetime
    views: int


@dataclass(frozen=True)
class VideoMeta:
    """Metadata used for the descriptive cadence/engagement aggregates."""

    published_at: datetime | None
    is_short: bool
    duration_s: int


@dataclass(frozen=True)
class Baseline:
    median: float
    sample_size: int


@dataclass(frozen=True)
class OutlierScore:
    score: float | None
    bracket: str | None
    provisional: bool
    baseline: float | None
    sample_size: int
    status: str  # "ok" | "provisional" | "insufficient_data"


@dataclass(frozen=True)
class VphPoint:
    from_at: datetime
    to_at: datetime
    hours: float
    vph: float


@dataclass(frozen=True)
class Resurgence:
    is_resurgent: bool
    ratio: float | None
    current_vph: float | None
    recent_avg_vph: float | None


@dataclass(frozen=True)
class Impact:
    vph_before: float | None
    vph_after: float | None


@dataclass(frozen=True)
class Engagement:
    like_rate: float | None
    comment_rate: float | None


@dataclass(frozen=True)
class Cadence:
    uploads_per_week: float
    by_weekday: dict[int, int]
    by_hour: dict[int, int]
    duration_by_format: dict[str, dict[str, float]]


# ------------------------- helpers -------------------------


def median(values: Sequence[float]) -> float | None:
    """Median of a non-empty sequence (None if empty). Robust to outliers — this
    is why baselines never use the mean."""
    vals = [v for v in values if v is not None]
    if not vals:
        return None
    return float(_median(vals))


def parse_brackets(spec: str) -> list[float]:
    """Parse an ascending, comma-separated bracket spec (e.g. "3,6,10")."""
    out = [float(part) for part in spec.split(",") if part.strip()]
    return sorted(out)


def _bracket_label(score: float, brackets: Sequence[float]) -> str | None:
    """Highest bracket the score reaches, formatted like "3x" (None if below all)."""
    label: str | None = None
    for threshold in sorted(brackets):
        if score >= threshold:
            value = int(threshold) if float(threshold).is_integer() else threshold
            label = f"{value}x"
    return label


# ------------------------- outlier score -------------------------


def channel_baseline(peer_views: Sequence[int], *, min_sample: int) -> Baseline | None:
    """Median of a same-format, already-windowed peer set. `None` when there are
    fewer than `min_sample` peers, or the median is non-positive (no basis for a
    ratio) — both surface as "insufficient_data" downstream."""
    vals = [int(v) for v in peer_views]
    if len(vals) < min_sample:
        return None
    med = median(vals)
    if med is None or med <= 0:
        return None
    return Baseline(median=med, sample_size=len(vals))


def outlier_score(
    views: int,
    baseline: Baseline | None,
    *,
    brackets: Sequence[float],
    provisional: bool,
) -> OutlierScore:
    """Score = views / baseline median, labelled by bracket. A young video is
    flagged provisional; an absent baseline yields insufficient_data."""
    if baseline is None:
        return OutlierScore(
            score=None, bracket=None, provisional=provisional,
            baseline=None, sample_size=0, status="insufficient_data",
        )
    score = views / baseline.median
    return OutlierScore(
        score=score,
        bracket=_bracket_label(score, brackets),
        provisional=provisional,
        baseline=baseline.median,
        sample_size=baseline.sample_size,
        status="provisional" if provisional else "ok",
    )


# ------------------------- views-per-hour (deltas only) -------------------------


def _sorted(snaps: Sequence[Snap]) -> list[Snap]:
    return sorted(snaps, key=lambda s: s.captured_at)


def vph_series(snaps: Sequence[Snap]) -> list[VphPoint]:
    """Views-per-hour between each consecutive snapshot pair (pairs with a
    non-positive elapsed time are skipped)."""
    ordered = _sorted(snaps)
    out: list[VphPoint] = []
    for a, b in zip(ordered, ordered[1:]):
        hours = (b.captured_at - a.captured_at).total_seconds() / 3600.0
        if hours <= 0:
            continue
        out.append(VphPoint(a.captured_at, b.captured_at, hours, (b.views - a.views) / hours))
    return out


def current_vph(snaps: Sequence[Snap], *, window_hours: float = 24.0) -> float | None:
    """VPH over the trailing `window_hours`, anchored at the latest snapshot: it is
    compared against the newest snapshot at or before `latest − window_hours`
    (falling back to the earliest snapshot if none is that old). `None` when fewer
    than two snapshots exist — VPH is never fabricated from totals."""
    ordered = _sorted(snaps)
    if len(ordered) < 2:
        return None
    latest = ordered[-1]
    threshold = latest.captured_at - timedelta(hours=window_hours)
    ref = ordered[0]
    for s in ordered[:-1]:
        if s.captured_at <= threshold:
            ref = s
    hours = (latest.captured_at - ref.captured_at).total_seconds() / 3600.0
    if hours <= 0:
        return None
    return (latest.views - ref.views) / hours


def window_vph(
    snaps: Sequence[Snap], start: datetime, end: datetime
) -> float | None:
    """Average VPH over the snapshots observed within [start, end]: the delta
    between the first and last snapshot in the window over their elapsed hours.
    `None` when fewer than two snapshots fall inside the window."""
    inside = [s for s in _sorted(snaps) if start <= s.captured_at <= end]
    if len(inside) < 2:
        return None
    hours = (inside[-1].captured_at - inside[0].captured_at).total_seconds() / 3600.0
    if hours <= 0:
        return None
    return (inside[-1].views - inside[0].views) / hours


# ------------------------- resurgence -------------------------


def resurgence(
    snaps: Sequence[Snap],
    *,
    published_at: datetime | None,
    now: datetime,
    min_age_days: int,
    k: float,
    baseline_days: int,
    min_vph: float,
    window_hours: float = 24.0,
) -> Resurgence:
    """Flag an OLD video (age >= `min_age_days`) whose current VPH is at least
    `k ×` its recent trailing-average VPH. The recent baseline is the average VPH
    over the `baseline_days` ending one window before now, so it excludes the
    current spike. `min_vph` guards a ~0 baseline from producing a spurious hit."""
    current = current_vph(snaps, window_hours=window_hours)
    base_end = now - timedelta(hours=window_hours)
    base_start = base_end - timedelta(days=baseline_days)
    recent_avg = window_vph(snaps, base_start, base_end)

    if published_at is None:
        return Resurgence(False, None, current, recent_avg)
    age_days = (now - published_at).total_seconds() / 86400.0
    if age_days < min_age_days or current is None or current < min_vph:
        return Resurgence(False, None if recent_avg in (None, 0) else current / recent_avg,
                          current, recent_avg)
    if recent_avg is None or recent_avg <= 0:
        # Dormant then spiking: a real resurgence, but the ratio is undefined.
        return Resurgence(True, None, current, recent_avg)
    ratio = current / recent_avg
    return Resurgence(ratio >= k, ratio, current, recent_avg)


# ------------------------- packaging impact -------------------------


def packaging_impact(
    snaps: Sequence[Snap],
    *,
    detected_at: datetime,
    now: datetime,
    window_days: int = 7,
) -> Impact:
    """Mean VPH in the `window_days` before vs after a packaging change. The
    "after" side is computed only once the window has fully elapsed."""
    delta = timedelta(days=window_days)
    before = window_vph(snaps, detected_at - delta, detected_at)
    after = None
    if now >= detected_at + delta:
        after = window_vph(snaps, detected_at, detected_at + delta)
    return Impact(before, after)


# ------------------------- engagement & cadence -------------------------


def engagement_rates(views: int, likes: int, comments: int) -> Engagement:
    if views <= 0:
        return Engagement(None, None)
    return Engagement(likes / views, comments / views)


def cadence(videos: Sequence[VideoMeta], *, now: datetime) -> Cadence:
    """Descriptive upload cadence: uploads/week over the observed history and the
    distribution of publish weekday/hour, plus median duration per format."""
    pubs = sorted(v.published_at for v in videos if v.published_at is not None)
    by_weekday: dict[int, int] = {}
    by_hour: dict[int, int] = {}
    for ts in pubs:
        by_weekday[ts.weekday()] = by_weekday.get(ts.weekday(), 0) + 1
        by_hour[ts.hour] = by_hour.get(ts.hour, 0) + 1

    if pubs:
        span_days = max((now - pubs[0]).total_seconds() / 86400.0, 7.0)
        uploads_per_week = len(pubs) / (span_days / 7.0)
    else:
        uploads_per_week = 0.0

    duration_by_format: dict[str, dict[str, float]] = {}
    for fmt, is_short in (("short", True), ("long", False)):
        durations = [v.duration_s for v in videos if v.is_short == is_short]
        med = median(durations)
        if med is not None:
            duration_by_format[fmt] = {"median_s": med, "count": len(durations)}

    return Cadence(uploads_per_week, by_weekday, by_hour, duration_by_format)
