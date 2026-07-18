"""Unit tests for the pure metrics core (guideline 10 methodology).

No DB, no network — the methodology is exercised directly on synthetic values.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from primocut.metrics.core import (
    Snap,
    VideoMeta,
    band_ruler,
    cadence,
    current_vph,
    engagement_rates,
    nearest_rank_percentile,
    outlier_score,
    packaging_impact,
    parse_cutoffs,
    resurgence,
    vph_series,
    wilson_interval,
)

NOW = datetime(2026, 7, 14, 12, 0, 0, tzinfo=timezone.utc)
FIXED = parse_cutoffs("3,6,10")       # legacy fixed-multiple cutoffs
PCT = parse_cutoffs("90,97,99")       # percentile cutoffs


def _fixed_ruler(peers):
    return band_ruler(peers, basis="fixed_multiple", cutoffs=FIXED, min_sample=10)


# ------------------------- baseline: median, robust to a viral -------------------------


def test_baseline_is_median_robust_to_one_viral():
    # Ten typical videos around 1000 views + one 1,000,000 viral in the window.
    peers = [900, 950, 1000, 1000, 1050, 1100, 1000, 980, 1020, 1000, 1_000_000]
    ruler = _fixed_ruler(peers)
    assert ruler is not None
    # The mean would be ~92k (wrecked by the viral); the median stays ~1000.
    assert 950 <= ruler.median <= 1050
    # The viral scores as a massive outlier against that robust baseline.
    viral = outlier_score(1_000_000, ruler, provisional=False)
    assert viral.band == "exceptional" and viral.median_multiple > 900


def test_insufficient_sample_below_min_sample():
    ruler = _fixed_ruler([1000] * 9)  # only 9 peers
    assert ruler is None
    score = outlier_score(5000, ruler, provisional=False)
    assert score.status == "insufficient_sample"
    assert score.median_multiple is None and score.band is None


# ------------------------- Shorts vs long-form separation -------------------------


def test_shorts_and_longform_use_separate_baselines():
    short_ruler = _fixed_ruler([500] * 10)     # low Short baseline
    long_ruler = _fixed_ruler([10_000] * 10)   # high long baseline
    views = 5000
    short_score = outlier_score(views, short_ruler, provisional=False)
    long_score = outlier_score(views, long_ruler, provisional=False)
    # Same view count, different verdict — because the baselines differ by format.
    assert short_score.median_multiple == 10.0 and short_score.band == "exceptional"
    assert long_score.median_multiple == 0.5 and long_score.band is None


# ------------------------- provisional vs definitive -------------------------


def test_provisional_flag_for_young_video():
    ruler = _fixed_ruler([1000] * 10)
    young = outlier_score(4000, ruler, provisional=True)
    mature = outlier_score(4000, ruler, provisional=False)
    assert young.provisional and young.status == "provisional"
    assert (not mature.provisional) and mature.status == "ok"
    assert young.median_multiple == mature.median_multiple == 4.0 and young.band == "relevant"


# ------------------------- percentile bands (the default basis) -------------------------


def test_percentile_bands_classify_by_channel_tail():
    # 1..100: median 50.5; nearest-rank cutoffs land on the 90th/97th/99th values.
    peers = list(range(1, 101))
    ruler = band_ruler(peers, basis="percentile", cutoffs=PCT, min_sample=10)
    assert ruler.thresholds == {"relevant": 90.0, "strong": 97.0, "exceptional": 99.0}
    below = outlier_score(50, ruler, provisional=False)
    relevant = outlier_score(95, ruler, provisional=False)
    exceptional = outlier_score(100, ruler, provisional=False)
    assert below.band is None
    # Percentile basis carries the cutoff percentile as evidence; multiple stays too.
    assert relevant.band == "relevant" and relevant.percentile == 90.0
    assert exceptional.band == "exceptional" and exceptional.percentile == 99.0
    assert relevant.band_basis == "percentile"
    # Fixed-multiple basis has no percentile evidence.
    assert outlier_score(95, _fixed_ruler(peers), provisional=False).percentile is None


def test_nearest_rank_collapses_top_cutoffs_at_min_sample():
    # With n=10, ceil(0.97·10)=ceil(0.99·10)=10 → p97 and p99 are both the maximum.
    vals = list(range(1, 11))  # 1..10
    assert nearest_rank_percentile(vals, 90) == 9.0
    assert nearest_rank_percentile(vals, 97) == 10.0
    assert nearest_rank_percentile(vals, 99) == 10.0
    assert nearest_rank_percentile(vals, 0) == 1.0
    assert nearest_rank_percentile([], 90) is None


def test_wilson_interval_keeps_width_at_extremes():
    # Wald would give width 0 at p=1 (falsely certain); Wilson stays an interval.
    lo, hi = wilson_interval(3, 3, 1.96)
    assert hi - lo > 0 and lo < 1.0 and hi <= 1.0
    lo0, hi0 = wilson_interval(0, 3, 1.96)
    assert hi0 > 0 and lo0 == 0.0
    # An empty group carries no information — the whole [0, 1].
    assert wilson_interval(0, 0, 1.96) == (0.0, 1.0)


# ------------------------- VPH strictly from deltas -------------------------


def test_current_vph_from_two_snapshots():
    snaps = [Snap(NOW - timedelta(hours=24), 100), Snap(NOW, 340)]
    assert current_vph(snaps, window_hours=24) == 10.0  # 240 views / 24 h


def test_vph_none_with_single_snapshot():
    assert current_vph([Snap(NOW, 500)], window_hours=24) is None


def test_vph_series_pairwise_deltas():
    snaps = [Snap(NOW - timedelta(hours=2), 0), Snap(NOW - timedelta(hours=1), 10), Snap(NOW, 40)]
    pts = vph_series(snaps)
    assert [p.vph for p in pts] == [10.0, 30.0]


# ------------------------- resurgence -------------------------


def test_resurgence_old_video_current_vph_exceeds_recent_average():
    # 8-day series: low baseline VPH (~10/h) then a spike in the last 24 h (~100/h).
    snaps = []
    views = 0
    for day in range(8, 0, -1):
        snaps.append(Snap(NOW - timedelta(days=day), views))
        views += 240  # ~10 views/hour across each prior day
    snaps.append(Snap(NOW, views + 2400))  # last 24 h: 2400 views = 100/h
    res = resurgence(
        snaps, published_at=NOW - timedelta(days=120), now=NOW,
        min_age_days=30, k=3.0, baseline_days=7, min_vph=1.0,
    )
    assert res.is_resurgent
    assert res.current_vph is not None and res.recent_avg_vph is not None
    assert res.ratio is not None and res.ratio >= 3.0


def test_no_resurgence_for_young_video():
    snaps = [Snap(NOW - timedelta(hours=24), 0), Snap(NOW, 10_000)]
    res = resurgence(
        snaps, published_at=NOW - timedelta(days=3), now=NOW,
        min_age_days=30, k=3.0, baseline_days=7, min_vph=1.0,
    )
    assert not res.is_resurgent


# ------------------------- packaging impact -------------------------


def test_packaging_impact_before_and_after_when_elapsed():
    detected = NOW - timedelta(days=8)  # window fully elapsed
    snaps = [
        Snap(detected - timedelta(days=7), 0),
        Snap(detected, 700),          # before: 700 views / 7 d ≈ 4.17/h
        Snap(detected + timedelta(days=7), 4900),  # after: 4200 / 7 d = 25/h
    ]
    impact = packaging_impact(snaps, detected_at=detected, now=NOW, window_days=7)
    assert impact.vph_before is not None and impact.vph_after is not None
    assert impact.vph_after > impact.vph_before


def test_packaging_impact_after_none_until_window_elapsed():
    detected = NOW - timedelta(days=2)  # only 2 days elapsed of a 7-day window
    snaps = [Snap(detected - timedelta(days=7), 0), Snap(detected, 700), Snap(NOW, 900)]
    impact = packaging_impact(snaps, detected_at=detected, now=NOW, window_days=7)
    assert impact.vph_after is None


# ------------------------- engagement & cadence -------------------------


def test_engagement_rates_and_zero_views_guard():
    e = engagement_rates(1000, 80, 6)
    assert e.like_rate == 0.08 and e.comment_rate == 0.006
    assert engagement_rates(0, 0, 0).like_rate is None


def test_cadence_uploads_per_week_and_distributions():
    vids = [
        VideoMeta(NOW - timedelta(days=7), False, 600),
        VideoMeta(NOW - timedelta(days=14), False, 720),
        VideoMeta(NOW - timedelta(days=21), True, 45),
    ]
    c = cadence(vids, now=NOW)
    assert c.uploads_per_week > 0
    assert sum(c.by_weekday.values()) == 3 and sum(c.by_hour.values()) == 3
    assert c.duration_by_format["short"]["median_s"] == 45
    assert c.duration_by_format["long"]["median_s"] == 660


def test_parse_cutoffs():
    assert parse_cutoffs("90,97,99") == {"relevant": 90.0, "strong": 97.0, "exceptional": 99.0}
    # Sorted onto the ascending band names regardless of input order.
    assert parse_cutoffs("10, 3 ,6") == {"relevant": 3.0, "strong": 6.0, "exceptional": 10.0}
    for bad in ("3,6", "3,6,10,12", ""):
        try:
            parse_cutoffs(bad)
        except ValueError:
            continue
        raise AssertionError(f"expected ValueError for {bad!r}")
