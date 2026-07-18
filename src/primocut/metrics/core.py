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
  same-format videos yields **insufficient_sample** rather than a misleading number.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from statistics import median as _median
from typing import Sequence

# Band names in ascending severity. A video's band is the highest one whose
# threshold it reaches; the three names mean the same thing in either basis
# (percentile or fixed-multiple) — see `band_ruler`.
BAND_NAMES: tuple[str, str, str] = ("relevant", "strong", "exceptional")


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
class BandRuler:
    """The per-channel, per-format ruler that classifies a video into a band.

    `median` and `sample_size` describe the rolling-window peer set. `basis` is
    "percentile" or "fixed_multiple"; `cutoffs` maps each band name to its ruler
    value (percentiles 90/97/99, or multiples 3/6/10). `thresholds` maps each
    band name to the ABSOLUTE view count a video must reach for that band — the
    percentile view value, or `median × multiple`.
    """

    median: float
    sample_size: int
    basis: str
    cutoffs: dict[str, float]
    thresholds: dict[str, float]


@dataclass(frozen=True)
class OutlierScore:
    # `median_multiple` (views / median) stays readable ("20.6× the median") but
    # no longer classifies; `band` is the ordinal verdict. `percentile` is the
    # cutoff percentile of the reached band (percentile basis only), `None` in the
    # legacy fixed-multiple basis. `band_cutoffs` travels alongside so a consumer
    # (e.g. the M2 event log) is self-descriptive: a later config change cannot
    # retroactively rewrite the meaning of a recorded band.
    median_multiple: float | None
    band: str | None  # "relevant" | "strong" | "exceptional" | None
    percentile: float | None
    band_basis: str
    band_cutoffs: dict[str, float] = field(default_factory=dict)
    provisional: bool = False
    baseline: float | None = None
    sample_size: int = 0
    status: str = ""  # "ok" | "provisional" | "insufficient_sample"


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


# Number of bars in the "channel spread" strip. Fixed so the same distribution
# renders identically wherever it appears (Radar card, video detail).
SPREAD_BUCKETS = 28


@dataclass(frozen=True)
class SpreadHistogram:
    """A view-count histogram of a channel-format window: `counts[i]` peers fall
    in `[edges[i], edges[i+1])`. `edges` has `len(counts) + 1` entries spanning
    `[0, max]`, so a consumer maps any view count to a bar by its edges. `n` is
    the window size; `min`/`max` the observed view range."""

    counts: list[int]
    edges: list[float]
    min: float
    max: float
    n: int


# ------------------------- helpers -------------------------


def median(values: Sequence[float]) -> float | None:
    """Median of a non-empty sequence (None if empty). Robust to outliers — this
    is why baselines never use the mean."""
    vals = [v for v in values if v is not None]
    if not vals:
        return None
    return float(_median(vals))


def parse_cutoffs(spec: str) -> dict[str, float]:
    """Parse three ascending, comma-separated cutoffs into the band map.

    Percentile basis expects "90,97,99"; fixed-multiple basis expects "3,6,10".
    The three values are sorted and zipped onto (relevant, strong, exceptional).
    """
    vals = sorted(float(part) for part in spec.split(",") if part.strip())
    if len(vals) != len(BAND_NAMES):
        raise ValueError(f"expected {len(BAND_NAMES)} cutoffs, got {spec!r}")
    return dict(zip(BAND_NAMES, vals))


def nearest_rank_percentile(values: Sequence[float], q: float) -> float | None:
    """The q-th percentile (q in 0..100) by the nearest-rank method — the value
    at 1-based rank ceil(q/100 · n), no interpolation. `None` for an empty input.

    Nearest-rank (rather than linear interpolation) is deliberate: with the small
    samples we work with (>= 10), interpolation would invent cutoffs strictly
    between two observed view counts — values no real video in the window ever
    had — whereas nearest-rank always returns an actually-observed data point and
    is stable and deterministic as the window shifts by one video.
    """
    vals = sorted(float(v) for v in values if v is not None)
    if not vals:
        return None
    rank = math.ceil((q / 100.0) * len(vals))
    rank = max(1, min(rank, len(vals)))
    return vals[rank - 1]


def spread_histogram(
    values: Sequence[float], *, buckets: int = SPREAD_BUCKETS
) -> SpreadHistogram | None:
    """Bucket a window's view counts into `buckets` equal-width bars over `[0, max]`.

    Linear over `[0, max]` (not `[min, max]`) on purpose: the story a channel
    spread tells is exactly that the typical videos cluster low and a rare one
    sits far in the tail — an absolute origin keeps that shape honest and keeps
    two videos of the same channel comparable. `None` for an empty input.
    """
    vals = [float(v) for v in values if v is not None]
    if not vals:
        return None
    lo, hi = 0.0, max(vals)
    if hi <= lo:  # a window of all-zero views: one unit wide so edges are valid
        hi = 1.0
    width = (hi - lo) / buckets
    edges = [lo + width * i for i in range(buckets + 1)]
    counts = [0] * buckets
    for v in vals:
        idx = int((v - lo) / width)
        idx = max(0, min(idx, buckets - 1))  # the max value lands in the last bar
        counts[idx] += 1
    return SpreadHistogram(
        counts=counts, edges=edges, min=min(vals), max=max(vals), n=len(vals)
    )


def wilson_interval(hits: int, n: int, z: float) -> tuple[float, float]:
    """The Wilson score interval for a proportion `hits / n` at confidence `z`.

    Wilson, not Wald (p ± z·√(p(1−p)/n)): Wald's width collapses to zero at p=0
    and p=1 — frequent with rare attributes on a small n — falsely asserting
    absolute certainty; Wilson keeps a sensible interval there. `n == 0` yields
    the whole [0, 1] (no information), so an empty group never separates.
    """
    if n <= 0:
        return (0.0, 1.0)
    phat = hits / n
    z2 = z * z
    denom = 1.0 + z2 / n
    center = (phat + z2 / (2.0 * n)) / denom
    margin = (z / denom) * math.sqrt(phat * (1.0 - phat) / n + z2 / (4.0 * n * n))
    return (max(0.0, center - margin), min(1.0, center + margin))


# ------------------------- outlier score -------------------------


def band_ruler(
    peer_views: Sequence[int],
    *,
    basis: str,
    cutoffs: dict[str, float],
    min_sample: int,
) -> BandRuler | None:
    """Build the classification ruler from a same-format, already-windowed peer
    set. `None` when there are fewer than `min_sample` peers, or the median is
    non-positive (no basis for a multiple) — both surface as
    "insufficient_sample" downstream.

    Percentile basis: each band threshold is the nearest-rank percentile of the
    peer views. Fixed-multiple basis (legacy): each threshold is median × the
    multiple. Bands remain a pure per-channel metric — the project never enters.
    """
    vals = [int(v) for v in peer_views]
    if len(vals) < min_sample:
        return None
    med = median(vals)
    if med is None or med <= 0:
        return None
    if basis == "percentile":
        thresholds = {name: nearest_rank_percentile(vals, q) for name, q in cutoffs.items()}
    elif basis == "fixed_multiple":
        thresholds = {name: med * mult for name, mult in cutoffs.items()}
    else:
        raise ValueError(f"unknown band basis {basis!r}")
    return BandRuler(median=med, sample_size=len(vals), basis=basis,
                     cutoffs=dict(cutoffs), thresholds=thresholds)


def outlier_score(
    views: int,
    ruler: BandRuler | None,
    *,
    provisional: bool,
) -> OutlierScore:
    """Classify `views` against a channel-format ruler. `median_multiple` is still
    computed and carried (readable), but the `band` — the highest cutoff the
    views reach — is the verdict. A young video is flagged provisional; an absent
    ruler yields insufficient_sample."""
    if ruler is None:
        return OutlierScore(
            median_multiple=None, band=None, percentile=None, band_basis="",
            band_cutoffs={}, provisional=provisional, baseline=None,
            sample_size=0, status="insufficient_sample",
        )
    band: str | None = None
    for name in BAND_NAMES:  # ascending — the last one reached wins
        if views >= ruler.thresholds[name]:
            band = name
    percentile = ruler.cutoffs[band] if band is not None and ruler.basis == "percentile" else None
    return OutlierScore(
        median_multiple=views / ruler.median,
        band=band,
        percentile=percentile,
        band_basis=ruler.basis,
        band_cutoffs=dict(ruler.cutoffs),
        provisional=provisional,
        baseline=ruler.median,
        sample_size=ruler.sample_size,
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
