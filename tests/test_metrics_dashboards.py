"""Tests for the metrics dashboards (service + API), including the guarantee that
a channel shared by two projects yields identical channel metrics while each
project report is scoped to its own channels.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from primocut.api.app import create_app, get_db, get_settings_dep
from primocut.config import Settings
from primocut.db import competitors as crepo
from primocut.db.session import Database
from primocut.metrics import service
from primocut.metrics.attributes import refresh_syntactic_attributes

NOW = datetime(2026, 7, 14, 12, 0, 0, tzinfo=timezone.utc)
SETTINGS = Settings(youtube_api_key="K")


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
    assert top["video_id"] == "viral" and top["bracket"] == "10x" and top["score"] == 10.0


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
    assert data["outliers"]["long"][0]["score"] == 10.0
    assert data["outliers"]["short"][0]["score"] == 10.0


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

    # The shared video's score is identical across the two project reports.
    sa = next(o for o in dash_a["outliers"]["long"] if o["video_id"] == "viral")
    sb = next(o for o in dash_b["outliers"]["long"] if o["video_id"] == "viral")
    assert sa["score"] == sb["score"] == 10.0


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
    # 20 outliers (5x) all sharing a "?" title — a minority, so the median stays
    # at the baseline (median is robust); a well-supported pattern (>= 15).
    for i in range(20):
        _video(db, "UCp", f"out{i}", views=5000, days_ago=20 + i, title=f"Will this work? {i}")
    # 40 baseline videos hold the median at ~1000, so the outliers score 5x.
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
    assert q is not None and q["outlier_occurrences"] >= 15
    assert q["lift"] is not None and q["lift"] > 1.0
