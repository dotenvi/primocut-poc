"""DAO for Phase 3 AI packaging: per-item artifacts + the live's audience match.
Pure SQLite; upsert/cache-key contract and the item cascade."""

from __future__ import annotations

from primocut.db import cuts as cuts_repo
from primocut.db import competitors as crepo
from primocut.db import packaging as pkg_repo
from primocut.db import repo
from primocut.db.session import Database
from primocut.models import Topic, Video, VideoStatus


def _db(tmp_path) -> Database:
    db = Database(f"sqlite:///{tmp_path / 't.db'}")
    db.init()
    return db


def _seed_item(db):
    with db.session() as s:
        pid = crepo.create_project(s, "P").id
        repo.upsert_video(s, Video(id="vid1", url="u", title="L", channel="C",
                                   duration=600.0, status=VideoStatus.DONE,
                                   project_id=pid))
        repo.save_topics(s, "vid1", [Topic(title="T", summary="", start=0.0,
                                            end=60.0, score=0.5)])
        tid = repo.get_topic_rows(s, "vid1")[0].id
        cut = cuts_repo.ensure_default_cut(s, "vid1")
        item = cuts_repo.add_item(s, cut, duration=600.0, topic_id=tid)
        return "vid1", cut.id, item.id


def test_upsert_and_read_packaging(tmp_path):
    db = _db(tmp_path)
    _, cut_id, item_id = _seed_item(db)
    with db.session() as s:
        pkg_repo.upsert_packaging(s, cut_item_id=item_id, cut_id=cut_id, kind="title",
                                  payload={"titles": ["a", "b"]}, content_hash="h1")
    with db.session() as s:
        row = pkg_repo.get_packaging(s, item_id, "title")
        assert row.payload["titles"] == ["a", "b"]
        assert row.content_hash == "h1" and row.state == "done"
        by_item = pkg_repo.packaging_by_item(s, cut_id)
        assert by_item[item_id]["title"].payload["titles"] == ["a", "b"]


def test_upsert_is_idempotent_per_item_kind(tmp_path):
    db = _db(tmp_path)
    _, cut_id, item_id = _seed_item(db)
    with db.session() as s:
        pkg_repo.upsert_packaging(s, cut_item_id=item_id, cut_id=cut_id, kind="title",
                                  payload={"titles": ["a"]}, content_hash="h1")
        pkg_repo.upsert_packaging(s, cut_item_id=item_id, cut_id=cut_id, kind="title",
                                  payload={"titles": ["c"]}, content_hash="h2")
    with db.session() as s:
        rows = pkg_repo.list_packaging_for_cut(s, cut_id)
        assert len(rows) == 1  # same (item, kind) upserted in place
        assert rows[0].payload["titles"] == ["c"] and rows[0].content_hash == "h2"


def test_packaging_cascades_with_the_item(tmp_path):
    db = _db(tmp_path)
    vid, cut_id, item_id = _seed_item(db)
    with db.session() as s:
        pkg_repo.upsert_packaging(s, cut_item_id=item_id, cut_id=cut_id, kind="title",
                                  payload={"titles": ["a"]}, content_hash="h1")
    # Removing the item takes its packaging with it (FK cascade).
    with db.session() as s:
        cut = cuts_repo.get_cut(s, vid, cut_id)
        cuts_repo.remove_item(s, cut, item_id)
    with db.session() as s:
        assert pkg_repo.list_packaging_for_cut(s, cut_id) == []


def test_audience_match_upsert(tmp_path):
    db = _db(tmp_path)
    vid, _, _ = _seed_item(db)
    with db.session() as s:
        pkg_repo.upsert_audience_match(s, video_id=vid,
                                       payload={"1": [{"type": "guest", "value": "X"}]},
                                       content_hash="ah1")
    with db.session() as s:
        row = pkg_repo.get_audience_match(s, vid)
        assert row.content_hash == "ah1"
        assert row.payload["1"][0]["value"] == "X"
