"""Tests for channel registration + backfill (YouTube fully faked, no network).

Covers: new-channel backfill of 120 videos with is_short/thumbnail_hash, the
first channel+video snapshots written by the backfill itself, dedup across
projects (2nd project only links, no re-collection), API-error degradation
(never crashes), an unreachable queue, the retry of a channel whose collection
never completed, and quota accounting.
"""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

from primocut.config import Settings
from primocut.db import competitors as crepo
from primocut.db.models import ChannelSnapshotRow, VideoSnapshotRow
from primocut.db.session import Database
from primocut.worker.channel_jobs import backfill_channel
from primocut.worker.channels import detach_channel, register_channel
from primocut.worker.queue import QueueUnavailableError
from primocut.youtube.client import ChannelInfo, ChannelStats, VideoInfo, YouTubeError

NOW = datetime(2026, 7, 15, 12, 0, 0, tzinfo=timezone.utc)


def _db(tmp_path) -> Database:
    db = Database(f"sqlite:///{tmp_path / 't.db'}")
    db.init()
    return db


def _settings(tmp_path, **kw):
    return Settings(db_path=tmp_path / "t.db", youtube_api_key="K", **kw)


class FakeYouTube:
    """A fake YouTubeClient with in-memory quota accounting."""

    def __init__(self, *, channel: ChannelInfo, videos: list[VideoInfo]):
        self._channel = channel
        self._videos = videos
        self.units_used = 0

    def get_channel(self, **kwargs) -> ChannelInfo:
        self.units_used += 1
        return self._channel

    def get_channels_stats(self, channel_ids):
        # 1 unit per batch of 50, mirroring the real client.
        self.units_used += (len(channel_ids) + 49) // 50
        return [
            ChannelStats(
                channel_id=cid, subscribers=1000, total_views=50_000, video_count=7
            )
            for cid in channel_ids
        ]

    def list_uploads_video_ids(self, playlist_id, *, limit=120):
        ids = [v.video_id for v in self._videos][:limit]
        # 1 unit per page of 50, mirroring the real client.
        self.units_used += (len(ids) + 49) // 50
        return ids

    def list_videos(self, video_ids):
        by_id = {v.video_id: v for v in self._videos}
        self.units_used += (len(video_ids) + 49) // 50
        return [by_id[v] for v in video_ids]


class BoomYouTube(FakeYouTube):
    def list_uploads_video_ids(self, *a, **k):
        raise YouTubeError("quota exceeded")


class StatsBoomYouTube(FakeYouTube):
    """Channel statistics fail, the uploads themselves are fine."""

    def get_channels_stats(self, channel_ids):
        raise YouTubeError("channels.list unavailable")


def _channel_info(channel_id="UCchan0000000000000000", uploads="UUchan"):
    return ChannelInfo(
        channel_id=channel_id, title="Chan", handle="@chan", uploads_playlist_id=uploads
    )


def _videos(n=120):
    # Alternate a Short (<= 60s) and a long-form video. The stats ride along in
    # the same videos.list response the real client returns.
    return [
        VideoInfo(
            video_id=f"vid{i:03d}",
            title=f"V{i}",
            published_at=None,
            duration_s=30 if i % 2 == 0 else 600,
            thumbnail_url=f"https://i.ytimg.com/vi/vid{i:03d}/hq.jpg",
            view_count=100 * i,
            like_count=10 * i,
            comment_count=i,
        )
        for i in range(n)
    ]


class _FakeQueue:
    def __init__(self):
        self.calls = []

    def enqueue(self, fn, *args, **kwargs):
        self.calls.append((fn, args, kwargs))
        return SimpleNamespace(id="rqBF")


class _DeadQueue:
    """A queue whose broker is unreachable — mirrors Redis being down."""

    def enqueue(self, fn, *args, **kwargs):
        raise ConnectionError("Error 111 connecting to localhost:6379. Connection refused.")


def _fake_thumb(url: str) -> bytes:
    return url.encode()  # deterministic bytes → deterministic sha256


def _rows(db, model):
    from sqlalchemy import select

    with db.session() as s:
        return list(s.execute(select(model)).scalars())


# ------------------------- registration -------------------------


def test_register_new_channel_links_and_enqueues_backfill(tmp_path):
    db = _db(tmp_path)
    with db.session() as s:
        pid = crepo.create_project(s, "P").id
    queue = _FakeQueue()
    client = FakeYouTube(channel=_channel_info(), videos=_videos())

    res = register_channel(
        pid, "@chan", settings=_settings(tmp_path), database=db, queue=queue, client=client
    )

    assert res["existing"] is False and res["backfill"] == "queued"
    assert queue.calls[0][0] is backfill_channel
    assert queue.calls[0][1] == ("UCchan0000000000000000",)
    with db.session() as s:
        ch = crepo.get_channel(s, "UCchan0000000000000000")
        assert ch is not None and ch.active is True
        assert ch.backfill_state == "queued" and ch.backfill_rq_job_id == "rqBF"


def test_register_existing_channel_dedups_no_backfill(tmp_path):
    db = _db(tmp_path)
    with db.session() as s:
        p1 = crepo.create_project(s, "P1").id
        p2 = crepo.create_project(s, "P2").id
    client = FakeYouTube(channel=_channel_info(), videos=_videos())

    register_channel(p1, "@chan", settings=_settings(tmp_path), database=db,
                     queue=_FakeQueue(), client=client)

    # Second project: the channel already exists → only link, no enqueue.
    queue2 = _FakeQueue()
    res = register_channel(p2, "@chan", settings=_settings(tmp_path), database=db,
                           queue=queue2, client=client)

    assert res["existing"] is True and res["backfill"] == "skipped"
    assert queue2.calls == []  # no re-collection
    with db.session() as s:
        assert crepo.count_channel_projects(s, "UCchan0000000000000000") == 2


def test_register_reactivates_orphaned_channel(tmp_path):
    db = _db(tmp_path)
    with db.session() as s:
        p = crepo.create_project(s, "P").id
        crepo.upsert_channel(s, channel_id="UCchan0000000000000000",
                             uploads_playlist_id="UUchan", active=False)
    client = FakeYouTube(channel=_channel_info(), videos=_videos())

    res = register_channel(p, "@chan", settings=_settings(tmp_path), database=db,
                           queue=_FakeQueue(), client=client)

    assert res["existing"] is True and res["reactivated"] is True
    with db.session() as s:
        assert crepo.get_channel(s, "UCchan0000000000000000").active is True


def test_register_marks_error_when_the_queue_is_unreachable(tmp_path):
    """The channel row is committed before the enqueue, so a broker failure must
    leave a channel reported as failed — never one stuck collecting forever."""
    db = _db(tmp_path)
    with db.session() as s:
        pid = crepo.create_project(s, "P").id
    client = FakeYouTube(channel=_channel_info(), videos=_videos())
    import pytest

    with pytest.raises(QueueUnavailableError):
        register_channel(pid, "@chan", settings=_settings(tmp_path), database=db,
                         queue=_DeadQueue(), client=client)

    with db.session() as s:
        ch = crepo.get_channel(s, "UCchan0000000000000000")
        assert ch.backfill_state == "error"
        assert "6379" in ch.backfill_error  # the broker failure is reported
        assert ch.backfill_rq_job_id is None


def test_register_retries_the_backfill_of_a_never_collected_channel(tmp_path):
    """Global dedup must not strand a channel whose collection never ran: a
    re-registration re-enqueues it instead of reporting it as already collected."""
    db = _db(tmp_path)
    with db.session() as s:
        pid = crepo.create_project(s, "P").id
    client = FakeYouTube(channel=_channel_info(), videos=_videos())
    import pytest

    with pytest.raises(QueueUnavailableError):  # broker down → left in "error"
        register_channel(pid, "@chan", settings=_settings(tmp_path), database=db,
                         queue=_DeadQueue(), client=client)

    queue = _FakeQueue()  # broker back up → same reference, same project
    res = register_channel(pid, "@chan", settings=_settings(tmp_path), database=db,
                           queue=queue, client=client)

    assert res["existing"] is True and res["backfill"] == "queued"
    assert queue.calls[0][0] is backfill_channel
    with db.session() as s:
        ch = crepo.get_channel(s, "UCchan0000000000000000")
        assert ch.backfill_state == "queued" and ch.backfill_rq_job_id == "rqBF"
        assert ch.backfill_error is None  # the previous failure is cleared
        assert crepo.count_channel_projects(s, "UCchan0000000000000000") == 1  # no dup link


def test_register_does_not_retry_an_already_collected_channel(tmp_path):
    """A finished collection is never re-run — that is the point of the dedup."""
    db = _db(tmp_path)
    with db.session() as s:
        pid = crepo.create_project(s, "P").id
        crepo.upsert_channel(s, channel_id="UCchan0000000000000000",
                             uploads_playlist_id="UUchan", backfill_state="done")
    client = FakeYouTube(channel=_channel_info(), videos=_videos())
    queue = _FakeQueue()

    res = register_channel(pid, "@chan", settings=_settings(tmp_path), database=db,
                           queue=queue, client=client)

    assert res["existing"] is True and res["backfill"] == "skipped"
    assert queue.calls == []


def test_register_missing_project_raises(tmp_path):
    db = _db(tmp_path)
    client = FakeYouTube(channel=_channel_info(), videos=_videos())
    import pytest

    with pytest.raises(KeyError):
        register_channel(999, "@chan", settings=_settings(tmp_path), database=db,
                         queue=_FakeQueue(), client=client)


# ------------------------- backfill job -------------------------


def test_backfill_collects_120_with_is_short_and_hash(tmp_path):
    db = _db(tmp_path)
    with db.session() as s:
        crepo.upsert_channel(s, channel_id="UCchan0000000000000000",
                             uploads_playlist_id="UUchan", backfill_state="queued")
    client = FakeYouTube(channel=_channel_info(), videos=_videos(120))

    res = backfill_channel(
        "UCchan0000000000000000", settings=_settings(tmp_path), database=db,
        client=client, thumbnail_fetcher=_fake_thumb,
    )

    assert res["videos"] == 120
    with db.session() as s:
        ch = crepo.get_channel(s, "UCchan0000000000000000")
        assert ch.backfill_state == "done"
        assert ch.backfill_done == 120 and ch.backfill_total == 120
        vids = crepo.list_channel_videos(s, "UCchan0000000000000000")
        assert len(vids) == 120
        shorts = [v for v in vids if v.is_short]
        assert len(shorts) == 60  # every even index is 30s ≤ 60s
        assert all(v.thumbnail_hash for v in vids)  # hash computed from bytes


def test_backfill_quota_accounting(tmp_path):
    db = _db(tmp_path)
    with db.session() as s:
        crepo.upsert_channel(s, channel_id="UCchan0000000000000000",
                             uploads_playlist_id="UUchan")
    client = FakeYouTube(channel=_channel_info(), videos=_videos(120))

    res = backfill_channel(
        "UCchan0000000000000000", settings=_settings(tmp_path), database=db,
        client=client, thumbnail_fetcher=_fake_thumb,
    )
    # 120 videos → 3 playlist pages (3u) + 3 video batches (3u) + the channel
    # statistics behind the first snapshot (1u) = 7 units.
    assert res["quota_units"] == 7
    assert client.units_used == 7


# ------------------------- first snapshot -------------------------


def test_backfill_writes_the_first_video_snapshots(tmp_path):
    """The view/like/comment counts already ride along in the videos.list response
    the backfill makes anyway, so recording them costs no extra quota and leaves
    the dashboard populated before the first scheduled cycle."""
    db = _db(tmp_path)
    with db.session() as s:
        crepo.upsert_channel(s, channel_id="UCchan0000000000000000",
                             uploads_playlist_id="UUchan", backfill_state="queued")
    client = FakeYouTube(channel=_channel_info(), videos=_videos(4))

    backfill_channel(
        "UCchan0000000000000000", settings=_settings(tmp_path), database=db,
        client=client, thumbnail_fetcher=_fake_thumb, now=NOW,
    )

    assert len(_rows(db, VideoSnapshotRow)) == 4  # one per collected video
    with db.session() as s:
        snaps = crepo.list_video_snapshots(s, "vid002")
        assert len(snaps) == 1
        assert (snaps[0].views, snaps[0].likes, snaps[0].comments) == (200, 20, 2)
        assert snaps[0].captured_at.replace(tzinfo=timezone.utc) == NOW


def test_backfill_writes_the_first_channel_snapshot(tmp_path):
    """Without it the dashboard has no subscriber count until the first cron."""
    db = _db(tmp_path)
    with db.session() as s:
        crepo.upsert_channel(s, channel_id="UCchan0000000000000000",
                             uploads_playlist_id="UUchan", backfill_state="queued")
    client = FakeYouTube(channel=_channel_info(), videos=_videos(4))

    backfill_channel(
        "UCchan0000000000000000", settings=_settings(tmp_path), database=db,
        client=client, thumbnail_fetcher=_fake_thumb, now=NOW,
    )

    with db.session() as s:
        snap = crepo.latest_channel_snapshot(s, "UCchan0000000000000000")
        assert snap is not None
        assert snap.subscribers == 1000
        assert snap.total_views == 50_000 and snap.video_count == 7
        assert snap.captured_at.replace(tzinfo=timezone.utc) == NOW


def test_backfill_snapshots_share_one_captured_at(tmp_path):
    """A snapshot is a reading of one instant: the channel row and every video row
    must be comparable, so they carry the same timestamp."""
    db = _db(tmp_path)
    with db.session() as s:
        crepo.upsert_channel(s, channel_id="UCchan0000000000000000",
                             uploads_playlist_id="UUchan", backfill_state="queued")
    client = FakeYouTube(channel=_channel_info(), videos=_videos(4))

    backfill_channel(
        "UCchan0000000000000000", settings=_settings(tmp_path), database=db,
        client=client, thumbnail_fetcher=_fake_thumb,  # no `now` → real clock
    )

    stamps = {r.captured_at for r in _rows(db, VideoSnapshotRow)}
    stamps |= {r.captured_at for r in _rows(db, ChannelSnapshotRow)}
    assert len(stamps) == 1


def test_backfill_degrades_when_channel_stats_fail(tmp_path):
    """The uploads are the point of the backfill: a channels.list failure costs the
    channel snapshot, never the 120 videos nor a `done` state."""
    db = _db(tmp_path)
    with db.session() as s:
        crepo.upsert_channel(s, channel_id="UCchan0000000000000000",
                             uploads_playlist_id="UUchan", backfill_state="queued")
    client = StatsBoomYouTube(channel=_channel_info(), videos=_videos(4))

    res = backfill_channel(
        "UCchan0000000000000000", settings=_settings(tmp_path), database=db,
        client=client, thumbnail_fetcher=_fake_thumb, now=NOW,
    )

    assert res["videos"] == 4 and "error" not in res
    assert len(_rows(db, VideoSnapshotRow)) == 4  # video snapshots still written
    assert _rows(db, ChannelSnapshotRow) == []  # the degraded part
    with db.session() as s:
        ch = crepo.get_channel(s, "UCchan0000000000000000")
        assert ch.backfill_state == "done" and ch.backfill_error is None


def test_backfill_api_error_degrades_without_crashing(tmp_path):
    db = _db(tmp_path)
    with db.session() as s:
        crepo.upsert_channel(s, channel_id="UCchan0000000000000000",
                             uploads_playlist_id="UUchan", backfill_state="queued")
    client = BoomYouTube(channel=_channel_info(), videos=_videos())

    res = backfill_channel(
        "UCchan0000000000000000", settings=_settings(tmp_path), database=db,
        client=client, thumbnail_fetcher=_fake_thumb,
    )

    assert "error" in res  # returned, not raised
    with db.session() as s:
        ch = crepo.get_channel(s, "UCchan0000000000000000")
        assert ch.backfill_state == "error"
        assert "quota exceeded" in (ch.backfill_error or "")


# ------------------------- detach -------------------------


def test_detach_preserves_backfilled_videos(tmp_path):
    db = _db(tmp_path)
    with db.session() as s:
        pid = crepo.create_project(s, "P").id
        crepo.upsert_channel(s, channel_id="UCchan0000000000000000",
                             uploads_playlist_id="UUchan")
        crepo.link_channel(s, pid, "UCchan0000000000000000")
        crepo.upsert_video(s, video_id="v1", channel_id="UCchan0000000000000000")

    res = detach_channel(pid, "UCchan0000000000000000",
                         settings=_settings(tmp_path), database=db)

    assert res["detached"] is True and res["deactivated"] is True
    with db.session() as s:
        assert crepo.count_channel_videos(s, "UCchan0000000000000000") == 1
        assert crepo.get_channel(s, "UCchan0000000000000000").active is False
