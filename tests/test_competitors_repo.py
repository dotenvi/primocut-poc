"""Tests for the Phase 2 DAO: projects, global channels, M:N links and videos.

Focus on the multi-project invariants: global dedup, orphan deactivation and
data preservation on detach/delete.
"""

from __future__ import annotations

import pytest

from primocut.db import competitors as crepo
from primocut.db.session import Database


def _db(tmp_path) -> Database:
    db = Database(f"sqlite:///{tmp_path / 't.db'}")
    db.init()
    return db


def _channel(s, channel_id="UC1", **kw):
    return crepo.upsert_channel(s, channel_id=channel_id, uploads_playlist_id="UU1", **kw)


def test_create_project_rejects_duplicate_name(tmp_path):
    db = _db(tmp_path)
    with db.session() as s:
        crepo.create_project(s, "Niche")
    with db.session() as s:
        with pytest.raises(crepo.ProjectExistsError):
            crepo.create_project(s, "Niche")


def test_link_is_idempotent(tmp_path):
    db = _db(tmp_path)
    with db.session() as s:
        p = crepo.create_project(s, "P")
        _channel(s)
        assert crepo.link_channel(s, p.id, "UC1") is True
        assert crepo.link_channel(s, p.id, "UC1") is False  # already linked
        assert crepo.count_channel_projects(s, "UC1") == 1


def test_channel_shared_across_projects_deduplicated(tmp_path):
    db = _db(tmp_path)
    with db.session() as s:
        p1 = crepo.create_project(s, "P1")
        p2 = crepo.create_project(s, "P2")
        _channel(s)
        crepo.link_channel(s, p1.id, "UC1")
        crepo.link_channel(s, p2.id, "UC1")
        # One global channel row, linked to both projects.
        assert crepo.count_channel_projects(s, "UC1") == 2
        assert [c.channel_id for c in crepo.list_project_channels(s, p1.id)] == ["UC1"]
        assert [c.channel_id for c in crepo.list_project_channels(s, p2.id)] == ["UC1"]


def test_detach_keeps_data_and_deactivates_orphan(tmp_path):
    db = _db(tmp_path)
    with db.session() as s:
        p = crepo.create_project(s, "P")
        _channel(s)
        crepo.link_channel(s, p.id, "UC1")
        crepo.upsert_video(s, video_id="v1", channel_id="UC1", title="keep me")

    with db.session() as s:
        assert crepo.unlink_channel(s, p.id, "UC1") is True
        assert crepo.deactivate_if_orphan(s, "UC1") is True

    with db.session() as s:
        ch = crepo.get_channel(s, "UC1")
        assert ch is not None and ch.active is False  # deactivated, not deleted
        assert crepo.count_channel_videos(s, "UC1") == 1  # data preserved


def test_detach_from_one_of_two_projects_stays_active(tmp_path):
    db = _db(tmp_path)
    with db.session() as s:
        p1 = crepo.create_project(s, "P1")
        p2 = crepo.create_project(s, "P2")
        _channel(s)
        crepo.link_channel(s, p1.id, "UC1")
        crepo.link_channel(s, p2.id, "UC1")

    with db.session() as s:
        crepo.unlink_channel(s, p1.id, "UC1")
        assert crepo.deactivate_if_orphan(s, "UC1") is False  # still in p2

    with db.session() as s:
        assert crepo.get_channel(s, "UC1").active is True


def test_delete_project_detaches_and_preserves_channel_data(tmp_path):
    db = _db(tmp_path)
    with db.session() as s:
        p = crepo.create_project(s, "P")
        _channel(s)
        crepo.link_channel(s, p.id, "UC1")
        crepo.upsert_video(s, video_id="v1", channel_id="UC1")
        pid = p.id

    with db.session() as s:
        assert crepo.delete_project(s, pid) is True

    with db.session() as s:
        assert crepo.get_project(s, pid) is None
        ch = crepo.get_channel(s, "UC1")
        assert ch is not None and ch.active is False  # orphaned → deactivated
        assert crepo.count_channel_videos(s, "UC1") == 1  # never deleted
        assert crepo.count_channel_projects(s, "UC1") == 0


def test_delete_project_keeps_channel_active_if_in_another_project(tmp_path):
    db = _db(tmp_path)
    with db.session() as s:
        p1 = crepo.create_project(s, "P1")
        p2 = crepo.create_project(s, "P2")
        _channel(s)
        crepo.link_channel(s, p1.id, "UC1")
        crepo.link_channel(s, p2.id, "UC1")
        pid1 = p1.id

    with db.session() as s:
        crepo.delete_project(s, pid1)

    with db.session() as s:
        assert crepo.get_channel(s, "UC1").active is True  # still linked to p2


def test_upsert_video_is_deduplicated_by_id(tmp_path):
    db = _db(tmp_path)
    with db.session() as s:
        _channel(s)
        crepo.upsert_video(s, video_id="v1", channel_id="UC1", title="first")
        crepo.upsert_video(s, video_id="v1", channel_id="UC1", title="updated")
        assert crepo.count_channel_videos(s, "UC1") == 1
        assert crepo.list_channel_videos(s, "UC1")[0].title == "updated"
