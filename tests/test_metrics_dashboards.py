"""Tests for the metrics dashboards (service + API), including the guarantee that
a channel shared by two projects yields identical channel metrics while each
project report is scoped to its own channels.
"""

from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from primocut.api.app import create_app, get_db, get_settings_dep
from primocut.config import Settings
from primocut.db import competitors as crepo
from primocut.db.session import Database
from primocut.metrics import service
from primocut.metrics.attributes import refresh_syntactic_attributes

NOW = datetime(2026, 7, 14, 12, 0, 0, tzinfo=timezone.utc)
# The uniform synthetic fixtures below (all peers at one view count + a viral) are
# calibrated to the legacy fixed-multiple semantics, so structural tests run in
# that basis; the percentile default is exercised by the distribution tests, which
# use a skewed sample. A wide window lets every seeded video enter the ruler.
SETTINGS = Settings(youtube_api_key="K", band_basis="fixed_multiple")
PCT_SETTINGS = Settings(youtube_api_key="K", outlier_window_videos=500)
FIXED_BIG = Settings(youtube_api_key="K", band_basis="fixed_multiple", outlier_window_videos=500)


def _db(tmp_path) -> Database:
    db = Database(f"sqlite:///{tmp_path / 't.db'}")
    db.init()
    return db


def _channel(db, cid):
    with db.session() as s:
        crepo.upsert_channel(s, channel_id=cid, title=f"Title {cid}", uploads_playlist_id=f"UU{cid}")
        crepo.add_channel_snapshot(
            s, channel_id=cid, captured_at=NOW, subscribers=1000, total_views=9, video_count=5
        )


def _video(db, cid, vid, *, views, is_short=False, days_ago=30, title="A normal title",
           likes=0, comments=0, extra_snap=None):
    with db.session() as s:
        crepo.upsert_video(
            s, video_id=vid, channel_id=cid, title=title, is_short=is_short,
            published_at=NOW - timedelta(days=days_ago),
        )
        if extra_snap is not None:
            crepo.add_video_snapshot(s, video_id=vid, captured_at=NOW - timedelta(hours=24),
                                     views=extra_snap, likes=0, comments=0)
        crepo.add_video_snapshot(s, video_id=vid, captured_at=NOW, views=views,
                                 likes=likes, comments=comments)


def _seed_baseline_channel(db, cid, *, base_views=1000, count=12, is_short=False):
    """A channel with `count` same-format videos at ~base_views (enough for a baseline)."""
    _channel(db, cid)
    for i in range(count):
        _video(db, cid, f"{cid}_v{i}", views=base_views, is_short=is_short, days_ago=20 + i)


def _client(db) -> TestClient:
    app = create_app()
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_settings_dep] = lambda: SETTINGS
    return TestClient(app)


# ------------------------- channel dashboard -------------------------


def test_channel_dashboard_ranks_outliers_and_separates_formats(tmp_path):
    db = _db(tmp_path)
    _seed_baseline_channel(db, "UClong", base_views=1000, count=12, is_short=False)
    _seed_baseline_channel(db, "UClong", base_views=1000)  # no-op guard (idempotent channel)
    # A long viral (10x) and a Short baseline that must not contaminate the long one.
    _video(db, "UClong", "viral", views=10_000, is_short=False, days_ago=25, title="HUGE VIRAL HIT")
    for i in range(12):
        _video(db, "UClong", f"short{i}", views=500, is_short=True, days_ago=10 + i)

    with db.session() as s:
        data = service.channel_dashboard(s, "UClong", settings=SETTINGS, now=NOW)

    assert data["baselines"]["long"]["median"] == 1000
    assert data["baselines"]["short"]["median"] == 500  # separate baseline
    top = data["outliers"]["long"][0]
    assert top["video_id"] == "viral" and top["band"] == "exceptional" and top["median_multiple"] == 10.0


def test_channel_dashboard_segregates_engagement_by_format(tmp_path):
    # Shorts and long-form monetise, target and behave differently, so a single
    # median across both describes neither. 12 long videos at a 4% like rate and
    # 12 Shorts at 10% must be reported apart, never blended into ~7%.
    db = _db(tmp_path)
    _channel(db, "UCmix")
    for i in range(12):
        _video(db, "UCmix", f"long{i}", views=1000, likes=40, comments=10,
               is_short=False, days_ago=20 + i)
    for i in range(12):
        _video(db, "UCmix", f"short{i}", views=1000, likes=100, comments=5,
               is_short=True, days_ago=20 + i)

    with db.session() as s:
        data = service.channel_dashboard(s, "UCmix", settings=SETTINGS, now=NOW)

    assert data["engagement"]["long"]["like_rate_median"] == 0.04
    assert data["engagement"]["short"]["like_rate_median"] == 0.10
    assert data["engagement"]["long"]["comment_rate_median"] == 0.01
    assert data["engagement"]["short"]["comment_rate_median"] == 0.005


def test_channel_dashboard_segregates_outliers_by_format(tmp_path):
    db = _db(tmp_path)
    _seed_baseline_channel(db, "UCsplit", base_views=1000, count=12, is_short=False)
    _video(db, "UCsplit", "viral_long", views=10_000, is_short=False, days_ago=25)
    for i in range(12):
        _video(db, "UCsplit", f"short{i}", views=500, is_short=True, days_ago=10 + i)
    _video(db, "UCsplit", "viral_short", views=5_000, is_short=True, days_ago=25)

    with db.session() as s:
        data = service.channel_dashboard(s, "UCsplit", settings=SETTINGS, now=NOW)

    assert [o["video_id"] for o in data["outliers"]["long"]] == ["viral_long"]
    assert [o["video_id"] for o in data["outliers"]["short"]] == ["viral_short"]
    # Each is scored against its own format's baseline: 10000/1000 and 5000/500.
    assert data["outliers"]["long"][0]["median_multiple"] == 10.0
    assert data["outliers"]["short"][0]["median_multiple"] == 10.0


def test_channel_dashboard_404(tmp_path):
    db = _db(tmp_path)
    client = _client(db)
    assert client.get("/api/channels/nope/dashboard").status_code == 404


# ------------------------- video report -------------------------


def test_video_report_vph_and_provisional(tmp_path):
    db = _db(tmp_path)
    _seed_baseline_channel(db, "UCc", base_views=1000, count=12)
    # A young (provisional) video with two snapshots 24 h apart → measurable VPH.
    _video(db, "UCc", "young", views=340, days_ago=3, extra_snap=100)

    with db.session() as s:
        rep = service.video_report(s, "UCc", "young", settings=SETTINGS, now=NOW)

    assert rep["vph"]["current"] == 10.0  # (340-100)/24
    assert rep["outlier"]["provisional"] is True
    assert rep["outlier"]["status"] == "provisional"
    assert rep["outlier"]["format"] == "long"


def test_video_report_404_and_channel_mismatch(tmp_path):
    db = _db(tmp_path)
    _channel(db, "UCc")
    _video(db, "UCc", "v1", views=100)
    client = _client(db)
    assert client.get("/api/channels/UCc/videos/missing").status_code == 404
    # video exists but under a different channel → 404
    assert client.get("/api/channels/UCother/videos/v1").status_code == 404


# ------------------------- shared-channel correctness -------------------------


def test_shared_channel_identical_metrics_scoped_reports(tmp_path):
    db = _db(tmp_path)
    # Shared channel with a clear 10x viral.
    _seed_baseline_channel(db, "UCshared", base_views=1000, count=12)
    _video(db, "UCshared", "viral", views=10_000, days_ago=25, title="Shared viral")
    # A distinct channel per project.
    _seed_baseline_channel(db, "UConlyA", base_views=1000, count=12)
    _video(db, "UConlyA", "viralA", views=8_000, days_ago=25)
    _seed_baseline_channel(db, "UConlyB", base_views=1000, count=12)
    _video(db, "UConlyB", "viralB", views=9_000, days_ago=25)

    with db.session() as s:
        a = crepo.create_project(s, "A")
        b = crepo.create_project(s, "B")
        for cid in ("UCshared", "UConlyA"):
            crepo.link_channel(s, a.id, cid)
        for cid in ("UCshared", "UConlyB"):
            crepo.link_channel(s, b.id, cid)
        pa, pb = a.id, b.id

    with db.session() as s:
        # Channel-level metrics are project-independent: identical object.
        d1 = service.channel_dashboard(s, "UCshared", settings=SETTINGS, now=NOW)
        d2 = service.channel_dashboard(s, "UCshared", settings=SETTINGS, now=NOW)
        assert d1 == d2

        dash_a = service.project_dashboard(s, pa, settings=SETTINGS, now=NOW)
        dash_b = service.project_dashboard(s, pb, settings=SETTINGS, now=NOW)

    ids_a = {o["video_id"] for o in dash_a["outliers"]["long"]}
    ids_b = {o["video_id"] for o in dash_b["outliers"]["long"]}
    # The shared viral appears in BOTH; the exclusive ones stay scoped.
    assert "viral" in ids_a and "viral" in ids_b
    assert "viralA" in ids_a and "viralA" not in ids_b
    assert "viralB" in ids_b and "viralB" not in ids_a

    # The shared video's band is identical across the two project reports — it is a
    # channel metric, independent of which project scopes the report.
    sa = next(o for o in dash_a["outliers"]["long"] if o["video_id"] == "viral")
    sb = next(o for o in dash_b["outliers"]["long"] if o["video_id"] == "viral")
    assert sa["median_multiple"] == sb["median_multiple"] == 10.0
    assert sa["band"] == sb["band"] == "exceptional"


def test_project_dashboard_segregates_outliers_by_format(tmp_path):
    db = _db(tmp_path)
    _seed_baseline_channel(db, "UCpx", base_views=1000, count=12, is_short=False)
    _video(db, "UCpx", "viral_long", views=10_000, is_short=False, days_ago=25)
    for i in range(12):
        _video(db, "UCpx", f"s{i}", views=500, is_short=True, days_ago=10 + i)
    _video(db, "UCpx", "viral_short", views=5_000, is_short=True, days_ago=25)
    with db.session() as s:
        proj = crepo.create_project(s, "P")
        crepo.link_channel(s, proj.id, "UCpx")
        pid = proj.id

    with db.session() as s:
        dash = service.project_dashboard(s, pid, settings=SETTINGS, now=NOW)

    assert [o["video_id"] for o in dash["outliers"]["long"]] == ["viral_long"]
    assert [o["video_id"] for o in dash["outliers"]["short"]] == ["viral_short"]


def test_project_dashboard_404(tmp_path):
    db = _db(tmp_path)
    client = _client(db)
    assert client.get("/api/projects/999/dashboard").status_code == 404


# ------------------------- pattern guard via the project dashboard -------------------------


def test_project_dashboard_pattern_guard(tmp_path):
    db = _db(tmp_path)
    _channel(db, "UCp")
    # 20 outliers (5x → banded) all sharing a "?" title — a minority, so the median
    # stays at the baseline (median is robust). The control is the 40 plain videos.
    for i in range(20):
        _video(db, "UCp", f"out{i}", views=5000, days_ago=20 + i, title=f"Will this work? {i}")
    for i in range(40):
        _video(db, "UCp", f"base{i}", views=1000, days_ago=60 + i, title="plain title")

    with db.session() as s:
        refresh_syntactic_attributes(s, [v.video_id for v in crepo.list_channel_videos(s, "UCp")])
    with db.session() as s:
        proj = crepo.create_project(s, "P")
        crepo.link_channel(s, proj.id, "UCp")
        pid = proj.id

    with db.session() as s:
        dash = service.project_dashboard(s, pid, settings=SETTINGS, now=NOW)

    q = next((p for p in dash["patterns"]["long"] if p["attribute"] == "has_question"), None)
    # The "?" is universal among outliers and absent from control — a positive
    # pattern whose Wilson intervals clear each other.
    assert q is not None and q["verdict"] == "positive"
    assert q["n_outlier"] == 20 and q["n_control"] == 40
    assert q["freq_outlier"] == 1.0 and q["freq_control"] == 0.0
    assert q["ci_outlier"][0] > q["ci_control"][1]


def test_percentile_bands_flag_roughly_the_tail(tmp_path):
    # A skewed (log-normal) channel: percentile bands mark ~10% of videos, whereas
    # the legacy fixed multiple marks far more — the recalibration's whole point.
    db = _db(tmp_path)
    _channel(db, "UCln")
    rng = random.Random(1234)
    n = 200
    for i in range(n):
        views = max(1, int(rng.lognormvariate(6.0, 2.0)))
        # All mature (>= 7 days) and inside the 180-day window.
        _video(db, "UCln", f"v{i}", views=views, days_ago=10 + i * 0.7)

    with db.session() as s:
        pct = service.channel_dashboard(s, "UCln", settings=PCT_SETTINGS, now=NOW)
        fixed = service.channel_dashboard(s, "UCln", settings=FIXED_BIG, now=NOW)

    pct_frac = len(pct["outliers"]["long"]) / n
    fixed_frac = len(fixed["outliers"]["long"]) / n
    assert 0.05 <= pct_frac <= 0.16          # nearest-rank p90 ≈ the top tenth
    assert fixed_frac >= 0.22                 # 3× the median lands mid-distribution
    assert pct_frac < fixed_frac


def test_insufficient_sample_yields_no_band(tmp_path):
    db = _db(tmp_path)
    _channel(db, "UCi")
    for i in range(9):  # below outlier_min_sample (10)
        _video(db, "UCi", f"v{i}", views=1000, days_ago=20 + i)

    with db.session() as s:
        rep = service.video_report(s, "UCi", "v0", settings=PCT_SETTINGS, now=NOW)

    assert rep["outlier"]["status"] == "insufficient_sample"
    assert rep["outlier"]["band"] is None and rep["outlier"]["median_multiple"] is None


def test_provisional_video_is_censored_from_control(tmp_path):
    # A young (< 7 days) video carrying the outlier attribute must NOT count as a
    # non-outlier control: it is an unobserved outcome (censored), and including it
    # would leak an outlier attribute into the control and deflate the lift.
    db = _db(tmp_path)
    _channel(db, "UCc")
    for i in range(15):  # mature outliers (5x), all "?"
        _video(db, "UCc", f"out{i}", views=5000, days_ago=35 + i, title=f"Really? {i}")
    for i in range(15):  # mature control, plain titles
        _video(db, "UCc", f"base{i}", views=1000, days_ago=20 + i, title="plain title")
    # The trap: a 2-day-old video with a "?" title and a non-outlier view count.
    _video(db, "UCc", "young", views=1000, days_ago=2, title="Trap question?")

    with db.session() as s:
        refresh_syntactic_attributes(s, [v.video_id for v in crepo.list_channel_videos(s, "UCc")])
    with db.session() as s:
        proj = crepo.create_project(s, "P")
        crepo.link_channel(s, proj.id, "UCc")
        pid = proj.id

    with db.session() as s:
        dash = service.project_dashboard(s, pid, settings=SETTINGS, now=NOW)

    q = next(p for p in dash["patterns"]["long"] if p["attribute"] == "has_question")
    # Control counts only the 15 mature plain videos — the provisional "?" is out.
    assert q["n_outlier"] == 15 and q["n_control"] == 15
    assert q["freq_control"] == 0.0 and q["verdict"] == "positive"
