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
from primocut.metrics.enrichment import SOURCE_TITLE

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


def test_llm_patterns_appear_once_both_sides_are_enriched(tmp_path):
    # The whole point of enriching the mature population and not just the outliers:
    # a title attribute present in 80% of outliers and 30% of control can only
    # separate when BOTH groups carry the title_llm source. Enriching outliers
    # alone leaves the control at N=0 and the comparison with nothing to compare.
    db = _db(tmp_path)
    _channel(db, "UCc")
    outliers = [f"out{i}" for i in range(20)]
    control = [f"ctl{i}" for i in range(40)]
    hook = set(outliers[:16]) | set(control[:12])  # 80% of outliers, 30% of control

    for i, vid in enumerate(outliers):  # 4x the median → banded
        _video(db, "UCc", vid, views=4000, days_ago=20 + i,
               title=(f"Really? {vid}" if vid in hook else f"plain {vid}"))
    for i, vid in enumerate(control):   # at the median → band-less control
        _video(db, "UCc", vid, views=1000, days_ago=20 + i,
               title=(f"Really? {vid}" if vid in hook else f"plain {vid}"))

    all_ids = outliers + control
    with db.session() as s:
        refresh_syntactic_attributes(s, all_ids)
        for vid in all_ids:  # enrich BOTH sides with a title_llm attribute
            crepo.upsert_video_attributes(
                s, video_id=vid, source=SOURCE_TITLE,
                attributes={"title_hook": vid in hook}, content_hash=f"h-{vid}",
            )
        proj = crepo.create_project(s, "P")
        crepo.link_channel(s, proj.id, "UCc")
        pid = proj.id

    with db.session() as s:
        dash = service.project_dashboard(s, pid, settings=FIXED_BIG, now=NOW)

    long = dash["patterns"]["long"]
    planted = next(p for p in long if p["attribute"] == "title_hook")
    assert planted["source"] == "title_llm"
    assert planted["n_outlier"] == 20 and planted["n_control"] == 40  # control is NOT 0
    assert planted["verdict"] == "positive" and planted["lift"] > 1
    # syntactic separates the same split, untouched — the deterministic source still works.
    assert any(p["attribute"] == "has_question" and p["source"] == "syntactic" for p in long)


# ------------------------- channel spread distribution -------------------------


def test_channel_distribution_buckets_the_window_and_marks_the_tail(tmp_path):
    # A skewed channel: 20 typical videos + one viral. The spread is a histogram of
    # the window's view counts; the viral pins the max and sits alone in the tail.
    db = _db(tmp_path)
    _channel(db, "UCspread")
    for i in range(20):
        _video(db, "UCspread", f"v{i}", views=1000, is_short=False, days_ago=20 + i)
    _video(db, "UCspread", "viral", views=100_000, is_short=False, days_ago=25)

    with db.session() as s:
        dist = service.channel_distribution(s, "UCspread", fmt="long", settings=PCT_SETTINGS, now=NOW)

    assert dist["channel_id"] == "UCspread" and dist["format"] == "long"
    assert dist["count"] == 21 and dist["max"] == 100_000.0
    assert sum(dist["buckets"]) == 21
    assert dist["buckets"][0] == 20 and dist["buckets"][-1] == 1
    assert len(dist["bucket_edges"]) == len(dist["buckets"]) + 1
    # Percentile thresholds travel along so the same strip can shade the bands.
    assert dist["thresholds"] is not None and dist["median"] == 1000


def test_channel_distribution_separates_formats(tmp_path):
    db = _db(tmp_path)
    _channel(db, "UCfmt")
    for i in range(12):
        _video(db, "UCfmt", f"long{i}", views=5000, is_short=False, days_ago=20 + i)
    for i in range(12):
        _video(db, "UCfmt", f"short{i}", views=300, is_short=True, days_ago=20 + i)

    with db.session() as s:
        long_d = service.channel_distribution(s, "UCfmt", fmt="long", settings=PCT_SETTINGS, now=NOW)
        short_d = service.channel_distribution(s, "UCfmt", fmt="short", settings=PCT_SETTINGS, now=NOW)

    assert long_d["count"] == 12 and long_d["max"] == 5000.0
    assert short_d["count"] == 12 and short_d["max"] == 300.0


def test_channel_distribution_unknown_channel_is_none(tmp_path):
    db = _db(tmp_path)
    with db.session() as s:
        assert service.channel_distribution(s, "UCnope", fmt="long", settings=PCT_SETTINGS, now=NOW) is None


def test_distribution_batch_has_no_nplus1_and_no_snapshot_scan(tmp_path):
    # The Radar renders many cards. The batch must stay flat in the number of
    # channels — never grow with videos-per-channel, and never scan snapshots.
    db = _db(tmp_path)
    for c in range(3):
        cid = f"UCbatch{c}"
        _channel(db, cid)
        for i in range(30):  # many videos per channel
            _video(db, cid, f"{cid}_v{i}", views=1000 + i, is_short=False, days_ago=20 + i)

    ids = ["UCbatch0", "UCbatch1", "UCbatch2"]
    engine = db.engine  # count the SQL statements the batch actually issues
    seen = []
    from sqlalchemy import event as sa_event

    def _count(conn, cursor, statement, params, context, executemany):
        seen.append(statement)

    sa_event.listen(engine, "before_cursor_execute", _count)
    try:
        with db.session() as s:
            out = service.channel_distributions(s, ids, fmt="long", settings=PCT_SETTINGS, now=NOW)
    finally:
        sa_event.remove(engine, "before_cursor_execute", _count)

    assert set(out) == set(ids)
    for cid in ids:
        assert out[cid]["count"] == 30
    # No per-video snapshot query: the only snapshot read is the single batched
    # latest_views. So query volume is bounded by channels, not videos.
    assert not any("FROM video_snapshots" in q and "WHERE" in q and "IN (" not in q for q in seen), seen
    # Flat in channels: 3 get_channel + 3 list_channel_videos + 1 latest_views = 7,
    # regardless of the 30 videos each. Allow a small margin, but far below 3*30.
    assert len(seen) <= 12, f"{len(seen)} queries — batch is not flat: {seen}"


# ------------------------- distribution endpoints -------------------------


def _pct_client(db) -> TestClient:
    app = create_app()
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_settings_dep] = lambda: PCT_SETTINGS
    return TestClient(app)


def test_distribution_endpoint_single_and_batch(tmp_path):
    db = _db(tmp_path)
    _channel(db, "UCa")
    for i in range(12):
        _video(db, "UCa", f"a{i}", views=1000, is_short=False, days_ago=20 + i)
    _channel(db, "UCb")
    for i in range(12):
        _video(db, "UCb", f"b{i}", views=2000, is_short=False, days_ago=20 + i)
    c = _pct_client(db)

    single = c.get("/api/channels/UCa/distribution?format=long")
    assert single.status_code == 200
    body = single.json()
    assert body["channel_id"] == "UCa" and body["count"] == 12
    assert len(body["bucket_edges"]) == len(body["buckets"]) + 1

    batch = c.get("/api/channels/distribution?channel_ids=UCa,UCb&format=long")
    assert batch.status_code == 200
    m = batch.json()
    assert set(m) == {"UCa", "UCb"} and m["UCa"]["count"] == 12 and m["UCb"]["count"] == 12


def test_distribution_endpoint_validates_format_and_channel(tmp_path):
    db = _db(tmp_path)
    _channel(db, "UCa")
    c = _pct_client(db)
    assert c.get("/api/channels/UCa/distribution?format=bogus").status_code == 400
    assert c.get("/api/channels/UCmissing/distribution?format=long").status_code == 404
    # The batch skips unknown ids rather than 404-ing the whole page.
    assert c.get("/api/channels/distribution?channel_ids=UCmissing&format=long").json() == {}
