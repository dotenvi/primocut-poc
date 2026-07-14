"""Unit tests for the pure metrics core (guideline 10 methodology).

No DB, no network — the methodology is exercised directly on synthetic values.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from primocut.metrics.core import (
    Snap,
    VideoMeta,
    cadence,
    channel_baseline,
    current_vph,
    engagement_rates,
    outlier_score,
    packaging_impact,
    parse_brackets,
    resurgence,
    vph_series,
)

NOW = datetime(2026, 7, 14, 12, 0, 0, tzinfo=timezone.utc)
BRACKETS = [3.0, 6.0, 10.0]


# ------------------------- baseline: median, robust to a viral -------------------------


def test_baseline_is_median_robust_to_one_viral():
    # Ten typical videos around 1000 views + one 1,000,000 viral in the window.
    peers = [900, 950, 1000, 1000, 1050, 1100, 1000, 980, 1020, 1000, 1_000_000]
    base = channel_baseline(peers, min_sample=10)
    assert base is not None
    # The mean would be ~92k (wrecked by the viral); the median stays ~1000.
    assert 950 <= base.median <= 1050
    # The viral scores as a massive outlier against that robust baseline.
    viral = outlier_score(1_000_000, base, brackets=BRACKETS, provisional=False)
    assert viral.bracket == "10x" and viral.score > 900


def test_insufficient_data_below_min_sample():
    base = channel_baseline([1000] * 9, min_sample=10)  # only 9 peers
    assert base is None
    score = outlier_score(5000, base, brackets=BRACKETS, provisional=False)
    assert score.status == "insufficient_data"
    assert score.score is None and score.bracket is None


# ------------------------- Shorts vs long-form separation -------------------------


def test_shorts_and_longform_use_separate_baselines():
    short_base = channel_baseline([500] * 10, min_sample=10)   # low Short baseline
    long_base = channel_baseline([10_000] * 10, min_sample=10)  # high long baseline
    views = 5000
    short_score = outlier_score(views, short_base, brackets=BRACKETS, provisional=False)
    long_score = outlier_score(views, long_base, brackets=BRACKETS, provisional=False)
    # Same view count, different verdict — because the baselines differ by format.
    assert short_score.score == 10.0 and short_score.bracket == "10x"
    assert long_score.score == 0.5 and long_score.bracket is None


# ------------------------- provisional vs definitive -------------------------


def test_provisional_flag_for_young_video():
    base = channel_baseline([1000] * 10, min_sample=10)
    young = outlier_score(4000, base, brackets=BRACKETS, provisional=True)
    mature = outlier_score(4000, base, brackets=BRACKETS, provisional=False)
    assert young.provisional and young.status == "provisional"
    assert (not mature.provisional) and mature.status == "ok"
    assert young.score == mature.score == 4.0 and young.bracket == "3x"


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


def test_parse_brackets():
    assert parse_brackets("3,6,10") == [3.0, 6.0, 10.0]
    assert parse_brackets("10, 3 ,6") == [3.0, 6.0, 10.0]
