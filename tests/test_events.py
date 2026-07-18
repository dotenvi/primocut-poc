"""Tests for the append-only event log: the anti-flapping `outlier_crossed`
emission, comment collection as its consumer, project-badge attribution on read,
the seen/resolved state and the feed being O(limit) rather than a snapshot scan.

The YouTube API and thumbnail fetch are fully faked (no network). The band basis
is `fixed_multiple` so a target's band is a clean function of its view count
against a median-1000 peer set: relevant >= 3000, strong >= 6000, exceptional
>= 10000.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from primocut.api.app import create_app, get_db
from primocut.config import Settings
from primocut.db import competitors as crepo
from primocut.db import events as erepo
from primocut.db.models import EventRow, VideoSnapshotRow
from primocut.db.session import Database
from primocut.worker.event_emission import emit_outlier_events

NOW = datetime(2026, 7, 14, 12, 0, 0, tzinfo=timezone.utc)


def _db(tmp_path) -> Database:
    db = Database(f"sqlite:///{tmp_path / 't.db'}")
    db.init()
    return db


def _settings(tmp_path, **kw) -> Settings:
    kw.setdefault("band_basis", "fixed_multiple")
    return Settings(db_path=tmp_path / "t.db", youtube_api_key="K", **kw)


def _seed_channel_with_peers(db, cid="UCc", *, project_name="Niche", n_peers=12, peer_views=1000):
    """A channel whose peers sit at `peer_views` (median baseline), linked to a
    project, plus a scored target `v1` (no snapshot yet)."""
    with db.session() as s:
        p = crepo.create_project(s, project_name)
        crepo.upsert_channel(s, channel_id=cid, uploads_playlist_id=f"UU{cid}")
        crepo.link_channel(s, project_id=p.id, channel_id=cid)
        for i in range(n_peers):
            crepo.upsert_video(
                s, video_id=f"{cid}p{i}", channel_id=cid, title=f"Peer {i}",
                published_at=NOW - timedelta(days=40 + i), duration_s=600,
            )
            crepo.add_video_snapshot(
                s, video_id=f"{cid}p{i}", captured_at=NOW - timedelta(days=1),
                views=peer_views, likes=1, comments=1,
            )
        crepo.upsert_video(
            s, video_id="v1", channel_id=cid, title="Target",
            published_at=NOW - timedelta(days=30), duration_s=600,
        )
        return p.id


def _set_views(db, video_id, views, *, at):
    """Append a snapshot so `latest_views` sees `views` for the target."""
    with db.session() as s:
        crepo.add_video_snapshot(s, video_id=video_id, captured_at=at, views=views, likes=1, comments=1)


def _events(db, **where) -> list[EventRow]:
    from sqlalchemy import select

    with db.session() as s:
        stmt = select(EventRow)
        for k, v in where.items():
            stmt = stmt.where(getattr(EventRow, k) == v)
        return list(s.execute(stmt.order_by(EventRow.id)).scalars())


# ------------------------- anti-flapping -------------------------


def test_oscillating_around_the_cut_emits_exactly_one_event(tmp_path):
    """A video flickering across the p90/3x cut records the crossing once."""
    db = _db(tmp_path)
    _seed_channel_with_peers(db)
    settings = _settings(tmp_path)

    # crosses (3x=relevant) → drops below → crosses again, each its own cycle.
    for i, views in enumerate([3000, 2000, 3500, 2500, 3200]):
        _set_views(db, "v1", views, at=NOW + timedelta(hours=i))
        emit_outlier_events(["UCc"], settings=settings, database=db, now=NOW + timedelta(hours=i))

    crossings = _events(db, type="outlier_crossed", video_id="v1")
    assert len(crossings) == 1
    assert crossings[0].payload["band"] == "relevant"


def test_climbing_the_band_ladder_emits_one_event_per_rung(tmp_path):
    """relevant → strong → exceptional records three crossings, one per band."""
    db = _db(tmp_path)
    _seed_channel_with_peers(db)
    settings = _settings(tmp_path)

    for i, views in enumerate([3000, 6000, 10_000]):
        _set_views(db, "v1", views, at=NOW + timedelta(hours=i))
        emit_outlier_events(["UCc"], settings=settings, database=db, now=NOW + timedelta(hours=i))

    bands = [e.payload["band"] for e in _events(db, type="outlier_crossed", video_id="v1")]
    assert bands == ["relevant", "strong", "exceptional"]


def test_a_step_up_after_a_drop_still_emits(tmp_path):
    """After relevant then a drop, jumping to strong is a real step up → emits."""
    db = _db(tmp_path)
    _seed_channel_with_peers(db)
    settings = _settings(tmp_path)

    for i, views in enumerate([3000, 1000, 6000]):  # relevant, none, strong
        _set_views(db, "v1", views, at=NOW + timedelta(hours=i))
        emit_outlier_events(["UCc"], settings=settings, database=db, now=NOW + timedelta(hours=i))

    bands = [e.payload["band"] for e in _events(db, type="outlier_crossed", video_id="v1")]
    assert bands == ["relevant", "strong"]


def test_provisional_crossing_is_flagged_in_the_payload(tmp_path):
    """A < 7-day video that crosses emits with provisional=true."""
    db = _db(tmp_path)
    _seed_channel_with_peers(db)
    with db.session() as s:  # make the target young
        crepo.upsert_video(s, video_id="v1", channel_id="UCc", title="Fresh",
                           published_at=NOW - timedelta(days=2), duration_s=600)
    _set_views(db, "v1", 3000, at=NOW)
    emit_outlier_events(["UCc"], settings=_settings(tmp_path), database=db, now=NOW)

    crossing = _events(db, type="outlier_crossed", video_id="v1")[0]
    assert crossing.payload["provisional"] is True
    assert crossing.payload["band"] == "relevant"


# ------------------------- project attribution (badges) -------------------------


def test_a_channel_in_two_projects_is_one_row_with_two_badges(tmp_path):
    """A niche event is written once (project_id NULL) and rendered with a badge
    per project the channel belongs to — never duplicated."""
    db = _db(tmp_path)
    p1 = _seed_channel_with_peers(db, project_name="Niche A")
    with db.session() as s:  # link the same channel into a second project
        p2 = crepo.create_project(s, "Niche B")
        crepo.link_channel(s, project_id=p2.id, channel_id="UCc")

    _set_views(db, "v1", 3000, at=NOW)
    emit_outlier_events(["UCc"], settings=_settings(tmp_path), database=db, now=NOW)

    # Exactly one row, project_id NULL (it is a channel fact).
    rows = _events(db, type="outlier_crossed")
    assert len(rows) == 1 and rows[0].project_id is None

    client = _client(db)
    body = client.get("/api/events?category=niche").json()
    assert len(body) == 1
    badges = sorted(b["name"] for b in body[0]["projects"])
    assert badges == ["Niche A", "Niche B"]
    assert body[0]["video_id"] == "v1"
    # The feed is self-descriptive: the card names the video, resolved on read
    # (the payload never stored a title). The channel key is present too.
    assert body[0]["title"] == "Target"
    assert "channel_title" in body[0]


def test_the_feed_can_be_scoped_to_a_single_project(tmp_path):
    db = _db(tmp_path)
    _seed_channel_with_peers(db, cid="UCa", project_name="A")
    p_b = _seed_channel_with_peers(db, cid="UCb", project_name="B")
    # give each channel its own target
    with db.session() as s:
        crepo.upsert_video(s, video_id="vA", channel_id="UCa", title="A",
                           published_at=NOW - timedelta(days=30), duration_s=600)
        crepo.upsert_video(s, video_id="vB", channel_id="UCb", title="B",
                           published_at=NOW - timedelta(days=30), duration_s=600)
    _set_views(db, "vA", 3000, at=NOW)
    _set_views(db, "vB", 3000, at=NOW)
    emit_outlier_events(["UCa", "UCb"], settings=_settings(tmp_path), database=db, now=NOW)

    client = _client(db)
    scoped = client.get(f"/api/events?category=niche&project_id={p_b}").json()
    assert {e["channel_id"] for e in scoped} == {"UCb"}


# ------------------------- feed is O(limit), not a snapshot scan -------------------------


def test_feed_reads_the_event_log_not_the_snapshot_series(tmp_path):
    """The feed's cost is the number of events returned, independent of how many
    view snapshots the videos accumulated."""
    db = _db(tmp_path)
    _seed_channel_with_peers(db)
    settings = _settings(tmp_path)
    _set_views(db, "v1", 3000, at=NOW)
    emit_outlier_events(["UCc"], settings=settings, database=db, now=NOW)

    # Pile on many more snapshots AFTER the crossing was recorded.
    for i in range(200):
        _set_views(db, "v1", 3000 + i, at=NOW + timedelta(hours=i + 1))

    with db.session() as s:
        n_snaps = len(list(s.execute(
            __import__("sqlalchemy").select(VideoSnapshotRow)
        ).scalars()))
        rows = erepo.list_niche_events(s, limit=50)
    assert n_snaps > 200          # the series is large
    assert len(rows) == 1         # ...but the feed returned exactly the events


# ------------------------- seen / alerts / activity -------------------------


def test_seen_marks_niche_events_and_never_ops(tmp_path):
    db = _db(tmp_path)
    now = NOW.replace(tzinfo=None)
    with db.session() as s:
        niche = erepo.add_event(s, category="niche", type="outlier_crossed",
                                occurred_at=now, channel_id="UCc", video_id="v1")
        ops = erepo.add_event(s, category="ops", type="pipeline_failed",
                              severity="error", occurred_at=now, project_id=1)
        niche_id, ops_id = niche.id, ops.id

    client = _client(db)
    r = client.post("/api/events/seen", json={"ids": [niche_id, ops_id]})
    assert r.status_code == 200 and r.json()["marked"] == 1  # only the niche one

    rows = {e.id: e for e in _events(db)}
    assert rows[niche_id].seen_at is not None
    assert rows[ops_id].seen_at is None  # ops never marked seen


def test_seen_until_marks_everything_up_to_a_timestamp(tmp_path):
    db = _db(tmp_path)
    with db.session() as s:
        for i in range(3):
            erepo.add_event(s, category="niche", type="outlier_crossed",
                            occurred_at=NOW.replace(tzinfo=None) + timedelta(hours=i),
                            channel_id="UCc", video_id=f"v{i}")

    client = _client(db)
    cutoff = (NOW + timedelta(hours=1)).isoformat()
    r = client.post("/api/events/seen", json={"until": cutoff})
    assert r.json()["marked"] == 2  # hours 0 and 1, not hour 2


def test_alerts_are_open_ops_events_only(tmp_path):
    db = _db(tmp_path)
    now = NOW.replace(tzinfo=None)
    with db.session() as s:
        erepo.add_event(s, category="ops", type="pipeline_failed", severity="error",
                        occurred_at=now, project_id=1)
        erepo.add_event(s, category="ops", type="quota_exhausted", severity="error",
                        occurred_at=now, project_id=1, resolved_at=now)  # resolved
        erepo.add_event(s, category="niche", type="outlier_crossed", occurred_at=now,
                        channel_id="UCc", video_id="v1")

    client = _client(db)
    alerts = client.get("/api/alerts").json()
    assert [a["type"] for a in alerts] == ["pipeline_failed"]


def test_activity_paginates_and_reports_quota(tmp_path):
    db = _db(tmp_path)
    now = NOW.replace(tzinfo=None)
    with db.session() as s:
        erepo.add_event(s, category="ops", type=erepo.CYCLE_DONE, occurred_at=now,
                        payload={"quota_units": 7}, resolved_at=now)
        erepo.add_event(s, category="ops", type=erepo.CYCLE_DONE, occurred_at=now,
                        payload={"quota_units": 5}, resolved_at=now)
        erepo.add_event(s, category="niche", type="outlier_crossed", occurred_at=now,
                        channel_id="UCc", video_id="v1")

    client = _client(db)
    body = client.get("/api/activity").json()
    assert len(body["events"]) == 3
    assert body["quota_units"] == 12  # summed across the cycle_done records


# ------------------------- comment collection consumes the event -------------------------


def test_comment_collection_fires_once_per_crossing_event(tmp_path):
    """The crossing event drives the collection: one `outlier_crossed`, one fetch.
    A second cycle with no new event issues no fetch (collect-once)."""
    from primocut.worker.comment_collection import collect_outlier_comments

    class FakeYT:
        def __init__(self):
            self.calls = []

        def list_top_comments(self, video_id, *, pages=2):
            self.calls.append(video_id)
            return []

    db = _db(tmp_path)
    _seed_channel_with_peers(db)
    settings = _settings(tmp_path)
    yt = FakeYT()
    now = NOW.replace(tzinfo=None)

    # No crossing yet → no event → no fetch.
    collect_outlier_comments(["UCc"], settings=settings, database=db, client=yt, now=now)
    assert yt.calls == []

    # Cross the threshold: one event is emitted, and collection fetches once.
    _set_views(db, "v1", 3000, at=NOW)
    emit_outlier_events(["UCc"], settings=settings, database=db, now=NOW)
    collect_outlier_comments(["UCc"], settings=settings, database=db, client=yt, now=now)
    assert yt.calls == ["v1"]

    # Re-running with no new crossing does not re-fetch (comments_state is done).
    collect_outlier_comments(["UCc"], settings=settings, database=db, client=yt, now=now)
    assert yt.calls == ["v1"]


# ------------------------- end-to-end through the real cycle -------------------------


def test_snapshot_cycle_emits_cycle_done_and_packaging_changed(tmp_path):
    """Wiring check: a real cycle records the born-resolved cycle_done (quota) and
    a packaging_changed event when a title changes."""
    import hashlib

    from primocut.youtube.client import ChannelStats, VideoInfo

    class FakeThumbs:
        def __call__(self, url):
            return url.encode()

    class FakeYT:
        def __init__(self):
            self.channel_stats = {"UCc": ChannelStats("UCc", 10, 100, 5)}
            self.uploads = {"UUUCc": []}
            self.videos = {}
            self.units_used = 0

        def get_channels_stats(self, ids):
            self.units_used += 1
            return [self.channel_stats[c] for c in ids if c in self.channel_stats]

        def list_uploads_video_ids(self, playlist_id, *, limit=15):
            self.units_used += 1
            return self.uploads.get(playlist_id, [])[:limit]

        def list_videos(self, video_ids):
            if video_ids:
                self.units_used += 1
            return [self.videos[v] for v in video_ids if v in self.videos]

        def list_top_comments(self, video_id, *, pages=2):
            return []

    from primocut.worker.snapshot_jobs import snapshot_cycle

    db = _db(tmp_path)
    with db.session() as s:
        p = crepo.create_project(s, "Niche")
        crepo.upsert_channel(s, channel_id="UCc", uploads_playlist_id="UUUCc")
        crepo.link_channel(s, project_id=p.id, channel_id="UCc")
        crepo.upsert_video(s, video_id="v1", channel_id="UCc", title="Old title",
                           published_at=NOW - timedelta(days=5), duration_s=600,
                           thumbnail_url="http://t/v1.jpg",
                           thumbnail_hash=hashlib.sha256(b"http://t/v1.jpg").hexdigest())

    yt = FakeYT()
    yt.videos["v1"] = VideoInfo(
        video_id="v1", title="New title", published_at=NOW - timedelta(days=5),
        duration_s=600, thumbnail_url="http://t/v1.jpg",
        view_count=100, like_count=1, comment_count=1,
    )

    res = snapshot_cycle("all", settings=_settings(tmp_path), database=db, client=yt,
                         thumbnail_fetcher=FakeThumbs(), now=NOW)

    cycle_done = _events(db, type=erepo.CYCLE_DONE)
    assert len(cycle_done) == 1
    assert cycle_done[0].payload["quota_units"] == res["quota_units"]
    assert cycle_done[0].resolved_at is not None  # never an open alert

    changed = _events(db, type="packaging_changed")
    assert len(changed) == 1 and changed[0].payload["field"] == "title"
    assert changed[0].payload["new_value"] == "New title"

    # The born-resolved cycle_done stays out of /api/alerts.
    client = _client(db)
    assert client.get("/api/alerts").json() == []


def _client(db) -> TestClient:
    app = create_app()
    app.dependency_overrides[get_db] = lambda: db
    return TestClient(app)
