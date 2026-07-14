"""Tests for the continuous-collection cycle (YouTube API + thumbnail download
fully faked, no network).

Covers frequency selection (all vs recent), snapshot recording, new-video
detection, title change, thumbnail change under an UNCHANGED URL (byte hash),
per-channel isolation, orphan exclusion and per-cycle quota accounting.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone

from primocut.config import Settings
from primocut.db import competitors as crepo
from primocut.db.models import ChannelSnapshotRow, PackagingChangeRow, VideoSnapshotRow
from primocut.db.session import Database
from primocut.worker.snapshot_jobs import snapshot_cycle
from primocut.youtube.client import ChannelStats, VideoInfo, YouTubeError

NOW = datetime(2026, 7, 14, 12, 0, 0, tzinfo=timezone.utc)


def _db(tmp_path) -> Database:
    db = Database(f"sqlite:///{tmp_path / 't.db'}")
    db.init()
    return db


def _settings(tmp_path, **kw):
    return Settings(db_path=tmp_path / "t.db", youtube_api_key="K", **kw)


def _h(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class FakeYouTube:
    def __init__(self):
        self.channel_stats: dict[str, ChannelStats] = {}
        self.uploads: dict[str, list[str]] = {}       # playlist_id -> top video ids
        self.videos: dict[str, VideoInfo] = {}        # video_id -> current metadata
        self.fail_channels: set[str] = set()
        self.units_used = 0

    def get_channels_stats(self, ids):
        self.units_used += 1
        out = []
        for cid in ids:
            if cid in self.fail_channels:
                raise YouTubeError(f"boom {cid}")
            if cid in self.channel_stats:
                out.append(self.channel_stats[cid])
        return out

    def list_uploads_video_ids(self, playlist_id, *, limit=15):
        self.units_used += 1
        return self.uploads.get(playlist_id, [])[:limit]

    def list_videos(self, video_ids):
        if video_ids:
            self.units_used += (len(video_ids) + 49) // 50
        return [self.videos[v] for v in video_ids if v in self.videos]


class FakeThumbs:
    """Thumbnail byte source keyed by URL (default: deterministic from the URL)."""

    def __init__(self):
        self.content: dict[str, bytes] = {}

    def __call__(self, url: str) -> bytes:
        return self.content.get(url, url.encode())


def _vinfo(vid, *, title="T", thumb="", views=100, likes=10, comments=1, published=None):
    return VideoInfo(
        video_id=vid, title=title, published_at=published, duration_s=600,
        thumbnail_url=thumb, view_count=views, like_count=likes, comment_count=comments,
    )


def _seed_channel(db, cid="UCc", *, active=True, uploads=None):
    with db.session() as s:
        crepo.upsert_channel(s, channel_id=cid, uploads_playlist_id=f"UU{cid}", active=active)
    return f"UU{cid}"


def _seed_video(db, cid, vid, **kw):
    with db.session() as s:
        crepo.upsert_video(s, video_id=vid, channel_id=cid, **kw)


def _rows(db, model, **where):
    from sqlalchemy import select

    with db.session() as s:
        stmt = select(model)
        for k, v in where.items():
            stmt = stmt.where(getattr(model, k) == v)
        return list(s.execute(stmt).scalars())


# ------------------------- frequency selection -------------------------


def test_recent_scope_selects_only_young_videos(tmp_path):
    db = _db(tmp_path)
    _seed_channel(db)
    _seed_video(db, "UCc", "vold", title="old", published_at=NOW - timedelta(days=30))
    _seed_video(db, "UCc", "vnew", title="new", published_at=NOW - timedelta(days=2))
    yt = FakeYouTube()
    yt.videos = {"vold": _vinfo("vold", title="old"), "vnew": _vinfo("vnew", title="new")}

    snapshot_cycle("recent", settings=_settings(tmp_path), database=db, client=yt,
                   thumbnail_fetcher=FakeThumbs(), now=NOW)

    snapped = {r.video_id for r in _rows(db, VideoSnapshotRow)}
    assert snapped == {"vnew"}  # < 7 days only


def test_all_scope_selects_every_video(tmp_path):
    db = _db(tmp_path)
    _seed_channel(db)
    _seed_video(db, "UCc", "vold", title="old", published_at=NOW - timedelta(days=30))
    _seed_video(db, "UCc", "vnew", title="new", published_at=NOW - timedelta(days=2))
    yt = FakeYouTube()
    yt.videos = {"vold": _vinfo("vold", title="old"), "vnew": _vinfo("vnew", title="new")}

    snapshot_cycle("all", settings=_settings(tmp_path), database=db, client=yt,
                   thumbnail_fetcher=FakeThumbs(), now=NOW)

    snapped = {r.video_id for r in _rows(db, VideoSnapshotRow)}
    assert snapped == {"vold", "vnew"}


# ------------------------- snapshot recording -------------------------


def test_records_channel_and_video_snapshots(tmp_path):
    db = _db(tmp_path)
    _seed_channel(db)
    _seed_video(db, "UCc", "v1", title="T", published_at=NOW - timedelta(days=1))
    yt = FakeYouTube()
    yt.channel_stats = {"UCc": ChannelStats("UCc", subscribers=1200, total_views=99, video_count=42)}
    yt.videos = {"v1": _vinfo("v1", views=500, likes=40, comments=3)}

    snapshot_cycle("all", settings=_settings(tmp_path), database=db, client=yt,
                   thumbnail_fetcher=FakeThumbs(), now=NOW)

    ch = _rows(db, ChannelSnapshotRow)
    assert len(ch) == 1 and ch[0].subscribers == 1200 and ch[0].video_count == 42
    vs = _rows(db, VideoSnapshotRow)
    assert len(vs) == 1 and (vs[0].views, vs[0].likes, vs[0].comments) == (500, 40, 3)
    # captured_at aligns across the cycle.
    assert ch[0].captured_at.replace(tzinfo=timezone.utc) == NOW


# ------------------------- new-video detection -------------------------


def test_new_video_detected_added_and_snapshotted(tmp_path):
    db = _db(tmp_path)
    playlist = _seed_channel(db)
    _seed_video(db, "UCc", "vknown", title="known", published_at=NOW - timedelta(days=3))
    yt = FakeYouTube()
    yt.uploads = {playlist: ["vfresh", "vknown"]}  # vfresh is new at the top
    yt.videos = {
        "vknown": _vinfo("vknown"),
        "vfresh": _vinfo("vfresh", title="fresh", published=NOW - timedelta(hours=1)),
    }

    snapshot_cycle("all", settings=_settings(tmp_path), database=db, client=yt,
                   thumbnail_fetcher=FakeThumbs(), now=NOW)

    with db.session() as s:
        ids = set(crepo.list_channel_video_ids(s, "UCc"))
    assert "vfresh" in ids  # added to competitor_videos
    assert "vfresh" in {r.video_id for r in _rows(db, VideoSnapshotRow)}  # snapshotted


# ------------------------- packaging changes -------------------------


def test_title_change_recorded(tmp_path):
    db = _db(tmp_path)
    _seed_channel(db)
    # Seed a matching thumbnail hash so only the title differs.
    url = "https://i.ytimg.com/vi/v1/hq.jpg"
    _seed_video(db, "UCc", "v1", title="Old title", thumbnail_url=url,
                thumbnail_hash=_h(url.encode()), published_at=NOW - timedelta(days=1))
    yt = FakeYouTube()
    yt.videos = {"v1": _vinfo("v1", title="New title", thumb=url)}

    snapshot_cycle("all", settings=_settings(tmp_path), database=db, client=yt,
                   thumbnail_fetcher=FakeThumbs(), now=NOW)

    changes = _rows(db, PackagingChangeRow)
    assert len(changes) == 1
    assert changes[0].field == "title"
    assert (changes[0].old_value, changes[0].new_value) == ("Old title", "New title")
    with db.session() as s:
        assert crepo.list_channel_videos(s, "UCc")[0].title == "New title"


def test_thumbnail_change_detected_with_unchanged_url(tmp_path):
    db = _db(tmp_path)
    _seed_channel(db)
    url = "https://i.ytimg.com/vi/v1/hq.jpg"  # stays the same across the change
    _seed_video(db, "UCc", "v1", title="T", thumbnail_url=url,
                thumbnail_hash=_h(b"old-bytes"), published_at=NOW - timedelta(days=1))
    thumbs = FakeThumbs()
    thumbs.content[url] = b"new-bytes"  # same URL, different image bytes
    yt = FakeYouTube()
    yt.videos = {"v1": _vinfo("v1", title="T", thumb=url)}

    snapshot_cycle("all", settings=_settings(tmp_path), database=db, client=yt,
                   thumbnail_fetcher=thumbs, now=NOW)

    changes = _rows(db, PackagingChangeRow)
    assert len(changes) == 1 and changes[0].field == "thumbnail"
    assert changes[0].old_value == _h(b"old-bytes")
    assert changes[0].new_value == _h(b"new-bytes")
    with db.session() as s:
        v = crepo.list_channel_videos(s, "UCc")[0]
        assert v.thumbnail_url == url          # URL unchanged
        assert v.thumbnail_hash == _h(b"new-bytes")  # hash updated


# ------------------------- isolation / orphans / quota -------------------------


def test_per_channel_isolation(tmp_path):
    db = _db(tmp_path)
    _seed_channel(db, "UCbad")
    _seed_channel(db, "UCok")
    _seed_video(db, "UCbad", "vbad", published_at=NOW - timedelta(days=1))
    _seed_video(db, "UCok", "vok", published_at=NOW - timedelta(days=1))
    yt = FakeYouTube()
    yt.fail_channels = {"UCbad"}  # its channel-stats call raises
    yt.videos = {"vbad": _vinfo("vbad"), "vok": _vinfo("vok")}

    res = snapshot_cycle("all", settings=_settings(tmp_path), database=db, client=yt,
                         thumbnail_fetcher=FakeThumbs(), now=NOW)

    snapped = {r.video_id for r in _rows(db, VideoSnapshotRow)}
    assert snapped == {"vok"}      # the good channel still collected
    assert res["channels"] == 1    # only UCok completed; UCbad was isolated


def test_orphan_channel_excluded(tmp_path):
    db = _db(tmp_path)
    _seed_channel(db, "UCorph", active=False)  # deactivated → out of the cycle
    _seed_video(db, "UCorph", "v1", published_at=NOW - timedelta(days=1))
    yt = FakeYouTube()
    yt.videos = {"v1": _vinfo("v1")}

    res = snapshot_cycle("all", settings=_settings(tmp_path), database=db, client=yt,
                         thumbnail_fetcher=FakeThumbs(), now=NOW)

    assert res["channels"] == 0
    assert _rows(db, VideoSnapshotRow) == []


def test_per_cycle_quota_accounting(tmp_path):
    db = _db(tmp_path)
    playlist = _seed_channel(db)
    _seed_video(db, "UCc", "v1", published_at=NOW - timedelta(days=1))
    yt = FakeYouTube()
    yt.channel_stats = {"UCc": ChannelStats("UCc", 1, 1, 1)}
    yt.uploads = {playlist: ["v1"]}
    yt.videos = {"v1": _vinfo("v1")}

    res = snapshot_cycle("all", settings=_settings(tmp_path), database=db, client=yt,
                         thumbnail_fetcher=FakeThumbs(), now=NOW)

    # 1 (channel stats) + 1 (uploads scan) + 1 (videos.list batch) = 3 units.
    assert res["quota_units"] == 3
    assert yt.units_used == 3
